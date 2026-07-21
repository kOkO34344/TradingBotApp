---
tags: [strategy, rejected, backtest]
status: "rejected"
decision_date: 2026-07-18
source: sma_crossover_backtest.py, strategy_shootout.py
---

# Strategy Decision: SMA 20/50 Crossover

## Executive Summary

**Status:** ❌ **REJECTED** — does not beat buy-and-hold after costs on out-of-sample data. Per [[Plan]], does not advance to Phase 3.

**Key metric:** Beat SPY in **0/10** tickers out-of-sample (2019–present). Beat own buy-and-hold in **1/10** (DIS only, and only because DIS itself lost money).

## The Rule

- **Entry:** Long when SMA(20) crosses above SMA(50)
- **Exit:** Close position when SMA(20) crosses back below SMA(50)
- **Scope:** One ticker at a time, no shorting, no leverage
- **Cost model:** 0.1% per trade (approximates spread + slippage on Alpaca)
- **Benchmark:** SPY buy-and-hold + holding the same stock (buy-and-hold)

## Results

### Out-of-sample (2019–present) — **the one that matters**

| Ticker | Strategy CAGR | Buy & hold | SPY | Sharpe | Max DD | Trades |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 16.4% | 33.7% | 17.3% | 0.67 | -28.5% | 20 |
| MSFT | 3.5% | 20.9% | 17.3% | 0.17 | -40.1% | 20 |
| GOOGL | 15.6% | 28.5% | 17.3% | 0.59 | -41.3% | 18 |
| AMZN | **-2.3%** | 16.7% | 17.3% | -0.10 | **-55.5%** | 21 |
| JPM | 12.8% | 21.1% | 17.3% | 0.59 | -23.6% | 18 |
| JNJ | 4.4% | 12.6% | 17.3% | 0.32 | -22.6% | 21 |
| PG | **-3.4%** | 9.6% | 17.3% | -0.29 | -32.0% | 23 |
| XOM | 5.3% | 15.5% | 17.3% | 0.23 | -34.2% | 20 |
| KO | 3.4% | 10.9% | 17.3% | 0.22 | -24.1% | 21 |
| DIS | 5.7% | **-0.9%** | 17.3% | 0.28 | -37.8% | 19 |

**Averages:**
- Strategy CAGR: **6.1%**
- Buy-and-hold CAGR: **16.9%**
- SPY CAGR: **17.3%**
- Sharpe: 0.27 (SPY: 0.92)

### In-sample (2010–2018) — **confirms it's not a fluke**

Same story: 0/10 beat their own buy-and-hold, only 3/10 beat SPY. Average strategy CAGR 7.1% vs. average B&H 12.8%.

## Why It Lost

**Three structural reasons:**

1. **Trades real costs against liquid large-caps in a strong bull market.** 20–29 trades per ticker over 7–16 years, each costing 0.1% twice (entry + exit). AAPL had 20 trades — that's $20 in costs alone on a $10k account, plus spread/slippage in execution.

2. **It's flat in cash during some of the market's best up-days.** The days after big down-days (when momentum is strongest) are when moving averages whipsaw the hardest. The strategy exits into cash right before the bounce, then re-enters late.

3. **Plain moving-average crossovers are arbitraged-away signals.** They're well-known, cheap to implement, and have no real edge left in liquid large-caps trading against algorithms.

**One thing it did right:** max drawdown was smaller than buy-and-hold's in 8/10 tickers — it does step aside during sustained downtrends. It just gives back more in costs and missed upside than it saves in avoided pain.

## Variants Tested

The "risk engine" experiment (`variant_experiments.py`) added three hardening layers:
- **V2:** Trend filter (only enter when Close > SMA200)
- **V3:** 2×ATR trailing stop
- **V4:** Both + fixed-fractional position sizing (so a stop-out loses ~2% of equity)

Result: V4 brought average CAGR up from 6.1% to ~7.8% and reduced average max drawdown from -34% to ~-30%. Still lost to buy-and-hold in 8/10 tickers. Hardening a broken signal doesn't fix the underlying problem.

## Decision Rationale

Per [[Plan]]'s Phase 2 exit criteria:

> "If it doesn't beat buy-and-hold after costs on out-of-sample data, it's not ready — iterate or pick a different strategy, don't skip to live."

This strategy failed that test. The options:

1. **Accept this as expected** — it's not a coding problem. Simple technical rules on liquid large-caps generally don't have edge. Parameter-tuning until a backtest looks good is overfitting, not a strategy.
2. **Redirect toward research/fundamentals + tactical execution** — the original plan flagged this as the more realistic lane.
3. **Iterate with a different hypothesis** — tested in [[Strategy Decisions - Momentum Rotation]], which passed.

**We chose option 3 and then option 2:** momentum rotation (the one that earned evidence) + a research agent that produces longer-horizon theses (the one that's built but ungraded).

## Related Notes

- [[Backtest Results & Findings]] — full comparison to other strategy families
- [[Strategy Decisions - Momentum Rotation]] — the strategy that *did* earn Phase 3 readiness
- [[ADR - Momentum over SMA]] — why this one was rejected and that one wasn't

## Files

- `sma_crossover_backtest.py` — the backtest
- `backtest_results.csv` — raw results, all periods
- `variant_experiments.py` — the risk-engine hardening variants
