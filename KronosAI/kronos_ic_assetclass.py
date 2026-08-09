"""
kronos_ic_assetclass.py — the per-asset-class IC screen rule 9 gates FTMO on.

CLAUDE.md rule 9: "Kronos may only trade an asset class that has passed its own
IC screen ... do not enable a class because it is configured, only because it
screened." The FTMO config lists four classes — stock CFDs, indices, FX,
commodities/crypto — and as of 2026-08-03 exactly one had ever been screened
(stocks: Spearman IC 0.036, hit rate 50.0%, i.e. nothing). This screens the
rest, and needs no cTrader connection, so it is not blocked on the Open API
app activating.

METHODOLOGY — deliberately identical to kronos_backtest.py's daily test, so
the numbers sit on the same scale as the existing stock evidence rather than
being a lookalike that cannot be compared:
  - daily bars (FTMO cadence is a daily rebalance with multi-day holds)
  - LOOKBACK 400 bars, PRED_LEN 20 bars — the same COUNTS as the daily test
  - checkpoints at/after CUTOFF_TEST_START (2024-07-01), because the Kronos
    paper's training data ends June 2024. Forecasting before that is scoring
    the model on its own training set.
  - a matched momentum baseline (trailing 400-bar return, identical horizon)
    per class, so Kronos never gets a pass without the cheap comparison it
    already lost at both daily and hourly cadence on stocks.

INFERENCE — pooled IC is reported for comparability with the existing project
numbers, but it is NOT what to judge a class on. Pooled date x ticker pairs
are heavily correlated: every ticker on one date shares that day's market
move, so a pooled n of 240 is nothing like 240 independent observations, and
its apparent significance is inflated. The honest statistic is the
CROSS-SECTIONAL IC computed per date, then averaged across dates with a
t-stat over the date series — dates are far closer to independent draws.
Both are printed. If they disagree, believe the date series.

Same discipline as grade_calls.py: a number is reported with what it would
have been by chance, never alone.

Usage:
  python3 kronos_ic_assetclass.py                      # all classes
  python3 kronos_ic_assetclass.py --classes fx,indices
  python3 kronos_ic_assetclass.py --n-checkpoints 6 --sample-count 3   # smoke

Long-running and GPU-bound — launch it through ./run_notify.sh (see the
notify-on-long-runs skill), do not call it bare.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from kronos_agent import get_predictor, DEFAULT_SAMPLE_COUNT      # noqa: E402
from kronos_backtest import spearman, CUTOFF_TEST_START           # noqa: E402

LOOKBACK = 400        # daily bars — same count as kronos_backtest.py
PRED_LEN = 20         # daily bars ahead — same count as kronos_backtest.py
FETCH_START = "2022-01-01"   # far enough back for 400 bars before the cutoff

CACHE_DIR = Path(__file__).parent / "price_data_assetclass"
CACHE_DIR.mkdir(exist_ok=True)

# Proxies for what FTMO actually lists. yfinance symbols, not venue symbols —
# the screen is about whether the SIGNAL has skill on the asset class, which is
# a property of the underlying, not of the CFD wrapper.
UNIVERSES = {
    "indices": ["^GSPC", "^NDX", "^DJI", "^RUT", "^GDAXI",
                "^FTSE", "^FCHI", "^N225", "^HSI", "^AXJO"],
    # CME FX futures, NOT spot pairs (EURUSD=X etc). yfinance reports volume
    # identically ZERO for every spot FX pair — verified 2026-08-03 across all
    # ten — and Kronos conditions on volume, so a spot-FX screen would be
    # scoring the model on a dead input and any result would be an artifact.
    # The futures track spot closely and carry real exchange volume, which
    # makes them the honest proxy for "does this signal work on FX at all".
    # FTMO lists spot FX CFDs, so read a pass here as "worth a real test on
    # venue data", not as a licence to trade spot directly.
    "fx": ["6E=F", "6B=F", "6J=F", "6A=F", "6C=F", "6S=F", "6N=F", "6M=F"],
    "commodities": ["GC=F", "SI=F", "HG=F", "PL=F", "CL=F",
                    "BZ=F", "NG=F", "ZC=F", "ZS=F", "ZW=F"],
    # Split out from commodities on purpose: the FTMO config lumps them, but
    # they are not one thing, and lumping would let a result in one hide the
    # other.
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD",
               "DOGE-USD", "LTC-USD", "BCH-USD", "LINK-USD", "AVAX-USD"],
}


def fetch_daily(ticker: str, force: bool = False) -> pd.DataFrame:
    """Cached daily OHLCV, tz stripped before caching so a re-read never has to
    guess how to parse a tz-aware index (same reasoning as fetch_hourly)."""
    cache_path = CACHE_DIR / f"{ticker.replace('/', '_')}.csv"
    if cache_path.exists() and not force:
        return pd.read_csv(cache_path, index_col=0, parse_dates=True).dropna()
    raw = yf.download(ticker, start=FETCH_START, interval="1d",
                      progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"no daily data returned for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if getattr(raw.index, "tz", None) is not None:
        raw.index = raw.index.tz_localize(None)
    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.to_csv(cache_path)
    return df.dropna()


def _nearest_pos(idx: pd.DatetimeIndex, date: pd.Timestamp) -> int:
    return idx.searchsorted(date, side="right") - 1


def kronos_forecast_at(predictor, hist: dict, tickers: list,
                       checkpoint: pd.Timestamp, sample_count: int) -> dict:
    """Walk-forward: forecast PRED_LEN bars ahead from `checkpoint` using only
    the LOOKBACK bars up to it. Same contract as kronos_backtest's version."""
    df_list, x_ts, y_ts, ok, last_close, realized = [], [], [], [], {}, {}
    for t in tickers:
        df = hist[t]
        idx = df.index
        pos = _nearest_pos(idx, checkpoint)
        if pos < LOOKBACK - 1:
            continue
        window = df.iloc[pos - LOOKBACK + 1: pos + 1]
        kdf = window.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        x_ts.append(pd.Series(kdf.index))
        step = kdf.index[-1] - kdf.index[-2] if len(kdf) > 1 else pd.Timedelta(days=1)
        y_ts.append(pd.Series([kdf.index[-1] + step * (i + 1) for i in range(PRED_LEN)]))
        df_list.append(kdf.reset_index(drop=True))
        ok.append(t)
        last_close[t] = df["Close"].iloc[pos]
        realized[t] = (df["Close"].iloc[pos + PRED_LEN] / last_close[t] - 1
                       if pos + PRED_LEN < len(idx) else None)

    if not df_list:
        return {}
    preds = predictor.predict_batch(
        df_list=df_list, x_timestamp_list=x_ts, y_timestamp_list=y_ts,
        pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=sample_count, verbose=False)
    return {t: (p["close"].iloc[-1] / last_close[t] - 1, realized[t])
            for t, p in zip(ok, preds)}


def momentum_at(hist: dict, tickers: list, checkpoint: pd.Timestamp) -> dict:
    """Matched-horizon baseline: trailing LOOKBACK-bar return, scored against
    the identical realized forward return Kronos is scored against."""
    out = {}
    for t in tickers:
        df = hist[t]
        idx = df.index
        pos = _nearest_pos(idx, checkpoint)
        if pos < LOOKBACK - 1 or pos + PRED_LEN >= len(idx):
            continue
        out[t] = (df["Close"].iloc[pos] / df["Close"].iloc[pos - LOOKBACK + 1] - 1,
                  df["Close"].iloc[pos + PRED_LEN] / df["Close"].iloc[pos] - 1)
    return out


def t_stat(xs: list) -> tuple[float, float]:
    """(mean, t) of a series of per-date ICs. Two-sided significance at the
    usual 5% bar is |t| > ~2 for these sample sizes."""
    a = np.asarray([x for x in xs if x == x], dtype=float)
    if len(a) < 3 or a.std(ddof=1) == 0:
        return (float(a.mean()) if len(a) else float("nan"), float("nan"))
    return float(a.mean()), float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a))))


def report(name: str, pooled: list, per_date: list) -> dict:
    """pooled: (predicted, realized) pairs. per_date: one cross-sectional IC
    per checkpoint."""
    print(f"\n--- {name} ---")
    if len(pooled) < 10:
        print(f"  not enough pairs ({len(pooled)}) to report")
        return {}
    df = pd.DataFrame(pooled, columns=["predicted", "realized"])
    ic_pooled = spearman(df["predicted"], df["realized"])
    hit = float((np.sign(df["predicted"]) == np.sign(df["realized"])).mean())
    mean_ic, t = t_stat(per_date)
    n_dates = len([x for x in per_date if x == x])

    print(f"  pooled pairs        {len(df)}  (across {n_dates} dates — NOT independent)")
    print(f"  pooled Spearman IC  {ic_pooled:+.3f}")
    print(f"  directional hit     {hit * 100:.1f}%   (chance 50%)")
    print(f"  mean date-wise IC   {mean_ic:+.3f}   t={t:+.2f} over {n_dates} dates")
    # nan fails BOTH `> 2` and `<= 2`, so an unguarded chain silently fell
    # through to whatever the last branch was — the smoke test printed
    # "SIGNIFICANTLY NEGATIVE" off a 2-date run with a positive IC. A verdict
    # function must never default; too few dates is its own answer.
    if n_dates < 3 or t != t:
        verdict = f"INSUFFICIENT DATES ({n_dates}) — not screened"
    elif abs(t) <= 2:
        verdict = "no skill detected"
    elif mean_ic > 0:
        verdict = "SKILL DETECTED"
    else:
        verdict = "SIGNIFICANTLY NEGATIVE"
    print(f"  -> {verdict}")
    return {"pooled_ic": ic_pooled, "hit": hit, "mean_ic": mean_ic,
            "t": t, "n_dates": n_dates, "n_pairs": len(df), "verdict": verdict}


def screen(cls: str, tickers: list, predictor, args) -> dict:
    print(f"\n{'=' * 62}\nASSET CLASS: {cls}  ({len(tickers)} instruments)\n{'=' * 62}")
    hist = {}
    for t in tickers:
        try:
            hist[t] = fetch_daily(t, force=args.refresh)
        except Exception as e:  # noqa: BLE001 - one bad symbol must not kill the class
            print(f"  {t}: skipped ({e})", file=sys.stderr)
    tickers = [t for t in tickers if t in hist]
    if len(tickers) < 4:
        print(f"  only {len(tickers)} instruments fetched — too few for a "
              f"cross-sectional IC. Class not screened.")
        return {}

    # Kronos conditions on volume. yfinance reports 0 volume for FX and some
    # index symbols; that is a data limitation, not a model result, and a class
    # that hits it cannot be honestly failed on the number below.
    dead_vol = [t for t in tickers if float(hist[t]["Volume"].abs().sum()) == 0.0]
    if dead_vol:
        print(f"  !! volume is identically zero for {len(dead_vol)}/{len(tickers)}: "
              f"{', '.join(dead_vol[:6])}{'...' if len(dead_vol) > 6 else ''}")
        print("     Kronos conditions on volume — treat this class's result as "
              "UNRELIABLE, not as a negative finding.")

    starts = [hist[t].index[max(LOOKBACK - 1, _nearest_pos(hist[t].index, CUTOFF_TEST_START))]
              for t in tickers]
    start_at = max(starts)
    ref = hist[tickers[0]].index
    max_pos = min(len(hist[t].index) for t in tickers) - PRED_LEN - 1
    cands = [d for d in ref if d >= start_at and _nearest_pos(ref, d) <= max_pos]
    if not cands:
        print("  no usable checkpoints after the pretraining cutoff. Class not screened.")
        return {}
    step = max(1, len(cands) // args.n_checkpoints)
    checkpoints = cands[::step][:args.n_checkpoints]
    print(f"  {len(checkpoints)} checkpoints: {checkpoints[0].date()} -> {checkpoints[-1].date()}")

    k_pooled, m_pooled, k_dates, m_dates = [], [], [], []
    for i, cp in enumerate(checkpoints):
        print(f"    [{i + 1}/{len(checkpoints)}] {cp.date()}", flush=True)
        kr = kronos_forecast_at(predictor, hist, tickers, cp, args.sample_count)
        pairs = [(p, r) for p, r in kr.values() if r is not None]
        if len(pairs) >= 4:
            k_pooled += pairs
            d = pd.DataFrame(pairs, columns=["p", "r"])
            k_dates.append(spearman(d["p"], d["r"]))

        mo = momentum_at(hist, tickers, cp)
        mpairs = list(mo.values())
        if len(mpairs) >= 4:
            m_pooled += mpairs
            d = pd.DataFrame(mpairs, columns=["p", "r"])
            m_dates.append(spearman(d["p"], d["r"]))

    res = {"kronos": report(f"{cls}: Kronos", k_pooled, k_dates),
           "momentum": report(f"{cls}: momentum baseline (matched horizon)",
                              m_pooled, m_dates),
           "unreliable": bool(dead_vol)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default=",".join(UNIVERSES),
                    help=f"comma-separated: {','.join(UNIVERSES)}")
    ap.add_argument("--pred-len", type=int, default=PRED_LEN,
                    help="daily bars ahead. Default 20 — the horizon EVERY "
                         "existing screen in this project was measured at. "
                         "Pass 5 to screen the shortened horizon; the two are "
                         "not comparable to each other's published numbers.")
    ap.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    ap.add_argument("--n-checkpoints", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--refresh", action="store_true", help="refetch price data")
    args = ap.parse_args()

    # Rebind the module global rather than threading pred_len through six
    # functions. The default is unchanged, so an argument-free run still
    # reproduces the 2026-08-03 numbers exactly; only an explicit --pred-len
    # moves it, and the header below prints whichever was used so an output
    # can never be mistaken for the other horizon's.
    globals()["PRED_LEN"] = args.pred_len

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    unknown = [c for c in classes if c not in UNIVERSES]
    if unknown:
        sys.exit(f"unknown class(es): {unknown}. Known: {list(UNIVERSES)}")

    print("Loading Kronos predictor (first run downloads from Hugging Face)...")
    predictor = get_predictor()
    print(f"Predictor ready on device: {predictor.device}")
    print(f"seed={args.seed}  sample_count={args.sample_count}  "
          f"LOOKBACK={LOOKBACK}  PRED_LEN={PRED_LEN}")

    results = {c: screen(c, UNIVERSES[c], predictor, args) for c in classes}

    print(f"\n{'=' * 62}\nSUMMARY — rule 9 gate\n{'=' * 62}")
    print(f"{'class':<14}{'Kronos IC':>11}{'t':>7}{'hit':>8}{'momo IC':>10}  verdict")
    for c, r in results.items():
        k, m = r.get("kronos") or {}, r.get("momentum") or {}
        if not k:
            print(f"{c:<14}{'not screened':>36}")
            continue
        flag = "  [UNRELIABLE: zero volume]" if r.get("unreliable") else ""
        print(f"{c:<14}{k['mean_ic']:>+11.3f}{k['t']:>+7.2f}{k['hit'] * 100:>7.1f}%"
              f"{m.get('mean_ic', float('nan')):>+10.3f}  {k['verdict']}{flag}")
    print("\nRule 9: a class may be enabled only if it PASSED its own screen.")
    print("'no skill detected' is a fail, not a maybe. Do not enable on a")
    print("configured-but-unscreened class, and do not enable on UNRELIABLE.")


if __name__ == "__main__":
    main()
