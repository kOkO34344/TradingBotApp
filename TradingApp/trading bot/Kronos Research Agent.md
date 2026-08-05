---
tags: [research, kronos, agent, workflow]
source: KronosAI/kronos_agent.py
status: "THE project signal (owner decision 2026-07-28) — no measured edge on ANY asset class (all four screened + failed 2026-08-03)"
last_updated: 2026-08-05
---

# Kronos Research Agent

> [!important] Kronos is now the project's main signal (2026-07-28)
> Owner decision. It is the default everywhere — `paper_trader.py` with no
> flags, `trader_app.py` menu 7, and `autotrade_runner.py`. Momentum is gated
> off (see [[Strategy Decisions - Momentum Rotation]]).
>
> **Nothing below has changed about the evidence.** Kronos still shows no
> measurable forecasting skill in the only honest post-cutoff window
> available, and it scored *worse* than the momentum baseline on the hourly
> IC screen. Being the focus is a research direction, not a result.

> [!warning] The top-3 is NOT stable — corrected 2026-07-28
> A previous note here claimed sampling noise leaves top-N rotation
> unaffected. That was wrong. Two `paper_trader.py --dry-run` runs ~30
> minutes apart, **identical closed-market data**, same `sample_count`,
> produced different top-3s: `[AMZN, MSFT, GOOGL]` then `[AMZN, MSFT, DIS]`.
> GOOGL and DIS sit ~1 point apart and simply swapped ranks 3/4; 6 of 14
> tickers changed rank.
>
> The consequence is not cosmetic: run 1 proposed BUY MSFT + BUY GOOGL
> (~$50k) and SELL DIS; run 2 proposed BUY MSFT only and HOLD DIS. **Which
> trades get placed depends on which sampling draw you happened to run.**
> Top-N is only stable when the rank-N/N+1 gap is wide relative to the
> sampling spread; near a cluster it is a coin flip.
>
> **Interim rule (no code needed):** before approving a Kronos rebalance,
> check the gap between rank N and N+1. If it's ~1 point or less, re-run once
> and only rotate on names present in both draws.
>
> **Proper fix:** measure the sampling SD across ~20 runs on frozen data
> first, then consider rotation hysteresis — and treat that as the strategy
> change it is, with an honest backtest. Plan in `Handoff.md`.

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

## Per-asset-class IC screens, 2026-08-03 — all four failed

The rule 9 gate for the FTMO venue: Kronos may only trade an asset class that
has passed its own IC screen. All four were screened
(`KronosAI/kronos_ic_assetclass.py`), on daily bars with the same LOOKBACK=400 /
PRED_LEN=20 counts, the same 2024-07-01 pretraining cutoff and the same matched
momentum baseline as the stock test — so these sit on the same scale as the
evidence above.

| class | Kronos date-wise IC | t | hit rate | momentum IC | verdict |
|---|---|---|---|---|---|
| stocks | +0.036 (pooled, 07-23) | — | 50.0% | — | no skill |
| indices | +0.068 | +0.89 | 39.6% | -0.103 | **FAILED** |
| FX (CME futures) | -0.138 | -1.55 | 48.4% | -0.002 | **FAILED** |
| commodities | -0.053 | -0.63 | 49.6% | +0.070 | **FAILED** |
| crypto | +0.103 | +1.34 | 50.4% | -0.013 | **FAILED** |

**No |t| exceeded 1.55 in either direction.** This is not "close" — it is four
independent nulls. The matched momentum baseline failed all four as well, so it
is not Kronos losing to a better alternative: *nothing* works on any of these
classes at this cadence. Combined with stocks, Kronos now has **no demonstrated
edge on any asset class this project has ever measured.**

**Judge a class on the DATE-WISE IC, never on pooled.** Pooled date×ticker pairs
share a market move, so pooled n wildly overstates independence. Not
theoretical: indices pooled at **+0.181**, which under this project's older
reporting style would have read as the first positive signal it ever found —
the date-wise t is +0.89, i.e. noise. FX is starker, pooled **+0.042** against
date-wise **-0.138**, disagreeing in *sign*. If the two disagree, believe the
date series.

**FX is screened on CME futures, not spot pairs.** yfinance reports volume as
identically zero for all ten spot FX pairs, and Kronos conditions on volume, so
a spot screen would have scored the model on a dead input and returned a
confident artifact. Any class whose volume is dead is flagged UNRELIABLE and
must be read as *unscreened*, not as a negative result.

A failed screen is **not** "needs a better configuration". Re-running with
different tickers or sample counts until one passes is exactly the parameter
tuning the honest-backtesting rule forbids. A class gets re-screened when there
is a genuinely new reason — different cadence, different data source, a fixed
defect in the input — and the re-run is recorded either way.

Consequence for [[FTMO Venue]]: **nothing may be enabled, and the venue is
cleared to trade nothing at all.**

## Live forecast re-run, 2026-07-27 — two observations worth keeping

Re-ran `kronos_watchlist_forecast.py` on all 14 watchlist tickers (20 trading
days ahead, `sample_count=10`, ~81s on MPS). Top-3: **AMZN +8.5%, MSFT +6.9%,
DIS +6.4%**; bottom: AAPL -14.6%, JNJ -14.8%, ASML -37.4%.

**1. Kronos's ranking looks a lot like a momentum ranking.** Against the
hourly momentum ranking from `autotrade_runner.log` (2026-07-25):
**Spearman 0.916**, Pearson 0.825, with the bottom six (KO, JPM, XOM, AAPL,
JNJ, ASML) in *exactly* the same order and 2 of 3 top names shared.

This is a **hypothesis, not a finding** — one snapshot, n=14, two different
cadences (hourly 400-bar vs daily 400-day) two days apart. It is deliberately
NOT in CLAUDE.md's Empirical Findings, which requires a real evidence bar.
But it is worth testing properly, because if it holds, Kronos is an expensive
momentum proxy: ~81s of GPU inference to land where a trailing-return sort
already was — and it scored *worse* than that sort on the hourly IC screen
(-0.081 vs -0.037). **Proper test:** compute that rank correlation across the
~24 rebalance dates the walk-forward backtest already covers.

**2. Run-to-run variance is large enough to matter for individual names.**
Three consecutive runs on *identical* data put GOOGL at **+2.69%, -3.72%,
+4.38%** — an 8-point spread, at the `sample_count=10` default. The top-3 was
stable across all three runs, so a top-N rotation is not affected, but no
individual Kronos number should be read as meaningful on its own. This is a
sharper version of the `sample_count` finding above.

**On the ASML -37.4% outlier:** verified *not* a data artifact — clean bars,
no split, and the stock is genuinely +151% over 12 months (595 → 1803). The
model is calling violent mean-reversion after that run. A sustained -2.3%/day
for 20 straight days is an extraordinary prediction and the single best
reason to be wary of these numbers.

## Related Notes

- [[Research Agent Workflow]] — the other research agent (LLM-based)
- [[ADR - Python Rules, Not Model Predictions]] — why this integration is safe: Kronos proposes, code + human still gate everything
- [[Phase Milestones Dashboard]] — where this fits in the phase picture
- [[00 MOC - Trading Bot Vault]]
