# Backtest results: 20/50-day SMA crossover

## Setup

- **Strategy:** long-only. Buy when SMA(20) crosses above SMA(50). Close the position when SMA(20) crosses back below SMA(50). One ticker at a time, no shorting, no leverage.
- **Universe:** AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS.
- **Benchmark:** buying and holding that same ticker, and buying and holding SPY, over the identical dates.
- **Costs:** 0.1% per trade (approximates spread + slippage; Alpaca itself charges no commission).
- **Periods tested:** in-sample 2010–2018, out-of-sample 2019–present (July 2026). Out-of-sample is the one that matters — it includes the 2020 crash, the 2022 bear market, and the recent bull run, none of which the strategy could have been tuned on.
- Data: daily bars from Yahoo Finance, split/dividend-adjusted.

## Result: it does not beat buy-and-hold

**Out-of-sample (2019–present):** the strategy beat SPY buy-and-hold in **0 of 10** tickers, and beat holding the same stock in **1 of 10** (DIS only — and only because DIS itself lost money over that stretch, so "not losing as much" counted as a win).

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

Average strategy CAGR: **6.1%**. Average buy-and-hold CAGR: **16.9%**. SPY: **17.3%**.

**In-sample (2010–2018):** same story — 0/10 beat their own buy-and-hold, only 3/10 beat SPY. This isn't an out-of-sample fluke; the rule underperforms in both periods.

## Why: this matches what the strategy comparison literature already told us

This isn't a bug — a plain moving-average crossover trading real transaction costs against a decade of large-cap tech and consumer staples in a historically strong bull market is expected to lose to just holding, because: it exits and re-enters during every whipsaw (20–29 trades per ticker over 7–16 years, each one paying the 0.1% cost twice), it's flat in cash during some of the market's best up-days (which cluster right after big down-days), and simple crossovers are extremely well-known, arbitraged-away signals with no real edge left in liquid large-caps.

The **max drawdowns are the one place it did its job**: for 8 of 10 tickers the strategy's drawdown was smaller than buy-and-hold's, because it does step aside during sustained downtrends. It just gives back more in cost and missed upside than it saves in avoided pain.

## Verdict per the plan's Phase 2 exit criteria

The plan said: *"If it doesn't beat buy-and-hold after costs on out-of-sample data, it's not ready — iterate or pick a different strategy, don't skip to live."*

It doesn't beat buy-and-hold. Per the plan, this strategy does not advance to Phase 3 (paper trading) as-is. Options from here, in order of how much I'd actually recommend them:

1. **Accept this as the expected result and don't force a "better" backtest.** Simple technical rules on liquid large-caps generally don't have edge — that's not a coding problem to solve by tweaking parameters until a backtest looks good (that's how you get overfitting, not a real strategy). If the goal is "beat the market," the honest base rate is that most attempts, including well-funded professional ones, fail at this.
2. **Redirect toward the research/fundamentals agent from Phase 1** as the primary product, and treat any trading component as tactical execution of a longer-horizon thesis rather than a standalone signal. This matches what the original plan flagged as the more realistic lane.
3. **If you want to keep iterating on rules-based trading**, the next legitimate step isn't "fix this strategy" — it's testing a different, more defensible hypothesis (e.g., something in the value/quality-factor family, or a specific documented anomaly) with the same rigor: in/out-of-sample split, cost-adjusted, benchmarked. I can run that same harness against a different rule if you want, but I'd be doing you a disservice if I kept tuning this one until an out-of-sample number happened to look good — that number wouldn't mean anything.

## What's in this deliverable

- `sma_crossover_backtest.py` — the full backtest script, runs cleanly end to end (verified: 30/30 ticker-period combinations ran without errors, trade counts and drawdowns sanity-checked).
- `backtest_results.csv` — raw results, all tickers, all three periods (full history / in-sample / out-of-sample).
- This report.

## Known limitations of this pass (so you know what "done" doesn't mean here)

- Each ticker is tested independently at full account size, not as a shared portfolio with the "max 5 positions" constraint from the plan — that needs a separate portfolio-level simulator, not something `backtesting.py` does natively.
- No walk-forward re-optimization (parameters were fixed at 20/50 throughout, deliberately, to avoid overfitting them to this specific dataset).
- Yahoo Finance daily bars, not Alpaca's own data — fine for this kind of validation, but Phase 3 should pull from Alpaca directly since that's the execution venue.
