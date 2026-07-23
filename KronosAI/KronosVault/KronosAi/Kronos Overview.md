---
tags: [kronos, overview, architecture]
source: KronosAI/
status: "Integrated, unvalidated"
---

# Kronos Overview

## What it is

Kronos ([shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos), paper:
[arXiv:2508.02739](https://arxiv.org/abs/2508.02739)) is an open-source
foundation model trained specifically on financial candlestick (K-line) data
from 45+ global exchanges — not a general time-series model repurposed for
finance. Two-stage architecture:

1. **KronosTokenizer** — quantizes continuous multi-dimensional OHLCV data into
   hierarchical discrete tokens.
2. **Kronos** — an autoregressive Transformer pre-trained on those tokens.

**Model used here:** `Kronos-small` (24.7M params) + `Kronos-Tokenizer-base`
(max context 512 bars) — the smallest fully open-sourced pair on Hugging Face
(`NeoQuasar/Kronos-small`, `NeoQuasar/Kronos-Tokenizer-base`). `Kronos-base`
(102M params, same tokenizer) is available too if `Kronos-small` turns out
under-powered; `Kronos-large` (499M) is not open-sourced.

## Why it's in this project

The owner asked for Kronos to be set up as a new research agent — a
quantitative alternative/complement to `research_agent.py`'s LLM-based
qualitative notes. Where `research_agent.py` reads price/technical/fundamental
context and writes a reasoned thesis (direction, confidence, risks), Kronos
directly forecasts the OHLCV path N trading days ahead and ranks the watchlist
by predicted return — a pure numeric signal, no reasoning/explanation attached.

## How it forecasts

Given a DataFrame of historical `open/high/low/close` (+ optional
`volume`/`amount`) and timestamps, `KronosPredictor.predict()` /
`.predict_batch()` returns a forecast DataFrame for `pred_len` steps ahead,
indexed by future timestamps you supply. Sampling is controlled by:
- `T` (temperature)
- `top_p` (nucleus sampling)
- `sample_count` — **how many forecast paths to generate and average.** This
  turned out to matter a lot in practice — see [[Kronos Integration Log]].

`predict_batch()` runs multiple tickers' forecasts in parallel (same lookback
length and pred_len required for all), which is what the watchlist integration
uses — much faster than forecasting tickers one at a time.

## Files (in `KronosAI/`)

| File | Purpose |
|---|---|
| `model/` | Vendored model code (`kronos.py`, `module.py`, `__init__.py`) from upstream. No nested `.git` — copied in as plain files, not a submodule. |
| `kronos_agent.py` | Shared module: `get_predictor()` (singleton model loader), `forecast_tickers()` (low-level batch forecast), `forecast_signal()` (returns the same `(top, data, ranked)` shape `paper_trader.py`'s momentum signal does, so it drops into the existing approval/execution pipeline unchanged). |
| `kronos_watchlist_forecast.py` | Ad hoc CLI — forecast the real watchlist (or specific tickers) and print a ranked table. Thin wrapper around `kronos_agent.py`. |
| `kronos_smoke_test.py` | Toy-data smoke test using a sample CSV shipped in the upstream repo (Alibaba HK 5-min bars) — proves the model loads and `predict()` runs, independent of any real project data. |
| `data/HK_ali_09988_kline_5min_all.csv` | The toy CSV used by the smoke test. |
| `requirements.txt`, `README.md`, `LICENSE` | Vendored from upstream, unmodified. |

## Where it's integrated (outside `KronosAI/`)

- **`trader_app.py`** — menu item 7, "Kronos forecast (research agent)".
  Analysis only: prints a ranked table, places no orders (consistent with the
  app's own "this app places no orders" promise). Lazy-imports
  `kronos_agent` only when the menu item is chosen, same pattern as the
  IBKR menu's lazy `ibkr_service` import — avoids paying torch's import cost
  when the feature isn't used.
- **`paper_trader.py`** — `--signal kronos` flag. Default remains `momentum`
  (the strategy that's actually earned Phase 3 evidence); Kronos is
  opt-in and explicitly labeled unvalidated in its own printed output.
  Everything downstream of signal selection — ATR-based stop sizing, bracket
  orders, RiskGuard, `trade_journal.csv` — is identical regardless of which
  signal produced the ranking.

## Status: unvalidated

No backtest, no `grade_calls.py`-style calibration exists for Kronos yet. Per
the main project's evidence-gating philosophy (autonomy/trust is earned by
graded evidence, not by adding capability — see the main vault's
[[ADR - Python Rules, Not Model Predictions]]), Kronos is wired in as an
**opt-in signal for paper-trading evaluation**, not a default, and nothing it
produces should be read as validated until it's been through the same rigor
momentum rotation went through (in/out-of-sample backtest, vs buy-and-hold).

## Related Notes

- [[00 MOC - Kronos Vault]]
- [[Kronos Integration Log]] — what was actually built/tested and when
- Main vault: [[Kronos Research Agent]], [[Research Agent Workflow]], [[ADR - Python Rules, Not Model Predictions]]
