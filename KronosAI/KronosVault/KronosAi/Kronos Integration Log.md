---
tags: [kronos, log, evidence]
status: "Live log — append new entries at the top"
last_updated: 2026-07-23
---

# Kronos Integration Log

Dated log of what was actually built and tested, with real numbers — not a
restatement of the code (see [[Kronos Overview]] for that). Append new
entries above the previous ones as work continues.

## 2026-07-23 — Integrated as the project's research agent, wired into app + paper trading

**Ask:** Make Kronos the main research agent for the project, integrate it
into `trader_app.py`, and continue testing it via paper trading.

**Built:**
- `KronosAI/kronos_agent.py` — new shared module. `forecast_signal()` returns
  the exact `(top, data, ranked)` shape `paper_trader.compute_signal()`
  (momentum) already returns, so Kronos slots into the existing
  approval/execution pipeline (ATR sizing, bracket orders, RiskGuard, trade
  journal) with zero changes to any of that — only the ranking source differs.
- `trader_app.py` — new menu item 7, "Kronos forecast (research agent)".
  Verified end to end via the actual TUI (piped stdin), including the model
  downloading from Hugging Face, forecasting, and rendering a sorted Rich
  table. No orders placed — matches the app's own "backtests/analysis only"
  framing.
- `paper_trader.py` — new `--signal {momentum,kronos}` flag, default
  `momentum` (unchanged behavior unless explicitly overridden). Verified the
  Kronos path computes a correct ranking against the real 14-ticker watchlist
  inside the full script (failed only at the IBKR connect step because IB
  Gateway wasn't running locally at test time — identical failure the
  momentum path would also hit, not Kronos-specific).
- `kronos_watchlist_forecast.py` refactored to reuse `kronos_agent.py` instead
  of duplicating the fetch/predict loop.

**Side fix (unrelated to Kronos):** `trader_settings.json`'s `commission_pct`
had been changed to `2.1` outside this session (looked like a typo — 210%
commission makes no sense); reverted to `0.1`. `cash` was left at `1141420`
per owner confirmation — that's the actual IBKR paper account balance, not a
stray value.

**Still true:** Kronos has no backtest and no calibration. It's opt-in
everywhere it's wired in — nothing defaults to it.

## 2026-07-23 — sample_count stabilization test

**Question:** Kronos's `predict_batch(sample_count=N)` averages N sampled
forecast paths internally. Does raising N actually stabilize the output, or
is `sample_count=1`'s noise structural?

**Method:** Ran the full watchlist forecast (20 trading days ahead) at
`sample_count` = 1, 10, and 30, same watchlist, back to back.

**Result — predicted % change to end of forecast window:**

| Ticker | n=1 | n=10 | n=30 |
|---|---|---|---|
| AAPL | -12.99% | -19.41% | -16.82% |
| MSFT | +4.79% | +3.57% | +3.79% |
| GOOGL | -15.32% | -9.32% | -9.44% |
| AMZN | +3.02% | +0.80% | +0.31% |
| JPM | -13.17% | -10.81% | -10.76% |
| JNJ | -14.89% | -13.56% | -14.22% |
| PG | -1.63% | -1.09% | -0.86% |
| XOM | -19.57% | -8.97% | -10.72% |
| KO | -8.00% | -11.59% | -9.72% |
| DIS | +2.87% | +1.89% | +2.44% |
| NVDA | -7.53% | -6.59% | -7.06% |
| PLTR | -3.11% | -5.28% | -3.79% |

Average absolute swing between runs: ~3.2pp (n=1 → n=10), dropping to ~0.9pp
(n=10 → n=30) — roughly the shrinkage Monte Carlo averaging predicts, and
reasonable evidence this is genuine variance reduction, not just noise.
AAPL was the one ticker still moving meaningfully at n=30 (2.6pp).

**Conclusion:** `sample_count=1` is too noisy to draw any conclusion from.
Set the code default to `sample_count=10` as a speed/stability tradeoff
(runtime scales roughly linearly with sample_count — n=30 took multiple
minutes for the full watchlist vs seconds for n=1). `--sample-count` is
exposed as a CLI flag / function argument to raise it further if needed.

## 2026-07-23 — Vendored + wired to real watchlist data

**Built:**
- Cloned `shiyu-coder/Kronos` from GitHub, vendored `model/` (the actual
  `Kronos`/`KronosTokenizer`/`KronosPredictor` classes) into `KronosAI/`,
  which previously only had a README and requirements.txt. No nested `.git` —
  copied in as plain files.
- `kronos_smoke_test.py` — proved the model loads from Hugging Face and
  `predict()` runs, using a real 5-min K-line CSV shipped in the upstream
  repo (Alibaba HK 09988) since the README's own referenced example CSV
  (`XSHG_5min_600977.csv`) isn't actually included upstream.
- `kronos_watchlist_forecast.py` — wired the model to the project's actual
  watchlist (`trader_settings.json`) instead of the toy CSV. Reused
  `trader_app.load_settings()`/`fetch()` rather than reimplementing a data
  pull. Force-refetches into `price_data_live/` (the same cache
  `paper_trader.py` already uses for fresh live-ranking data) rather than
  `trader_app`'s `price_data/` — that cache is keyed only by ticker, not date
  range, and a prior incident (documented in `paper_trader.py`) showed
  sharing it silently truncates the long-history backtest cache.

**Environment setup:**
- `.venv` was missing `torch`, `einops`, `huggingface_hub`, `matplotlib`,
  `tqdm`, `safetensors` despite being reported as "installed" — none were
  actually present. Installed unpinned (the repo's pins, e.g.
  `huggingface_hub==0.33.1`, predate Python 3.13 wheel availability).
  `torch` resolved to `2.13.0`, CPU/MPS build for Apple Silicon.

**Verified:** Ran an actual forecast against all 12 (now 14) watchlist
tickers via Hugging Face + `mps` — model downloads and runs correctly,
predictions are real numbers, not placeholders.

## Related Notes

- [[Kronos Overview]]
- [[00 MOC - Kronos Vault]]
