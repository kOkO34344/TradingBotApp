---
tags: [research, kronos, agent, workflow]
source: KronosAI/kronos_agent.py
status: "Integrated, unvalidated"
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
| Validation | Ungraded so far (notes too fresh — see [[Phase Milestones Dashboard]]) | **Unvalidated — no backtest, no calibration yet** |
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

## Status: not a substitute for graded evidence yet

No backtest, no `grade_calls.py`-equivalent calibration. Treat any Kronos
output the same way you'd treat an unbacktested strategy idea: interesting,
not actionable on its own. It's opt-in in both integration points above —
nothing in the project defaults to it.

**Natural next steps** (not started yet):
1. Backtest Kronos's ranking the way momentum rotation was backtested
   (in/out-of-sample, after costs, vs buy-and-hold) before trusting it for
   anything beyond curiosity.
2. Once `--signal kronos` gets run for real in `paper_trader.py`, track its
   proposals/approvals the same way momentum's are tracked, so it accumulates
   its own evidence trail rather than riding on momentum's.

## Related Notes

- [[Research Agent Workflow]] — the other research agent (LLM-based)
- [[ADR - Python Rules, Not Model Predictions]] — why this integration is safe: Kronos proposes, code + human still gate everything
- [[Phase Milestones Dashboard]] — where this fits in the phase picture
- [[00 MOC - Trading Bot Vault]]
