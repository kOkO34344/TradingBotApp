---
tags: [trading-bot, project-overview]
status: "Phase 2 complete — SMA crossover rejected, momentum rotation is the live candidate. Phase 1 research agent now built (research_agent.py + grade_calls.py) but has produced zero real calls — only synthetic test data exists. IBKR read-only connection layer and an unevaluated third-party TradingAgents wrapper also added."
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-19
---

# Trading Bot Project — Overview

This is the index note for the trading-bot project. The actual code lives **outside the vault**, at `/Users/kaloyanivanov/TradingBotApp` on this Mac — these notes are a synced summary of what's there, kept accurate as the project moves forward.

## Where things stand right now

**Phase 2 (backtesting) is done, and the result was a real finding, not a bug:** the strategy named in the original plan (20/50-day SMA crossover) **does not beat buy-and-hold**, in-sample or out-of-sample, on any of the 10 test tickers. Per the plan's own exit criteria, it does **not** advance to Phase 3 (paper trading) as-is.

A follow-up strategy shootout tested five rule families head-to-head. Only one was competitive: **monthly momentum rotation** (hold the top 3 of 10 tickers by trailing 12-month return, rebalance monthly) — it nearly matched SPY's return with a third less drawdown. Everything else (trend, breakout, mean-reversion) lost to just holding the index.

Separately, research into how real day traders operate (TJR/ICT "smart money" school vs. audited traders like Grittani/Dux/Cameron vs. the one published mechanical strategy with real evidence, Opening Range Breakout) reinforced the plan's original instinct: **fast discretionary day trading isn't something to automate as a first project**; the only defensible codifiable edge found is the slow, monthly momentum rotation — which fits the swing/position-trading lane the plan recommended from day one.

→ Full detail, numbers, and sourcing: **[[trading bot/Backtest Results & Findings]]**

**Since that finding, several new pieces were added to the folder — none change the bottom line above:**
- **`research_agent.py` + `grade_calls.py`** — the project's own **Phase 1 research agent**, built to the original plan's spec (Claude Agent SDK, multi-timeframe indicators, structured logged thesis) plus its grading/calibration counterpart. This is real, working infrastructure. But: **`research_log/` contains exactly two files, both explicitly labeled `SYNTHETIC TEST NOTE — not a real call`.** The one grading report that exists (`graded_calls.csv`, 1/4 rows "correct") is a pipeline smoke test on fake data, not evidence of anything. Zero real ticker analyses have been run. A `knowledge/` library (verified-sources-only, distilled from this project's own backtest findings) now feeds every prompt.
- **`ibkr_service.py`** — a real connection layer to Interactive Brokers (stocks, forex, futures, crypto through one API), wired into the app as menu option 8. It's **read-only**: account summary, positions, live bars. A `place_market_order` function exists in the module but nothing calls it — no order can be placed through the app today.
- **`trading_agent_service.py`** — a wrapper around the third-party **TradingAgents** multi-agent LLM library (bull/bear debate → buy/hold/sell call). A second, competing candidate for Phase 1. **Also never run or evaluated** — no output exists to judge it by.

There are now **two unevaluated Phase 1 candidates** sitting side by side (`research_agent.py` vs `trading_agent_service.py`) — worth picking one deliberately rather than half-using both. See "Recommended next build step" below.

**No money has moved.** This app places no orders and connects to no broker capable of executing. It's purely the Phase 2 (backtesting) layer, now with a read-only data/monitoring layer and an unused research-agent layer alongside it — validating strategies and (eventually) theses before any order-placing connection exists.

## Notes in this folder

| Note | What it's for |
|---|---|
| [[trading bot/Plan]] | The original 5-phase build plan (research agent → backtest → paper trading → tiny live capital), with a status banner showing progress against it |
| [[trading bot/README_trader_app]] | How to install and run `trader_app.py` on this machine, current menu, current file layout |
| [[trading bot/Backtest Results & Findings]] | The actual numbers: SMA crossover results, the 5-strategy shootout, day-trading research findings, and what they mean for what to build next |

## Current app snapshot

(from `trader_settings.json`, synced 2026-07-19)

- **Watchlist:** AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS — plus SPY as benchmark
- **SMA windows:** 20 / 50 (default, known to lose to buy-and-hold)
- **Cost model:** 0.1% per trade
- **Starting cash (sim):** $10,000
- **Risk engine:** off by default — togglable (trend filter + 2×ATR trailing stop + fixed % risk per trade)
- **Momentum rotation:** top 3, 12-month lookback — the strategy with the best evidence so far
- **IBKR:** port 7497 (TWS paper), client id 9 — read-only, no orders wired

## Recommended next build step

Make momentum rotation a first-class, portfolio-level strategy with the risk engine's drawdown circuit breaker wired in (already partially implemented — see `trader_app.py` menu option 5). Don't spend more time "fixing" the SMA crossover by tuning parameters until a backtest looks good — that's how you get an overfit result that means nothing out of sample.

Two side paths now exist too, worth a deliberate decision rather than drifting into either:
1. **Delete the two synthetic notes and run `research_agent.py` for real** on a handful of tickers you know well — that's the only way to judge whether its reasoning is any good, and it's the more developed, purpose-built option vs. `trading_agent_service.py`. Once real notes accumulate, run `grade_calls.py --csv` weekly and watch the calibration report — that report, sustained over time, is what the plan actually means by "earning autonomy."
2. Pick one of `research_agent.py` / `trading_agent_service.py` rather than maintaining both indefinitely — they solve the same problem two different ways, and neither has evidence yet.
3. Leave `ibkr_service.py` alone (read-only) until a strategy has actually earned Phase 3 — connecting `place_market_order` to anything is a Phase 3 decision, not a Phase 2 one.

See [[trading bot/Plan]] for how these map onto the phase structure.
