"""
kronos_ic_hourly.py

Cheap go/no-go check for whether Kronos (or a matched momentum-style
ranking) shows ANY forecasting skill at hourly bar granularity, before
building a real portfolio backtest or wiring a faster-than-monthly cadence
into autonomous trading (see CLAUDE.md / the 2026-07-24 "trade toggle"
discussion).

Why hourly, not 15/30-min: yfinance caps 15m/30m history at ~58-60 days
(confirmed 2026-07-24) — too short for a meaningful pooled sample or an
honest in/out-of-sample split. Hourly bars go back ~729 days via yfinance,
no live broker connection needed. A broker's own 15-min bars reach ~1 year
per request (2-year request came back empty) — also confirmed 2026-07-24.

Same pretraining-cutoff constraint as kronos_backtest.py applies here too:
the Kronos paper's dataset spans multiple bar frequencies under ONE cutoff
date (training data through June 2024), so — same as the daily test —
checkpoints here never forecast before July 2024, regardless of bar size.

LOOKBACK/PRED_LEN reuse the exact same BAR COUNTS as kronos_backtest.py's
daily test (400 lookback bars, 20-bar-ahead forecast). Kronos operates on
bar counts, not calendar time, so this is genuinely the same code applied
to a different data source: ~2.7-month lookback / ~3-trading-day-ahead
forecast at hourly granularity, vs ~1.6yr lookback / ~1-month-ahead daily.

Reports IC + hit rate for BOTH signals a user might pick for autonomous
trading, so neither gets a pass without this cheap screen:
  - Kronos's hourly forecast (predicted vs realized 20-bar return)
  - A matched momentum-style ranking (trailing 400-bar return, identical
    lookback/horizon, so the comparison is apples-to-apples)

Usage:
  python3 kronos_ic_hourly.py                                    ~24 checkpoints, sample_count=10
  python3 kronos_ic_hourly.py --n-checkpoints 12 --sample-count 5  faster, noisier
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
import trader_app as ta

from kronos_agent import get_predictor, DEFAULT_SAMPLE_COUNT
from kronos_backtest import spearman, CUTOFF_TEST_START

LOOKBACK = 400   # bars — same count as the daily test, different data (~2.7 trading months hourly)
PRED_LEN = 20    # bars — same count as the daily test (~3 trading days hourly)
FETCH_PERIOD = "729d"  # yfinance's practical max for 1h bars

HOURLY_CACHE_DIR = Path(__file__).parent / "price_data_hourly"
HOURLY_CACHE_DIR.mkdir(exist_ok=True)


def fetch_hourly(ticker: str, force: bool = False, cache_dir: Path = None) -> pd.DataFrame:
    """Cached hourly OHLCV, always stored/returned as tz-naive local exchange
    time (stripped immediately after download, before writing the cache, so
    a re-read never has to guess how to parse a tz-aware string column).

    `cache_dir` defaults to this backtest's own HOURLY_CACHE_DIR — pass a
    different dir for any live/frequently-refreshed caller (e.g.
    autotrade_signals.py) so a force=True live refresh never overwrites
    this backtest's cache out from under it (the exact bug documented in
    the live-data cache convention, just for hourly bars)."""
    cache_dir = cache_dir or HOURLY_CACHE_DIR
    cache_path = cache_dir / f"{ticker}.csv"
    if cache_path.exists() and not force:
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
    else:
        raw = yf.download(ticker, period=FETCH_PERIOD, interval="1h", progress=False, auto_adjust=True)
        if raw.empty:
            raise RuntimeError(f"No hourly data returned for {ticker}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw.index = raw.index.tz_localize(None)
        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.to_csv(cache_path)
    return df.dropna()


def _nearest_pos(idx: pd.DatetimeIndex, date: pd.Timestamp) -> int:
    return idx.searchsorted(date, side="right") - 1


def kronos_forecast_at(predictor, hist_data: dict, tickers: list, checkpoint: pd.Timestamp,
                       pred_len: int, sample_count: int):
    """Same walk-forward contract as kronos_backtest.kronos_forecast_at, on hourly bars."""
    df_list, x_ts_list, y_ts_list, ok_tickers, last_closes, realized = [], [], [], [], {}, {}
    for t in tickers:
        df = hist_data[t]
        idx = df.index
        pos = _nearest_pos(idx, checkpoint)
        if pos < LOOKBACK - 1:
            continue
        window = df.iloc[pos - LOOKBACK + 1: pos + 1]
        kdf = window.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        x_ts_list.append(pd.Series(kdf.index))
        # hourly step, not calendar days, for the future timestamps Kronos conditions on
        step = kdf.index[-1] - kdf.index[-2] if len(kdf) > 1 else pd.Timedelta(hours=1)
        y_ts_list.append(pd.Series([kdf.index[-1] + step * (i + 1) for i in range(pred_len)]))
        df_list.append(kdf.reset_index(drop=True))
        ok_tickers.append(t)
        last_closes[t] = df["Close"].iloc[pos]
        if pos + pred_len < len(idx):
            realized[t] = df["Close"].iloc[pos + pred_len] / last_closes[t] - 1
        else:
            realized[t] = None

    if not df_list:
        return {}

    pred_df_list = predictor.predict_batch(
        df_list=df_list, x_timestamp_list=x_ts_list, y_timestamp_list=y_ts_list,
        pred_len=pred_len, T=1.0, top_p=0.9, sample_count=sample_count, verbose=False,
    )
    out = {}
    for t, pred_df in zip(ok_tickers, pred_df_list):
        predicted_chg = pred_df["close"].iloc[-1] / last_closes[t] - 1
        out[t] = (predicted_chg, realized[t])
    return out


def momentum_at(hist_data: dict, tickers: list, checkpoint: pd.Timestamp, lookback: int, pred_len: int):
    """Matched-horizon momentum baseline: trailing `lookback`-bar return as
    the ranking signal, scored against the identical realized `pred_len`-bar
    forward return Kronos is scored against."""
    out = {}
    for t in tickers:
        df = hist_data[t]
        idx = df.index
        pos = _nearest_pos(idx, checkpoint)
        if pos < lookback - 1 or pos + pred_len >= len(idx):
            continue
        trailing_ret = df["Close"].iloc[pos] / df["Close"].iloc[pos - lookback + 1] - 1
        realized_ret = df["Close"].iloc[pos + pred_len] / df["Close"].iloc[pos] - 1
        out[t] = (trailing_ret, realized_ret)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    ap.add_argument("--n-checkpoints", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    settings = ta.load_settings()
    tickers = settings["tickers"]

    print(f"Fetching hourly history ({FETCH_PERIOD}) for {len(tickers)} tickers...")
    hist_data = {}
    for t in tickers:
        try:
            hist_data[t] = fetch_hourly(t)
        except Exception as e:
            print(f"  {t}: failed to fetch ({e})", file=sys.stderr)
    tickers = [t for t in tickers if t in hist_data]

    # earliest position, across all tickers, that both has LOOKBACK bars of
    # history behind it AND sits at/after the pretraining-cutoff test start
    eligible_starts = []
    for t in tickers:
        idx = hist_data[t].index
        pos = max(LOOKBACK - 1, _nearest_pos(idx, CUTOFF_TEST_START))
        eligible_starts.append(idx[pos])
    start_at = max(eligible_starts)

    all_candidates = [d for d in hist_data[tickers[0]].index if d >= start_at]
    # need PRED_LEN bars of realized future left, per ticker
    max_pos = min(len(hist_data[t].index) for t in tickers) - PRED_LEN - 1
    all_candidates = [d for d in all_candidates if _nearest_pos(hist_data[tickers[0]].index, d) <= max_pos]

    if len(all_candidates) < args.n_checkpoints:
        checkpoints = all_candidates
    else:
        step = len(all_candidates) // args.n_checkpoints
        checkpoints = all_candidates[::step][:args.n_checkpoints]

    print(f"Usable range: {checkpoints[0]} -> {checkpoints[-1]} "
          f"({len(checkpoints)} checkpoints, evenly spaced)")

    print("Loading Kronos predictor (first run downloads from Hugging Face)...")
    predictor = get_predictor()
    print(f"Predictor ready on device: {predictor.device}")

    kronos_pairs, momentum_pairs = [], []
    for i, cp in enumerate(checkpoints):
        print(f"Checkpoint {cp} ({i + 1}/{len(checkpoints)})...")
        kresult = kronos_forecast_at(predictor, hist_data, tickers, cp, PRED_LEN, args.sample_count)
        for t, (predicted_chg, realized_chg) in kresult.items():
            if realized_chg is not None:
                kronos_pairs.append((predicted_chg, realized_chg))

        mresult = momentum_at(hist_data, tickers, cp, LOOKBACK, PRED_LEN)
        for t, (predicted_chg, realized_chg) in mresult.items():
            momentum_pairs.append((predicted_chg, realized_chg))

    def report(name, pairs):
        if len(pairs) < 10:
            print(f"\n{name}: not enough pairs ({len(pairs)}) to report.")
            return
        df = pd.DataFrame(pairs, columns=["predicted", "realized"])
        ic = spearman(df["predicted"], df["realized"])
        hit = (np.sign(df["predicted"]) == np.sign(df["realized"])).mean()
        print(f"\n=== {name} ===")
        print(f"Pooled pairs: {len(df)}")
        print(f"Spearman IC (predicted vs realized {PRED_LEN}-bar/~hourly return): {ic:.3f}")
        print(f"Directional hit rate: {hit * 100:.1f}%")

    report("Kronos (hourly)", kronos_pairs)
    report("Momentum-style baseline (hourly, matched horizon)", momentum_pairs)
    print("\n(IC near 0 / hit rate near 50% = no forecasting skill detected at this cadence.)")


if __name__ == "__main__":
    main()
