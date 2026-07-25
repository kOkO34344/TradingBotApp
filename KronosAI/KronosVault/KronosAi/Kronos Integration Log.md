---
tags: [kronos, log, evidence]
status: "Live log — append new entries at the top"
last_updated: 2026-07-24
---

# Kronos Integration Log

Dated log of what was actually built and tested, with real numbers — not a
restatement of the code (see [[Kronos Overview]] for that). Append new
entries above the previous ones as work continues.

## 2026-07-24 — Hourly IC screen (still no edge) + Kronos becomes an autotrade option

**Context:** the owner asked for a "trade toggle" — flip on, the agent
trades unattended until flipped off — and wanted it to actually rebalance
faster than monthly. Before wiring Kronos (or anything) into that, ran the
same IC-screen methodology from the 2026-07-23 daily backtest at hourly
granularity, since Kronos operates on bar counts, not calendar time —
`LOOKBACK=400`/`PRED_LEN=20` bars applied to hourly data is a direct,
minimal-change port of the exact same code.

**Data feasibility check (real, not assumed):** yfinance caps 15m/30m
history at ~58-60 days — too short for a meaningful screen. Hourly bars go
back further (tested at ~729 days requested, actually got ~2.9 years —
apparently yfinance doesn't cap 1h bars as tightly as the shorter
intervals). IBKR's own 15-min bars (tested live against the paper Gateway)
cap at ~1 year per request; a 2-year request came back empty. Hourly bars
via yfinance was the only source that gave both real history depth and no
live-connection dependency.

**Result** (`KronosAI/kronos_ic_hourly.py`, 24 checkpoints, sample_count=10,
July 2024 → June 2026 — same pretraining-cutoff-bounded window as the daily
test, since the cutoff applies across all bar frequencies, not just daily):

| Signal | Spearman IC | Hit rate |
|---|---|---|
| Kronos (hourly) | -0.081 | 46.4% |
| Momentum-style baseline (matched horizon) | -0.037 | 48.5% |

336 pooled pairs. Both indistinguishable from noise — no edge at hourly
cadence either, consistent with the daily result.

**What happened next:** told this twice, the owner chose to build the
autotrade toggle anyway, with Kronos included as a selectable signal
(alongside an hourly momentum-style ranking) — a deliberate live paper
experiment, not a validation result being overridden by mistake. Full
build detail: `autotrade_runner.py`, `autotrade_signals.py`, and the
project's main vault note [[Autotrade (Experimental)]] (in the trading-bot
vault, not this one — this is the Kronos-specific angle on the same work).

**Kronos's role in autotrade:** `autotrade_signals.compute_live_kronos_hourly()`
reuses `kronos_ic_hourly.kronos_forecast_at()` directly (just called at
"now" instead of a historical checkpoint) — no new Kronos-calling code was
written for this, the walk-forward and live paths share the exact same
function.

## 2026-07-23 — Walk-forward backtest: no measurable forecasting skill found

**The headline result.** Kronos was backtested honestly for the first time,
and it doesn't show edge. This is a real negative finding, not a bug —
recorded the same way the SMA-crossover rejection was, per the project's
"negative results get reported, not massaged" rule.

**Why the window is what it is.** Kronos's pretraining data extends to June
2024 (per the paper, arXiv:2508.02739 — quote: "the pre-training data for
Kronos extends up to June 2024" / "our test period for all tasks begins in
July 2024 to ensure a strict temporal separation between training and
evaluation"). Evaluating on anything before that risks scoring memorization
of the actual historical path, not forecasting skill. So July 2024 → now
(2026-07-23) is the *entire* honest test window available — 24 monthly
rebalances. Lookback context predating June 2024 is fine (real historical
prices, same as any live run conditions on); only the *forecasted* window
had to stay post-cutoff.

**Method:** built `KronosAI/kronos_backtest.py` — a real walk-forward sim
(no lookahead: at each historical month-end, Kronos only sees bars up to
that date). Two stages:
1. **Information coefficient** — pooled Spearman rank correlation between
   Kronos's predicted 20-trading-day return and the *realized* 20-day
   return, across every (date, ticker) pair. The cheap go/no-go check,
   recommended over jumping straight to a portfolio sim because it's far
   more statistically informative (hundreds of data points vs one curve).
2. **Portfolio backtest** — Kronos-ranked top-3 monthly rotation vs
   momentum's own ranking vs SPY buy-and-hold, on identical dates, run
   through the exact same `simulate_rotation()` engine
   `trader_app.momentum_backtest` uses (extracted into a shared function
   specifically so the comparison is apples-to-apples on cost/turnover).

**Results (sample_count=10, seed=42, single draw):**

Stage 1 — Information Coefficient:
- Pooled pairs: 304 (24 dates × 14 tickers, wherever both existed)
- Spearman IC: **0.036** (~zero)
- Directional hit rate: **50.0%** (coin-flip)

Stage 2 — Portfolio backtest [2024-07-31 → 2026-07-31]:

| Strategy | CAGR | Max DD | Sharpe |
|---|---|---|---|
| Kronos rotation | 20.99% | -9.30% | 1.09 |
| Momentum rotation | 59.07% | -15.60% | 1.73 |
| SPY buy&hold | 17.92% | -18.76% | 1.06 |

**Interpretation — read Stage 1 first.** Taken alone, Stage 2 looks
tempting: Kronos beat SPY on both return and drawdown. But Stage 1 says
that has to be read as noise, not skill — with an IC this close to zero and
a hit rate at exactly 50%, there's no detectable predictive signal driving
the ranking. A 24-decision portfolio sim (picking 3 of 14 tickers each
month) can easily land on a decent-looking curve by chance alone. This is
exactly why the two-stage design exists: Stage 2 alone would have been
misleading on its own. Against momentum rotation (the strategy actually
running on paper), Kronos isn't remotely competitive either way.

**Bugs hit and fixed getting here (real, not hypothetical):**
- `pandas.Series.corr(method="spearman")` silently requires `scipy`
  internally — not documented anywhere obvious, and scipy wasn't installed.
  This wasn't caught until *after* the full 24-date walk-forward loop
  completed (~25-30 min of Kronos inference), because the crash happened at
  the final stats step with nothing checkpointed yet — the run's entire
  output was lost. Fixed two ways: (1) replaced the IC calculation with a
  scipy-free rank-based Pearson correlation (mathematically identical to
  Spearman, no new dependency), (2) added a checkpoint
  (`kronos_backtest_checkpoint.json`, `--from-checkpoint` to reload) saved
  immediately after the forecasting loop, before any further processing —
  so a crash in the (cheap) reporting stage can never cost the expensive
  part again.

**What changed in the code as a result:** `trader_app.py`'s menu item 7 and
`paper_trader.py --signal kronos` both now say "backtested — no measurable
edge found (IC 0.036, 50% hit rate)" instead of "unvalidated." Kronos stays
wired in as an opt-in signal (useful for re-testing with a different seed,
or after any future model/prompt changes) — it's just no longer accurate to
call it merely untested.

**Caveats on this result:**
- Single sampling draw (seed 42, sample_count=10). Kronos samples
  stochastically; a different seed would shift the numbers somewhat, though
  an IC this flat and a hit rate at exactly 50% would be a surprising thing
  for a different seed to reverse.
- Small sample by construction — 24 rebalances is the entire honest window,
  not a choice; this is a real methodological floor, not a shortcut taken
  for convenience.

**Natural next step, if revisited:** re-run with 2-3 different seeds to
confirm the IC finding is stable before considering this fully closed —
but there's no urgency, since the result is unambiguous as-is.

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

## 2026-07-25 — routine re-run, no code change

Re-ran `kronos_watchlist_forecast.py` against the full 14-ticker watchlist
(`sample_count=10`, 20 trading days ahead) at the owner's request. Notable
output: ASML predicted -37.4%, a much larger move than anything else in the
set. Flagged to the owner as a reason for *more* suspicion, not more
conviction — given the measured IC (~0.03 daily, ~-0.08 hourly, both
statistically no signal), a larger predicted move isn't more trustworthy
than a small one, it's just a noisier draw from the same no-edge
distribution. Analysis-only, as always; no orders placed, nothing to
journal.

## Related Notes

- [[Kronos Overview]]
- [[00 MOC - Kronos Vault]]
