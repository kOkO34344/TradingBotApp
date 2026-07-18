---
tags: [trading-bot, project-overview]
status: "Phase 2 complete — SMA crossover rejected, momentum rotation is the live candidate"
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-18
---

# Trading Bot Project — Overview

This is the index note for the trading-bot project. The actual code lives **outside the vault**, at `/Users/kaloyanivanov/TradingBotApp` on this Mac — these notes are a synced summary of what's there, kept accurate as the project moves forward.

## Where things stand right now

**Phase 2 (backtesting) is done, and the result was a real finding, not a bug:** the strategy named in the original plan (20/50-day SMA crossover) **does not beat buy-and-hold**, in-sample or out-of-sample, on any of the 10 test tickers. Per the plan's own exit criteria, it does **not** advance to Phase 3 (paper trading) as-is.

A follow-up strategy shootout tested five rule families head-to-head. Only one was competitive: **monthly momentum rotation** (hold the top 3 of 10 tickers by trailing 12-month return, rebalance monthly) — it nearly matched SPY's return with a third less drawdown. Everything else (trend, breakout, mean-reversion) lost to just holding the index.

Separately, research into how real day traders operate (TJR/ICT "smart money" school vs. audited traders like Grittani/Dux/Cameron vs. the one published mechanical strategy with real evidence, Opening Range Breakout) reinforced the plan's original instinct: **fast discretionary day trading isn't something to automate as a first project**; the only defensible codifiable edge found is the slow, monthly momentum rotation — which fits the swing/position-trading lane the plan recommended from day one.

→ Full detail, numbers, and sourcing: **[[trading bot/Backtest Results & Findings]]**

**No money has moved.** This app places no orders and connects to no broker. It's purely the Phase 2 (backtesting) layer — validating strategies before any Alpaca/live connection exists.

## Notes in this folder

| Note | What it's for |
|---|---|
| [[trading bot/Plan]] | The original 5-phase build plan (research agent → backtest → paper trading → tiny live capital), with a status banner showing progress against it |
| [[trading bot/README_trader_app]] | How to install and run `trader_app.py` on this machine, current menu, current file layout |
| [[trading bot/Backtest Results & Findings]] | The actual numbers: SMA crossover results, the 5-strategy shootout, day-trading research findings, and what they mean for what to build next |

## Current app snapshot

(from `trader_settings.json`, synced 2026-07-18)

- **Watchlist:** AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS — plus SPY as benchmark
- **SMA windows:** 20 / 50 (default, known to lose to buy-and-hold)
- **Cost model:** 0.1% per trade
- **Starting cash (sim):** $10,000
- **Risk engine:** off by default — togglable (trend filter + 2×ATR trailing stop + fixed % risk per trade)
- **Momentum rotation:** top 3, 12-month lookback — the strategy with the best evidence so far

## Recommended next build step

Make momentum rotation a first-class, portfolio-level strategy with the risk engine's drawdown circuit breaker wired in (already partially implemented — see `trader_app.py` menu option 5). Don't spend more time "fixing" the SMA crossover by tuning parameters until a backtest looks good — that's how you get an overfit result that means nothing out of sample.
