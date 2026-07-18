---
tags: [trading-bot, research, backtest]
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-18
---

# Backtest Results & Day-Trading Research — Findings

Synthesized from `backtest_report.md` and `day_trader_research.md` in `/Users/kaloyanivanov/TradingBotApp`. This is the evidence behind the status notes on [[trading bot/Plan]] and [[trading bot/The App]].

## 1. The SMA 20/50 crossover — rejected

**Setup:** long-only, buy when SMA(20) crosses above SMA(50), sell on cross-under. One ticker at a time, no shorting, no leverage. 10 tickers (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS), benchmarked against buy-and-hold (same stock) and SPY. Cost: 0.1%/trade. Data: daily bars, Yahoo Finance, split/dividend-adjusted.

**Out-of-sample (2019–present, includes the 2020 crash, 2022 bear market, and the recent bull run):**

| Ticker | Strategy CAGR | Buy & hold (same stock) | SPY buy & hold | Sharpe | Max drawdown | Trades |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 16.4% | 33.7% | 17.3% | 0.67 | -28.5% | 20 |
| MSFT | 3.5% | 20.9% | 17.3% | 0.17 | -40.1% | 20 |
| GOOGL | 15.6% | 28.5% | 17.3% | 0.59 | -41.3% | 18 |
| AMZN | -2.3% | 16.7% | 17.3% | -0.10 | -55.5% | 21 |
| JPM | 12.8% | 21.1% | 17.3% | 0.59 | -23.6% | 18 |
| JNJ | 4.4% | 12.6% | 17.3% | 0.32 | -22.6% | 21 |
| PG | -3.4% | 9.6% | 17.3% | -0.29 | -32.0% | 23 |
| XOM | 5.3% | 15.5% | 17.3% | 0.23 | -34.2% | 20 |
| KO | 3.4% | 10.9% | 17.3% | 0.22 | -24.1% | 21 |
| DIS | 5.7% | -0.9% | 17.3% | 0.28 | -37.8% | 19 |

**Beat SPY: 0/10. Beat own buy-and-hold: 1/10** (DIS only, and only because DIS lost money over the period anyway). Average strategy CAGR **6.1%** vs. average buy-and-hold **16.9%** vs. SPY **17.3%**. In-sample (2010–2018) tells the same story — 0/10 beat their own buy-and-hold, 3/10 beat SPY. Not an out-of-sample fluke.

**Why it loses:** it trades real transaction costs against liquid large-caps in a historically strong bull market — every whipsaw costs 0.1% twice (20–29 trades per ticker over the period), it's flat in cash during some of the market's best up-days (which cluster right after big down-days), and plain moving-average crossovers are extremely well-known, arbitraged-away signals with no real edge left in liquid large-caps. The one place it did its job: max drawdown was smaller than buy-and-hold's in 8/10 tickers — it does step aside during sustained downtrends, it just gives back more in cost and missed upside than it saves in avoided pain.

**Verdict per the plan's own Phase 2 exit criteria** ("if it doesn't beat buy-and-hold after costs out-of-sample, it's not ready"): **does not advance to Phase 3.** Don't fix this by tuning parameters until a backtest looks good — that's overfitting, not a strategy.

## 2. Strategy family shootout — what actually has evidence

Same 10 tickers, out-of-sample 2019–present, after costs. Five rule families tested head-to-head:

| Family | Avg CAGR | Avg max DD | Avg Sharpe | Beats buy & hold |
|---|---:|---:|---:|---:|
| SMA 20/50 (trend) | 6.1% | -34.0% | 0.27 | 1/10 |
| Golden cross 50/200 (slow trend) | 8.5% | -36.1% | 0.33 | 1/10 |
| Donchian 20/10 (breakout) | 6.3% | -31.1% | 0.33 | 1/10 |
| RSI-2 (mean reversion) | 2.4% | -17.6% | 0.25 | 1/10 |
| **Momentum top-3 rotation (monthly)** | **16.6%** | **-21.6%** | **0.87** | — |
| Buy & hold (avg) | 16.9% | -42.4% | — | — |
| SPY | 17.3% | -33.7% | 0.92 | — |

**Only momentum rotation is competitive** — nearly matching the index return with a third less drawdown than the average buy-and-hold position. This lines up with the academic literature, where cross-sectional momentum is among the most robust documented anomalies. Every single-ticker technical rule underperformed, regardless of family.

## 3. Day-trading landscape research — why day trading isn't the lane to automate

**TJR / ICT ("Smart Money Concepts") school:** marks session highs/lows as "liquidity pools," waits for a sweep, confirms a break of structure, enters on a retracement. **No rigorous public evidence this has edge.** Claimed backtests come from fans and indicator vendors, not audited accounts. Core objection — you can't actually infer institutional order flow from candlestick shapes alone — has never been convincingly answered, and the concepts are loose enough that any losing trade can be explained away in hindsight. The teachers' verified income is courses and affiliate deals, not audited trading returns. What IS worth keeping from this school: trade only during high-liquidity sessions, place stops beyond obvious levels, predefine risk per trade — real practices, but that's risk management, not prediction.

**Traders with actually audited records:** Tim Grittani (~$1.5k → eight figures, documented on Profit.ly), Steven Dux (>$11M, accounting-firm audited), Ross Cameron (>$10M, audited). Their edge is **not a chart pattern** — all three trade low-float, high-volatility small-caps (pump-and-dump mechanics, short squeezes, dilution) where retail order flow is dumb enough and institutions absent enough for edge to exist. Their methods are also heavily discretionary (tape reading, experience-based judgment) — precisely what doesn't translate into code. They're also survivors of a brutal distribution: academic studies (the Brazilian futures study, US brokerage data) consistently find **~97% of persistent day traders lose money.**

**The one day-trading strategy with published evidence — Opening Range Breakout (ORB):** Zarattini & Aziz, "Can Day Trading Really Be Profitable?" (SSRN, 2023) — mechanical 5-minute ORB on QQQ, 2016–2023, reported ~33% annualized alpha net of commissions. The paper's exact rules were implemented (`orb_backtest.py`) and run on the most recent 60 days of real 5-minute QQQ data (free data doesn't go back further): **result was -12.6% over 60 trading days, 23% win rate, 46 stop-outs, 1 target hit, profit factor 0.66.** One 60-day window proves little either way — the strategy is built around rare huge winners and none landed in this window — but that's the point: even the best-published day-trading strategy has months-long losing stretches that would shake out anyone undercapitalized or unprepared.

## 4. What this means for the project

- The evidence points away from "find the pattern that predicts price" and toward slow, codifiable edges — monthly momentum rotation, factor investing — which fits the swing/position-trading lane [[trading bot/Plan]] recommended from the start.
- Everything the verified traders agree on is risk discipline (fixed risk per trade, hard stops, daily loss limits), not signal generation — that belongs in code, not in a prompt. A first version (trend filter + 2×ATR trailing stop + fixed-fractional sizing) is already implemented as the app's "risk engine" toggle — see [[trading bot/README_trader_app]].
- Fast discretionary day trading (the Grittani/Dux type) is a skill business built on niche market microstructure and years of screen time. It is not automatable as a first project, and anyone selling "the automated version" of it is selling the dream, not the edge.

**Recommended next build step:** make momentum rotation a first-class, portfolio-level strategy with the risk engine's drawdown circuit breaker wired in (already scaffolded — `trader_app.py` menu option 5), rather than continuing to iterate on single-ticker technical rules. A proper multi-year ORB validation is worth revisiting only with access to deep intraday history (e.g., via an Alpaca data subscription) — the 60-day free-data test wasn't long enough to judge it fairly either way.

## Known limitations of the current results (so "done" is calibrated correctly)

- SMA backtest tests each ticker independently at full account size, not as a shared portfolio under the plan's "max 5 positions" constraint — needs a separate portfolio-level simulator.
- No walk-forward re-optimization — SMA parameters were fixed at 20/50 throughout, deliberately, to avoid overfitting to this dataset.
- Data is Yahoo Finance daily bars, not Alpaca's own — fine for this validation, but Phase 3 should pull from Alpaca directly since that's the execution venue.
- The ORB test is a 60-day mechanics smoke test on free intraday data, not a multi-year validation — treat the -12.6% result as "even the best case isn't easy," not as a final verdict on ORB.

## Source files (in `/Users/kaloyanivanov/TradingBotApp`)

- `sma_crossover_backtest.py`, `backtest_results.csv`, `backtest_report.md` — SMA crossover backtest and results
- `strategy_shootout.py` — runs all 5 strategy families in one command
- `variant_experiments.py` — risk-management variants of the SMA crossover (baseline / trend filter / ATR stop / fully risk-sized)
- `orb_backtest.py`, `orb_trades.csv` — Opening Range Breakout smoke test
- `day_trader_research.md` — full day-trading landscape write-up
