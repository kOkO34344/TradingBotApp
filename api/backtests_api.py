"""
backtests_api.py — reads what the backtests actually produced.

Two sources, kept clearly apart because they have different standing:

  1. `backtest_results.csv` — real per-ticker output from
     `sma_crossover_backtest.py`, with the in-sample / out-of-sample split
     preserved as separate rows. Loaded and served as data.

  2. The headline results for momentum rotation, ORB and Kronos, which live
     in CLAUDE.md's Empirical Findings rather than in a machine-readable
     file. These are served as QUOTED findings, each tagged with its source
     and the caveat recorded alongside it — never blended into the same
     table as (1), and never presented as if this API recomputed them.

That separation is the whole point. A dashboard that silently mixed
"computed just now" with "written down in July" would be exactly the kind of
authoritative-looking record this project has twice been burned by. The
`source` field on every finding says which it is.

Nothing here re-runs a backtest. Running one is a research act with its own
scripts and its own notification wrapper; this module only reports.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

RESULTS_CSV = BASE_DIR / "backtest_results.csv"
REPORT_MD = BASE_DIR / "backtest_report.md"

NUMERIC_FIELDS = {
    "strategy_return_pct", "strategy_cagr_pct", "strategy_max_dd_pct",
    "strategy_sharpe", "strategy_trades", "strategy_win_rate_pct",
    "bh_return_pct", "bh_cagr_pct", "bh_max_dd_pct", "bh_sharpe",
    "spy_bh_return_pct", "spy_bh_cagr_pct",
}

# Period keys as written by sma_crossover_backtest.py, with the reading each
# one deserves. Out-of-sample is the only one that can support a claim.
PERIOD_LABELS = {
    "full_history": ("Full history", "Spans the tuning window — not evidence."),
    "in_sample_2010_2018": ("In-sample 2010–2018",
                            "The window parameters could have been fitted to."),
    "out_of_sample_2019_present": ("Out-of-sample 2019–present",
                                   "The only period that supports a claim."),
}


def load_results() -> dict:
    """Per-ticker SMA-crossover results, grouped by period."""
    if not RESULTS_CSV.exists():
        return {"periods": [], "rows": [], "error":
                f"{RESULTS_CSV.name} not found — run sma_crossover_backtest.py."}

    rows = []
    with RESULTS_CSV.open(newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            row = {}
            for key, value in rec.items():
                if key in NUMERIC_FIELDS:
                    try:
                        row[key] = float(value)
                    except (TypeError, ValueError):
                        row[key] = None
                else:
                    row[key] = value
            rows.append(row)

    periods = []
    for key in ("out_of_sample_2019_present", "in_sample_2010_2018", "full_history"):
        subset = [r for r in rows if r.get("period") == key]
        if not subset:
            continue
        label, caveat = PERIOD_LABELS.get(key, (key, ""))

        def mean(field: str) -> float | None:
            values = [r[field] for r in subset if r.get(field) is not None]
            return sum(values) / len(values) if values else None

        beat_spy = sum(
            1 for r in subset
            if r.get("strategy_cagr_pct") is not None
            and r.get("spy_bh_cagr_pct") is not None
            and r["strategy_cagr_pct"] > r["spy_bh_cagr_pct"]
        )
        beat_bh = sum(
            1 for r in subset
            if r.get("strategy_cagr_pct") is not None
            and r.get("bh_cagr_pct") is not None
            and r["strategy_cagr_pct"] > r["bh_cagr_pct"]
        )
        periods.append({
            "key": key,
            "label": label,
            "caveat": caveat,
            "tickers": len(subset),
            "rows": sorted(subset, key=lambda r: r.get("ticker", "")),
            "avgStrategyCagr": mean("strategy_cagr_pct"),
            "avgBuyHoldCagr": mean("bh_cagr_pct"),
            "avgSpyCagr": mean("spy_bh_cagr_pct"),
            "avgMaxDd": mean("strategy_max_dd_pct"),
            "beatSpy": beat_spy,
            "beatBuyHold": beat_bh,
        })

    return {
        "strategy": "SMA 20/50 crossover, long-only, 0.1% per trade",
        "source": str(RESULTS_CSV.name),
        "periods": periods,
        "rows": rows,
        "error": None,
    }


# Recorded results for the strategies that have no machine-readable output.
# Quoted from CLAUDE.md's Empirical Findings, verbatim in substance, each
# with the caveat that was recorded with it. `computed: False` marks them as
# quoted rather than recalculated — the UI shows that distinction.
RECORDED_FINDINGS = [
    {
        "name": "Momentum rotation (top-3 of 10, monthly)",
        "verdict": "beat",
        "computed": False,
        "source": "CLAUDE.md — Empirical findings",
        "metrics": [
            {"label": "CAGR", "value": "18.5%"},
            {"label": "SPY", "value": "16%"},
            {"label": "Max drawdown", "value": "-21.7%"},
            {"label": "SPY max DD", "value": "-33.7%"},
        ],
        "note": "The only strategy family that ever earned Phase 3. Caveat "
                "recorded with it: the hand-picked mega-cap universe flatters "
                "it, and the broad-universe test is still pending.",
        "status": "DISABLED by owner instruction (2026-07-28) — does not run "
                  "unless explicitly asked for in-session.",
    },
    {
        "name": "SMA 20/50, golden cross, Donchian, RSI-2",
        "verdict": "lost",
        "computed": True,
        "source": "backtest_results.csv (table below)",
        "metrics": [
            {"label": "Result", "value": "all lost to buy-and-hold"},
            {"label": "Window", "value": "2019–2026, after costs"},
        ],
        "note": "Risk overlays cut drawdown but did not close the performance "
                "gap.",
    },
    {
        "name": "ORB (Zarattini/Aziz rules), 5m QQQ",
        "verdict": "lost",
        "computed": False,
        "source": "CLAUDE.md — Empirical findings",
        "metrics": [
            {"label": "Return", "value": "-12.6%"},
            {"label": "Window", "value": "recent 60 days"},
        ],
        "note": "Regime-dependent.",
    },
    {
        "name": "Kronos (foundation-model forecaster)",
        "verdict": "no-edge",
        "computed": False,
        "source": "KronosAI/kronos_backtest.py, 2026-07-23",
        "metrics": [
            {"label": "Spearman IC", "value": "0.036"},
            {"label": "Hit rate", "value": "50.0%"},
            {"label": "Pairs", "value": "304"},
            {"label": "Hourly IC", "value": "-0.081"},
        ],
        "note": "No measurable forecasting skill detected. The portfolio sim "
                "happened to beat SPY (20.99% vs 17.92% CAGR) but that is "
                "noise from a 24-decision sample given the flat IC — and it "
                "lost badly to momentum rotation (59.07% CAGR) on identical "
                "dates and costs.",
        "status": "Current focus signal, by decision rather than by measured "
                  "edge.",
    },
]


def report_markdown() -> str | None:
    if REPORT_MD.exists():
        return REPORT_MD.read_text(encoding="utf-8")
    return None


def _selftest() -> int:
    """`python3 api/backtests_api.py` — offline."""
    failures = []

    def check(name, cond):
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    data = load_results()
    check("results loaded", data["error"] is None and len(data["rows"]) > 0)
    check("periods grouped", len(data["periods"]) >= 2)

    oos = next((p for p in data["periods"]
                if p["key"] == "out_of_sample_2019_present"), None)
    check("out-of-sample period present", oos is not None)
    if oos:
        check("out-of-sample listed first", data["periods"][0] is oos)
        check("has per-ticker rows", len(oos["rows"]) >= 5)
        check("averages computed", oos["avgStrategyCagr"] is not None)
        # The recorded finding is that SMA beat SPY in 0 of 10 out-of-sample.
        check("beatSpy count matches the recorded finding", oos["beatSpy"] == 0)
        check("strategy underperformed buy-and-hold on average",
              oos["avgStrategyCagr"] < oos["avgBuyHoldCagr"])

    check("findings carry a source", all(f["source"] for f in RECORDED_FINDINGS))
    check("quoted findings are marked not-computed",
          any(f["computed"] is False for f in RECORDED_FINDINGS))
    check("report markdown available", report_markdown() is not None)

    print(f"\n{len(failures)} failure(s)." if failures else "\nAll backtest checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
