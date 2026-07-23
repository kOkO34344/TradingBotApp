"""
autotrade_signals.py

Live (not backtest) hourly signal computation for autotrade_runner.py.
Both functions return the (top, data, ranked) shape paper_trader's
execute_rebalance() expects, so the exact same sizing/execution path
works regardless of which signal produced the ranking — same pattern as
kronos_agent.forecast_signal / paper_trader.compute_signal.

IMPORTANT CONTEXT — read before trusting either signal's picks: both were
screened at this exact cadence in KronosAI/kronos_ic_hourly.py (2026-07-24)
and showed no measurable edge — momentum-hourly IC -0.037 / 48.5% hit
rate, Kronos-hourly IC -0.081 / 46.4% hit rate (336 pooled pairs, both
indistinguishable from noise). This module exists because the owner chose
to run it live anyway as a deliberate paper-only experiment, not because
either signal is validated. See CLAUDE.md's empirical findings.

`data[t]` returned here is HOURLY OHLCV (capitalized columns, matching
trader_app.fetch's convention) — NOT daily. ind.atr(data[t]) on this
computes a 14-HOUR ATR (appropriate for a position meant to be held on an
hourly cadence), not the 14-DAY ATR the monthly signals use.

Own cache dir (price_data_hourly_live/, force=True every call) — kept
separate from kronos_ic_hourly.py's price_data_hourly/ backtest cache for
the same reason paper_trader.py keeps price_data_live/ separate from
trader_app's price_data/: a force-refetch into a shared cache silently
truncates/overwrites whatever long-history cache another script relies on.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "KronosAI"))
from kronos_ic_hourly import fetch_hourly, kronos_forecast_at, LOOKBACK, PRED_LEN
from kronos_agent import get_predictor, DEFAULT_SAMPLE_COUNT

LIVE_HOURLY_CACHE_DIR = Path(__file__).parent / "KronosAI" / "price_data_hourly_live"
LIVE_HOURLY_CACHE_DIR.mkdir(exist_ok=True)


def _fetch_all(tickers: list) -> dict:
    data = {}
    for t in tickers:
        try:
            data[t] = fetch_hourly(t, force=True, cache_dir=LIVE_HOURLY_CACHE_DIR)
        except Exception as e:
            print(f"WARNING: could not fetch hourly {t}: {e}", file=sys.stderr)
    return data


def compute_live_momentum_hourly(settings: dict):
    """Trailing LOOKBACK-bar (hourly) return, ranked — the same
    lookback/horizon momentum-style baseline kronos_ic_hourly.py screened
    (IC -0.037, 48.5% hit rate)."""
    top_n = settings.get("momentum_top_n", 3)
    dual = settings.get("risk_engine", False)
    tickers = settings["tickers"]

    data = _fetch_all(tickers)
    tickers = [t for t in tickers if t in data]
    if len(tickers) < top_n + 1:
        raise RuntimeError(f"Only {len(tickers)}/{len(settings['tickers'])} tickers "
                            f"fetched — need at least {top_n + 1}.")

    now = min(data[t].index[-1] for t in tickers)  # most recent bar common to all
    chg = {}
    for t in tickers:
        idx = data[t].index
        pos = idx.searchsorted(now, side="right") - 1
        if pos < LOOKBACK - 1:
            continue
        chg[t] = data[t]["Close"].iloc[pos] / data[t]["Close"].iloc[pos - LOOKBACK + 1] - 1

    ranked = pd.Series(chg).sort_values(ascending=False)
    if len(ranked) == 0:
        raise RuntimeError("Not enough hourly history to compute trailing momentum yet.")
    top = list(ranked.index[:top_n])
    if dual:
        top = [t for t in top if ranked[t] > 0]
    return top, data, ranked


def compute_live_kronos_hourly(settings: dict, sample_count: int = DEFAULT_SAMPLE_COUNT):
    """Kronos's forecast at the most recent available hourly bar — the
    same LOOKBACK/PRED_LEN kronos_ic_hourly.py screened (IC -0.081, 46.4%
    hit rate)."""
    top_n = settings.get("momentum_top_n", 3)
    dual = settings.get("risk_engine", False)
    tickers = settings["tickers"]

    data = _fetch_all(tickers)
    tickers = [t for t in tickers if t in data]
    if len(tickers) < top_n + 1:
        raise RuntimeError(f"Only {len(tickers)}/{len(settings['tickers'])} tickers "
                            f"fetched — need at least {top_n + 1}.")

    now = min(data[t].index[-1] for t in tickers)
    predictor = get_predictor()
    result = kronos_forecast_at(predictor, data, tickers, now, PRED_LEN, sample_count)
    chg = {t: v[0] for t, v in result.items()}
    ranked = pd.Series(chg).sort_values(ascending=False)
    if len(ranked) == 0:
        raise RuntimeError("Kronos produced no forecasts — check hourly data availability.")
    top = list(ranked.index[:top_n])
    if dual:
        top = [t for t in top if ranked[t] > 0]
    return top, data, ranked
