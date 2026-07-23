"""
kronos_backtest.py

Honest walk-forward evaluation of Kronos as a monthly-rotation signal,
mirroring the rigor momentum rotation got before it earned Phase 3
(CLAUDE.md rule 4: in/out-of-sample, after costs, vs buy-and-hold —
here "vs buy-and-hold AND vs momentum rotation", the current baseline).

Window is bounded by Kronos's own pretraining cutoff, not chosen for
convenience: per the paper (arXiv:2508.02739), "the pre-training data for
Kronos extends up to June 2024" and "our test period for all tasks begins
in July 2024 to ensure a strict temporal separation between training and
evaluation." Evaluating on anything before July 2024 risks scoring
memorization, not forecasting skill — so this script never rebalances
before that date, and lookback (input) context predating June 2024 is
fine (that's just real historical price data, same as any live run would
condition on), but the FORECASTED window is always kept post-cutoff.

Two-stage evaluation, cheapest check first:
  1. Information coefficient (IC): pooled Spearman rank correlation between
     Kronos's predicted N-trading-day return and the REALIZED N-day return,
     across every (rebalance date, ticker) pair, plus directional hit rate.
     This is the go/no-go signal — near-zero IC means the portfolio backtest
     below is guaranteed to disappoint, and this check is far cheaper to
     interpret (many data points vs one equity curve).
  2. Portfolio backtest: Kronos-ranked top-N monthly rotation vs momentum's
     own ranking vs SPY buy-and-hold, all on the IDENTICAL rebalance dates
     and via the exact same simulate_rotation() engine trader_app.py's
     momentum_backtest uses — same cost/turnover mechanics, so the
     comparison is apples-to-apples.

Caveats (read before trusting the numbers):
  - Single sampling draw. Kronos samples stochastically (T=1.0, top_p=0.9);
    one run of this script is one draw from that distribution, not a fixed
    number. RNG is seeded for reproducibility of THIS run, but a different
    seed will shift the result somewhat (see KronosAI/KronosVault's
    Integration Log for the sample_count=1 vs 10 vs 30 stabilization data —
    this script uses sample_count=10 for that reason). Re-run with a
    different seed before trusting a borderline result.
  - Short window by construction (~23 months, July 2024 -> now) because
    that's the entire honest post-cutoff period available — this is not
    the multi-year rigor momentum rotation's original backtest had.
  - yfinance's auto_adjust=True retroactively adjusts historical closes for
    splits/dividends based on the full series fetched today — a pre-existing
    characteristic of every backtest in this project (sma_crossover_backtest.py,
    momentum_backtest), not something specific to Kronos.

Usage:
  python3 kronos_backtest.py                  full run, sample_count=10
  python3 kronos_backtest.py --sample-count 5  faster, noisier
  python3 kronos_backtest.py --seed 7          different draw
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).parent.parent))
import trader_app as ta

from kronos_agent import get_predictor, LOOKBACK, PRED_LEN, DEFAULT_SAMPLE_COUNT

CUTOFF_TEST_START = pd.Timestamp("2024-07-01")  # per Kronos paper: test period begins July 2024
FETCH_START = "2022-06-01"                       # far enough back for LOOKBACK=400 bars before the cutoff
CHECKPOINT_PATH = Path(__file__).parent / "kronos_backtest_checkpoint.json"


def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation via Pearson-on-ranks — avoids a scipy
    dependency (pandas' own .corr(method='spearman') silently requires
    scipy internally, which cost a full run's worth of compute to discover)."""
    return a.rank().corr(b.rank())


def _nearest_pos(idx: pd.DatetimeIndex, date: pd.Timestamp) -> int:
    return idx.searchsorted(date, side="right") - 1


def kronos_forecast_at(predictor, hist_data: dict, tickers: list, rebalance_date: pd.Timestamp,
                       pred_len: int, sample_count: int):
    """Walk-forward forecast for one rebalance date: for each ticker, use
    only bars up to (and including) rebalance_date as context. Returns
    {ticker: (predicted_chg, realized_chg_or_None)}. realized_chg is None
    if fewer than pred_len trading days of future data exist yet for that
    ticker (too recent to score)."""
    df_list, x_ts_list, y_ts_list, ok_tickers, last_closes, realized = [], [], [], [], {}, {}
    for t in tickers:
        df = hist_data[t]
        idx = df.index
        pos = _nearest_pos(idx, rebalance_date)
        if pos < LOOKBACK - 1:
            continue  # not enough history yet
        window = df.iloc[pos - LOOKBACK + 1: pos + 1]
        kdf = window.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        x_ts_list.append(pd.Series(kdf.index))
        y_ts_list.append(pd.Series(pd.bdate_range(kdf.index[-1] + pd.Timedelta(days=1), periods=pred_len)))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--from-checkpoint", action="store_true",
                    help=f"Skip the (expensive) forecasting loop entirely and re-run just the "
                         f"IC/portfolio reporting from {CHECKPOINT_PATH.name} (saved after the "
                         f"loop completes). Use this to recover from a crash in the reporting "
                         f"stage without re-forecasting.")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    settings = ta.load_settings()
    tickers = settings["tickers"]
    bench = settings["benchmark"]
    top_n = settings.get("momentum_top_n", 3)
    mom_lookback = settings.get("momentum_lookback_m", 12)
    dual = settings.get("risk_engine", False)
    cost = settings["commission_pct"] / 100
    today = pd.Timestamp.today().strftime("%Y-%m-%d")

    print(f"Fetching full history ({FETCH_START} -> {today}) for {len(tickers)} tickers + {bench}...")
    hist_data = {}
    for t in tickers + [bench]:
        try:
            hist_data[t] = ta.fetch(t, FETCH_START, today, force=False)
        except Exception as e:
            print(f"  {t}: failed to fetch ({e})", file=sys.stderr)
    tickers = [t for t in tickers if t in hist_data]

    closes = pd.DataFrame({t: hist_data[t]["Close"] for t in tickers}).dropna(how="all")
    monthly = closes.resample("ME").last()
    mom = monthly.pct_change(mom_lookback)
    daily_ret = closes.pct_change()

    # rebalance dates: month-ends >= the post-cutoff test start, excluding the
    # final month (no next month-end left for simulate_rotation to hold through)
    candidate_dates = [d for d in monthly.index[:-1] if d >= CUTOFF_TEST_START]

    if args.from_checkpoint:
        if not CHECKPOINT_PATH.exists():
            print(f"No checkpoint at {CHECKPOINT_PATH} — run without --from-checkpoint first.")
            return
        saved = json.loads(CHECKPOINT_PATH.read_text())
        kronos_tops = {pd.Timestamp(k): v for k, v in saved["kronos_tops"].items()}
        ic_pairs = saved["ic_pairs"]
        usable_dates = [pd.Timestamp(d) for d in saved["usable_dates"]]
        print(f"Loaded checkpoint from {CHECKPOINT_PATH} "
              f"(sample_count={saved['sample_count']}, seed={saved['seed']}, "
              f"{len(usable_dates)} dates) — skipping the forecasting loop.")
    else:
        print("Loading Kronos predictor (first run downloads from Hugging Face)...")
        predictor = get_predictor()
        print(f"Predictor ready on device: {predictor.device}")

        kronos_tops, ic_pairs = {}, []
        usable_dates = []
        for d in candidate_dates:
            print(f"Forecasting at {d.date()} ({len(usable_dates) + 1}/{len(candidate_dates)})...")
            result = kronos_forecast_at(predictor, hist_data, tickers, d, PRED_LEN, args.sample_count)
            if not result:
                continue
            chg = {t: v[0] for t, v in result.items()}
            ranked = pd.Series(chg).sort_values(ascending=False)
            top = list(ranked.index[:top_n])
            if dual:
                top = [t for t in top if ranked[t] > 0]
            kronos_tops[d] = top
            usable_dates.append(d)
            for t, (predicted_chg, realized_chg) in result.items():
                if realized_chg is not None:
                    ic_pairs.append((predicted_chg, realized_chg))

        # Checkpoint immediately — the forecasting loop is the expensive part
        # (~1 min/date); everything after this point is cheap pandas math that
        # can be safely re-run from disk if it errors (see --from-checkpoint).
        CHECKPOINT_PATH.write_text(json.dumps({
            "kronos_tops": {d.isoformat(): v for d, v in kronos_tops.items()},
            "ic_pairs": ic_pairs,
            "usable_dates": [d.isoformat() for d in usable_dates],
            "sample_count": args.sample_count,
            "seed": args.seed,
        }))
        print(f"Checkpoint saved to {CHECKPOINT_PATH}")

    if len(usable_dates) < 2:
        print("Not enough usable rebalance dates to backtest — exiting.")
        return

    # --- Stage 1: information coefficient ---
    ic_df = pd.DataFrame(ic_pairs, columns=["predicted", "realized"])
    spearman_ic = spearman(ic_df["predicted"], ic_df["realized"])
    hit_rate = (np.sign(ic_df["predicted"]) == np.sign(ic_df["realized"])).mean()
    print(f"\n=== Stage 1: Information Coefficient ===")
    print(f"Pooled pairs: {len(ic_df)} across {len(usable_dates)} dates x {len(tickers)} tickers")
    print(f"Spearman IC (predicted vs realized {PRED_LEN}-day return): {spearman_ic:.3f}")
    print(f"Directional hit rate: {hit_rate * 100:.1f}%")
    print("(IC near 0 / hit rate near 50% = no forecasting skill detected in this window.)")

    # --- Stage 2: portfolio backtest, Kronos vs momentum vs SPY, identical dates ---
    # +1: candidate_dates excluded the final month-end so every usable_dates[i]
    # has a next month-end to hold through; add it back as the sim's closing date.
    next_loc = monthly.index.get_loc(usable_dates[-1]) + 1
    month_ends = usable_dates + [monthly.index[next_loc]]
    if len(month_ends) < 2:
        print("\nNot enough dates left for a portfolio simulation.")
        return

    momentum_tops = {}
    for d in month_ends[:-1]:
        ranked = mom.loc[d].dropna().sort_values(ascending=False)
        top = list(ranked.index[:top_n])
        if dual:
            top = [t for t in top if ranked[t] > 0]
        momentum_tops[d] = top

    kronos_curve = ta.simulate_rotation(daily_ret, month_ends, kronos_tops, cost, top_n)
    momentum_curve = ta.simulate_rotation(daily_ret, month_ends, momentum_tops, cost, top_n)
    spy_slice = hist_data[bench].loc[month_ends[0]:month_ends[-1]]
    spy_stats = ta.buy_and_hold_stats(spy_slice)

    def stats(curve):
        yrs = (curve.index[-1] - curve.index[0]).days / 365.25
        cagr = ((curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1) * 100
        dd = ((curve - curve.cummax()) / curve.cummax()).min() * 100
        mret = curve.pct_change().dropna()
        sharpe = mret.mean() / mret.std() * np.sqrt(12) if mret.std() > 0 else float("nan")
        return cagr, dd, sharpe

    k_cagr, k_dd, k_sharpe = stats(kronos_curve)
    m_cagr, m_dd, m_sharpe = stats(momentum_curve)

    print(f"\n=== Stage 2: Portfolio backtest [{month_ends[0].date()} -> {month_ends[-1].date()}] ===")
    print(f"(sample_count={args.sample_count}, seed={args.seed}, top-{top_n}, {PRED_LEN}-day forecast horizon)")
    print(f"{'Strategy':<20}{'CAGR':>10}{'Max DD':>10}{'Sharpe':>10}")
    print(f"{'Kronos rotation':<20}{k_cagr:>9.2f}%{k_dd:>9.2f}%{k_sharpe:>10.2f}")
    print(f"{'Momentum rotation':<20}{m_cagr:>9.2f}%{m_dd:>9.2f}%{m_sharpe:>10.2f}")
    print(f"{bench + ' buy&hold':<20}{spy_stats['cagr_pct']:>9.2f}%{spy_stats['max_dd_pct']:>9.2f}%{spy_stats['sharpe']:>10.2f}")


if __name__ == "__main__":
    main()
