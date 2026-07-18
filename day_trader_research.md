# What the big day traders actually do — research findings

## TJR and the ICT/Smart Money school

TJR trades "Smart Money Concepts" descended from ICT (Michael Huddleston's Inner Circle Trader): mark the Asia/London session highs and lows as "liquidity pools," wait for price to sweep those levels (running the stops sitting there), confirm with a break of structure, then enter on a retracement into an order block or fair value gap, targeting the opposite liquidity pool.

The honest assessment: **there is no rigorous public evidence this framework has edge.** The claimed backtests come from fans and indicator vendors, not audited accounts or peer-reviewed studies; critics' core objection — that you cannot actually infer institutional order flow from candlestick shapes alone, so the narrative is unfalsifiable — has never been convincingly answered. Two structural red flags apply to most of this school: the concepts are defined loosely enough that any losing trade can be explained away in hindsight ("that wasn't a *valid* sweep"), and the teachers' primary verified income stream is courses, prop-firm affiliate deals, and content — not audited trading returns. That doesn't prove TJR loses money trading; it means you can't verify he makes it, and an unverifiable strategy can't be your foundation.

What IS worth taking from this school: trade only during high-liquidity sessions, place stops beyond obvious levels rather than at them, and predefine risk per trade. Those are real practices — and they're risk management, not prediction.

## Traders with actually audited records

Three US small-cap traders have third-party-verified profits: **Tim Grittani** (~$1.5k to eight figures, documented trade-by-trade on Profit.ly), **Steven Dux** (>$11M, audited by an accounting firm), and **Ross Cameron** (>$10M, audited). Two things matter about them:

1. **Their edge is not a chart pattern.** All three trade low-float, high-volatility small-cap stocks — pump-and-dump mechanics, short squeezes, dilution — where retail order flow is dumb enough and institutions absent enough that edge can exist. Grittani and Dux are substantially short sellers of manipulated stocks. This is a niche with real barriers: hard-to-borrow fees, halts, liquidity that evaporates, and account minimums. Their strategies are also heavily discretionary — tape reading, experience-based judgment — which is precisely what doesn't translate into code.
2. **They are survivors of a brutal distribution.** Academic studies (the well-known Brazilian futures study and US brokerage data) consistently find ~97% of persistent day traders lose money. The audited winners are real, and so is the base rate they beat.

## The one day-trading strategy with published evidence: ORB

Zarattini & Aziz's paper "Can Day Trading Really Be Profitable?" (SSRN, 2023) backtested a mechanical 5-minute **Opening Range Breakout** on QQQ, 2016–2023: trade in the direction of the first 5-minute candle, stop at that candle's opposite extreme, 10R profit target, 1% account risk per day, exit at close. Reported result: ~33% annualized alpha net of commissions — the only day-trading strategy I found with transparent, published, rule-based results over multiple market regimes.

**I implemented the paper's exact rules and ran them on the most recent 60 days of real 5-minute QQQ data** (free intraday data doesn't go back further): **-12.6%** over 60 trading days, 23% win rate, 46 stop-outs, 1 target hit, profit factor 0.66. One 60-day window proves little either way — the strategy is designed around rare huge winners, and none landed in this window — but that's exactly the point: even the best published day-trading strategy has months-long losing stretches that would shake out anyone undercapitalized or unprepared. It's regime-dependent, not a money printer.

## Strategy family shootout (our 10 tickers, out-of-sample 2019–now, after costs)

| Family | Avg CAGR | Avg max DD | Avg Sharpe | Beats buy & hold |
|---|---:|---:|---:|---:|
| SMA 20/50 (trend) | 6.1% | -34.0% | 0.27 | 1/10 |
| Golden cross 50/200 (slow trend) | 8.5% | -36.1% | 0.33 | 1/10 |
| Donchian 20/10 (breakout) | 6.3% | -31.1% | 0.33 | 1/10 |
| RSI-2 (mean reversion) | 2.4% | -17.6% | 0.25 | 1/10 |
| **Momentum top-3 rotation (monthly)** | **16.6%** | **-21.6%** | **0.87** | — |
| Buy & hold (avg) | 16.9% | -42.4% | — | — |
| SPY | 17.3% | -33.7% | 0.92 | — |

**Momentum rotation is the only family that's competitive** — nearly matching the index return with a third less drawdown. This is consistent with the academic literature, where cross-sectional momentum is among the most robust documented anomalies. Every single-ticker technical rule underperformed, no matter the family. The ORB smoke test lost money in its window.

## What this means for the project

The evidence points away from "find the pattern that predicts price" and toward three usable conclusions. First, the only codifiable edges with real evidence are slow (monthly momentum rotation, factor investing) — which fits the swing/position-trading lane the original plan recommended anyway. Second, everything the verified traders agree on is risk discipline: fixed risk per trade, hard stops, daily loss limits — all now implemented in the app's risk engine and all things that go in code, not prompts. Third, fast discretionary day trading of the Grittani/Dux type is a skill business built on niche market microstructure and years of screen time; it is not automatable by us, and anyone selling the automated version of it is selling the dream, not the edge.

Recommended next build step: make momentum rotation a first-class strategy in the app (portfolio-level, monthly rebalance, with the risk engine's drawdown circuit breaker), and consider a proper multi-year ORB validation later only if you get access to deep intraday history (e.g., through Alpaca's data subscription).

## Files

- `orb_backtest.py` — the paper's ORB rules, runnable; writes `orb_trades.csv`
- `strategy_shootout.py` — all strategy families, one command
- `trader_app.py` — updated: Settings menu option 6 toggles the risk engine (200-day trend filter + 2×ATR trailing stop + fixed % risk per trade)
