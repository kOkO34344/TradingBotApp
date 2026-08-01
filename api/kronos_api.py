"""
kronos_api.py — Kronos forecasts for the web UI, with the noise made visible.

The point of this module is not to produce a ranking. `kronos_agent` already
does that. The point is to show how much the ranking MOVES between runs,
because CLAUDE.md records that it moves enough to change which orders get
placed:

    Two `paper_trader.py --dry-run` runs ~30 minutes apart, same closed-market
    data, same sample_count, produced different top-3s: [AMZN, MSFT, GOOGL]
    then [AMZN, MSFT, DIS]. Run 1 proposed BUY MSFT + BUY GOOGL (~$50k) and
    SELL DIS; run 2 proposed BUY MSFT only and HOLD DIS.

So a single forecast is presented here as one draw from a distribution, never
as "the" forecast. The UI shows the spread across draws, the gap between rank
N and N+1, and an explicit warning when that gap is inside the noise.

How the draws are produced
--------------------------
`KronosPredictor` averages its internal samples before returning
(`np.mean(preds, axis=1)` in KronosAI/model/kronos.py), so individual sampled
paths are not recoverable from one call. That code is vendored third party and
the backtest depends on it, so it is not modified. Instead each draw is an
independent `forecast_tickers()` call — which is exactly the thing the owner
did by hand when the instability was discovered, done systematically.

Cost is real: each draw is a full batch inference. Draws default to 3, and
the job streams progress because this takes minutes.
"""
from __future__ import annotations

import logging
import statistics
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "KronosAI"))

log = logging.getLogger("api.kronos")

DEFAULT_DRAWS = 3
MAX_DRAWS = 10
# Matches kronos_agent.DEFAULT_SAMPLE_COUNT. The ranking shown here has to be
# the ranking the project would actually act on, so it averages the same
# number of internal samples paper_trader does. sample_count=1 is a single
# noisy draw — useful for the Monte Carlo fan, misleading for a ranking.
DEFAULT_SAMPLE_COUNT = 10
# A rank-N/N+1 gap at or below this (in predicted % return) is treated as
# indistinguishable. Derived from the observed GOOGL/DIS swap, where ~1 point
# separated them and they traded places between two runs on identical data.
GAP_WARNING_THRESHOLD = 1.0


class KronosError(RuntimeError):
    """User-facing Kronos problem."""


def _import_agent():
    """Import lazily and translate the usual failure into something useful.

    A missing torch here almost always means the wrong interpreter rather
    than a missing package — conda base has pandas/ib_async but not torch,
    which is the exact trap documented in CLAUDE.md.
    """
    try:
        import kronos_agent
        return kronos_agent
    except ImportError as exc:
        raise KronosError(
            f"Kronos dependencies are not importable ({exc}). This is usually "
            f"the wrong interpreter rather than a missing package — the API "
            f"must run under the project venv. Current interpreter: "
            f"{sys.executable}"
        ) from exc


def _series_from(df, price_key: str) -> list[dict]:
    out = []
    for ts, value in df[price_key].items():
        try:
            out.append({"time": int(ts.timestamp()), "value": float(value)})
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def _ohlc_from(df) -> list[dict]:
    rows = []
    cols = {c.lower(): c for c in df.columns}
    for ts, rec in df.iterrows():
        try:
            rows.append({
                "time": int(ts.timestamp()),
                "open": float(rec[cols["open"]]),
                "high": float(rec[cols["high"]]),
                "low": float(rec[cols["low"]]),
                "close": float(rec[cols["close"]]),
            })
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return rows


def run_forecast(ctx, tickers: list[str], draws: int = DEFAULT_DRAWS,
                 sample_count: int = DEFAULT_SAMPLE_COUNT,
                 pred_len: int | None = None,
                 history_bars: int = 120) -> dict:
    """Run `draws` independent Kronos forecasts and summarise the spread.

    Runs inside a job thread. `ctx` reports progress and logs.
    """
    agent = _import_agent()
    draws = max(1, min(int(draws), MAX_DRAWS))
    pred_len = int(pred_len or agent.PRED_LEN)

    ctx.log(f"Kronos forecast: {len(tickers)} tickers, {draws} independent "
            f"draw(s), sample_count={sample_count}, pred_len={pred_len}")
    ctx.log("Each draw is a separate batch inference — this is the slow part.")

    ctx.progress(0.02, "Loading tokenizer and model…")
    t0 = time.time()
    agent.get_predictor()          # cached per process after the first call
    ctx.log(f"Model ready in {time.time() - t0:.0f}s")

    per_draw: list[dict[str, float]] = []
    last_hist = None
    last_pred = None
    ok_tickers: list[str] = []

    for i in range(draws):
        ctx.raise_if_cancelled()
        ctx.progress(0.05 + 0.9 * (i / draws),
                     f"Draw {i + 1} of {draws}…")
        started = time.time()
        try:
            names, hist_data, pred_dfs = agent.forecast_tickers(
                tickers, pred_len=pred_len, sample_count=sample_count,
                verbose=False,
            )
        except Exception as exc:                        # noqa: BLE001
            raise KronosError(f"Draw {i + 1} failed: {exc}") from exc

        returns: dict[str, float] = {}
        for t in names:
            last_close = float(hist_data[t]["Close"].iloc[-1])
            pred_close = float(pred_dfs[t]["close"].iloc[-1])
            returns[t] = (pred_close / last_close - 1) * 100
        per_draw.append(returns)
        ok_tickers = names
        last_hist, last_pred = hist_data, pred_dfs

        ranked = sorted(returns, key=returns.get, reverse=True)
        ctx.log(
            f"Draw {i + 1} done in {time.time() - started:.0f}s — top: "
            + ", ".join(f"{t} {returns[t]:+.2f}%" for t in ranked[:4])
        )

    ctx.progress(0.97, "Summarising spread across draws…")

    # ---- per-ticker statistics across draws
    stats = []
    for t in ok_tickers:
        values = [d[t] for d in per_draw if t in d]
        if not values:
            continue
        mean = statistics.fmean(values)
        spread = max(values) - min(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
        stats.append({
            "ticker": t,
            "meanReturnPct": mean,
            "minReturnPct": min(values),
            "maxReturnPct": max(values),
            "spreadPct": spread,
            "stdevPct": stdev,
            "draws": values,
        })
    stats.sort(key=lambda s: s["meanReturnPct"], reverse=True)
    for i, s in enumerate(stats):
        s["rank"] = i + 1

    # ---- rank stability: did the same names stay in the same places?
    rank_changes = 0
    if len(per_draw) > 1:
        orders = [
            sorted(d, key=d.get, reverse=True) for d in per_draw
        ]
        first = {t: i for i, t in enumerate(orders[0])}
        for other in orders[1:]:
            for i, t in enumerate(other):
                if first.get(t) != i:
                    rank_changes += 1
        rank_changes = rank_changes // max(1, len(orders) - 1)

    # ---- the gap that decides whether a rotation is real or a coin flip
    import trader_app as ta
    settings = ta.load_settings()
    top_n = int(settings.get("momentum_top_n", 3))
    gap = None
    gap_warning = None
    if len(stats) > top_n:
        gap = stats[top_n - 1]["meanReturnPct"] - stats[top_n]["meanReturnPct"]
        boundary_spread = max(
            stats[top_n - 1]["spreadPct"], stats[top_n]["spreadPct"])
        if gap <= GAP_WARNING_THRESHOLD or (draws > 1 and gap < boundary_spread):
            gap_warning = (
                f"The gap between rank {top_n} ({stats[top_n - 1]['ticker']}, "
                f"{stats[top_n - 1]['meanReturnPct']:+.2f}%) and rank {top_n + 1} "
                f"({stats[top_n]['ticker']}, {stats[top_n]['meanReturnPct']:+.2f}%) "
                f"is {gap:.2f} points, "
                + (f"which is inside the {boundary_spread:.2f}-point spread those "
                   "two names showed across draws. "
                   if draws > 1 and gap < boundary_spread else
                   f"at or below the {GAP_WARNING_THRESHOLD:.1f}-point threshold. ")
                + "Which of them makes the cut is close to a coin flip — "
                  "re-run before rotating on it."
            )

    # ---- chart data for the last draw
    charts = {}
    for t in ok_tickers:
        hist = last_hist[t].tail(history_bars)
        charts[t] = {
            "history": _ohlc_from(hist),
            "forecast": _ohlc_from(last_pred[t]),
            "lastClose": float(last_hist[t]["Close"].iloc[-1]),
            "predictedClose": float(last_pred[t]["close"].iloc[-1]),
        }

    ctx.progress(1.0, "Done.")
    return {
        "generatedAt": time.time(),
        "tickers": ok_tickers,
        "requested": tickers,
        "skipped": [t for t in tickers if t not in ok_tickers],
        "draws": draws,
        "sampleCount": sample_count,
        "predLen": pred_len,
        "topN": top_n,
        "stats": stats,
        "perDraw": per_draw,
        "rankChangesPerDraw": rank_changes,
        "boundaryGap": gap,
        "gapWarning": gap_warning,
        "charts": charts,
    }


def monte_carlo(ctx, ticker: str, paths: int = 12, pred_len: int | None = None,
                history_bars: int = 120) -> dict:
    """Many single-sample forecast paths for one ticker — a fan chart.

    Each path is `sample_count=1`, i.e. one raw draw from the model rather
    than an average. That is what makes the fan an honest picture of the
    model's own uncertainty: averaging first would collapse exactly the
    variation the chart exists to show.
    """
    agent = _import_agent()
    paths = max(2, min(int(paths), 40))
    pred_len = int(pred_len or agent.PRED_LEN)
    ticker = ticker.upper()

    ctx.log(f"Monte Carlo: {paths} single-sample paths for {ticker}, "
            f"pred_len={pred_len}")
    ctx.progress(0.02, "Loading model…")
    agent.get_predictor()

    series: list[list[dict]] = []
    finals: list[float] = []
    hist = None
    last_close = None

    for i in range(paths):
        ctx.raise_if_cancelled()
        ctx.progress(0.05 + 0.9 * (i / paths), f"Path {i + 1} of {paths}…")
        names, hist_data, pred_dfs = agent.forecast_tickers(
            [ticker], pred_len=pred_len, sample_count=1, verbose=False)
        if not names:
            raise KronosError(
                f"{ticker} does not have enough history for Kronos "
                f"(needs {agent.LOOKBACK} daily bars)."
            )
        hist = hist_data[ticker]
        last_close = float(hist["Close"].iloc[-1])
        closes = _series_from(pred_dfs[ticker], "close")
        series.append(closes)
        finals.append(closes[-1]["value"] if closes else float("nan"))

    returns = [(f / last_close - 1) * 100 for f in finals]
    returns.sort()

    def pct(p: float) -> float:
        idx = min(len(returns) - 1, max(0, int(round(p * (len(returns) - 1)))))
        return returns[idx]

    ctx.progress(1.0, "Done.")
    return {
        "ticker": ticker,
        "generatedAt": time.time(),
        "paths": paths,
        "predLen": pred_len,
        "lastClose": last_close,
        "history": _ohlc_from(hist.tail(history_bars)),
        "series": series,
        "finalReturnsPct": returns,
        "medianReturnPct": statistics.median(returns),
        "meanReturnPct": statistics.fmean(returns),
        "p10ReturnPct": pct(0.10),
        "p90ReturnPct": pct(0.90),
        "shareUp": sum(1 for r in returns if r > 0) / len(returns) * 100,
    }
