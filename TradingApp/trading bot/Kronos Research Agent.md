---
tags: [research, kronos, agent, workflow]
source: KronosAI/kronos_agent.py
status: "Integrated, backtested — no measurable edge found"
last_updated: 2026-07-23
---

# Kronos Research Agent

## What it is

Kronos is a foundation model for financial candlestick (K-line) forecasting,
vendored from [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos)
into `KronosAI/` (previously just a README + requirements.txt in this repo).
It's the project's second research agent, alongside — not replacing —
`research_agent.py`:

| | `research_agent.py` | Kronos (`kronos_agent.py`) |
|---|---|---|
| Kind | LLM (Claude) reasoning over technical + fundamental context | Foundation model, directly forecasts OHLCV bars |
| Output | Qualitative thesis: direction, confidence, risks, key levels (`.md` in `research_log/`) | Quantitative: predicted close price N trading days out, ranked by % change |
| Validation | Ungraded so far (notes too fresh — see [[Phase Milestones Dashboard]]) | **Backtested 2026-07-23 — no measurable edge found** (Spearman IC 0.036, 50% hit rate) |
| Default in `paper_trader.py`? | N/A (momentum rotation is the trading signal, not this) | No — momentum stays default; `--signal kronos` opts in |

A full write-up (architecture, files, sample_count findings, integration
detail) lives in the sub-project's own vault: see `KronosAI/KronosVault/` —
start at its `00 MOC - Kronos Vault.md`. This note is the summary for this
vault's index.

## Where it's wired in

- **`trader_app.py`** — menu item 7, "Kronos forecast (research agent)".
  Prints a ranked table of predicted return per watchlist ticker. Analysis
  only, no orders — same promise the rest of the app makes.
- **`paper_trader.py`** — `python3 paper_trader.py --signal kronos` uses
  Kronos's ranking instead of momentum rotation's for the proposed rebalance.
  Everything downstream (ATR-based stop sizing, bracket orders, RiskGuard,
  human approval, `trade_journal.csv`) is unchanged — see
  [[ADR - Python Rules, Not Model Predictions]], which this respects exactly:
  Kronos only proposes a ranking, the same code-enforced risk layer decides
  what actually executes.

## Key finding so far: sample_count matters

Kronos's batch forecast can average multiple sampled paths
(`sample_count=N`). Tested N=1 vs 10 vs 30 against the full watchlist:
`sample_count=1` swung individual tickers' predicted return by 5-10
percentage points run to run — too noisy to read anything into. Averaging
10-30 paths cut that swing roughly 3x. Code default is now `sample_count=10`.
Full numbers: `KronosAI/KronosVault/Kronos Integration Log.md`.

## Status: backtested, no measurable forecasting skill found (2026-07-23)

Kronos was walk-forward backtested against the entire honest evaluation
window its own pretraining allows: the paper (arXiv:2508.02739) states
training data extends to June 2024 and its own test period begins July
2024, so July 2024 → now (~24 monthly rebalances) is the only window that
isn't at risk of scoring memorization rather than forecasting.

**Two-stage result:**
1. **Information coefficient** (predicted vs realized 20-day return, 304
   pooled date×ticker pairs): Spearman **0.036**, directional hit rate
   **50.0%** — statistically no signal.
2. **Portfolio backtest** (identical dates/costs to momentum, same
   simulation engine): Kronos rotation 20.99% CAGR / -9.30% DD — beats SPY
   (17.92% / -18.76%) but loses badly to momentum rotation (59.07% /
   -15.60%). Given the flat IC, the SPY-beating result should be read as
   noise from a small (24-decision) sample, not real skill — this is the
   exact trap a portfolio-only backtest would have missed.

**Verdict:** no measurable forecasting skill detected. This is a real,
reported negative finding — same treatment the SMA-crossover rejection got
— not a "hasn't been tested" placeholder. Kronos stays wired into
`trader_app.py` and `paper_trader.py --signal kronos` as an opt-in signal
for reference/re-testing, not because it's shown value.

Full methodology, the scipy bug hit along the way, and the checkpoint
safety-net added because of it: `KronosAI/KronosVault/Kronos Integration
Log.md`.

**If revisited:** re-run with 2-3 different seeds to confirm the IC finding
is stable (not urgent — a result this flat is unlikely to flip on a
different draw, but hasn't been formally confirmed).

## Related Notes

- [[Research Agent Workflow]] — the other research agent (LLM-based)
- [[ADR - Python Rules, Not Model Predictions]] — why this integration is safe: Kronos proposes, code + human still gate everything
- [[Phase Milestones Dashboard]] — where this fits in the phase picture
- [[00 MOC - Trading Bot Vault]]
