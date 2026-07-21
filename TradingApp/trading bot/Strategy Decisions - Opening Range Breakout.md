---
tags: [strategy, day-trading, research, rejected]
status: "smoke test only, not validated"
decision_date: 2026-07-18
source: orb_backtest.py, day_trader_research.md
---

# Strategy Decision: Opening Range Breakout (ORB)

## Executive Summary

**Status:** ❌ **SMOKE TEST ONLY** — The one day-trading strategy with published academic evidence (Zarattini & Aziz 2023, reported 33% annualized alpha). Implementation backtested on 60 days of real 5-minute QQQ data: **-12.6% over 60 trading days, 23% win rate, profit factor 0.66.**

**Key finding:** One 60-day window proves nothing either way — the strategy is built around rare huge winners and none landed in this window. But that's exactly the point: **even the best-published day-trading strategy has losing months that would shake out anyone undercapitalized or unprepared.**

## The Rule (from Zarattini & Aziz 2023)

- **Timeframe:** 5-minute bars (intraday only)
- **Asset:** QQQ (Nasdaq 100)
- **Entry:** If first 5-min bar closes up → go long at open of 2nd bar. If down → go short. Doji (open==close) → no trade.
- **Stop:** Opposite extreme of the first 5-min bar (e.g., for a long, stop at that bar's low)
- **Target:** 10R profit target (10× the entry-to-stop distance). Exit at close if neither hit.
- **Risk:** 1% of account per trade
- **Commission:** Assumed equivalent to typical spreads

## Results

### Zarattini & Aziz (2023) — the published claim
- **Period:** 2016–2023 (8 years)
- **Result:** ~33% annualized alpha net of commissions
- **Note:** "The only day-trading strategy I found with transparent, published, rule-based results over multiple market regimes."

### Our smoke test — 60 trading days (most recent free data available)
- **Period:** ~2 months of real 5-minute QQQ bars (latest available)
- **Result:** -12.6% over 60 trading days
- **Win rate:** 23%
- **Exit breakdown:**
  - Stops hit: 46
  - Profit targets hit: 1
  - Exit at close: remaining
- **Profit factor:** 0.66 (total winning $ / total losing $ — needs to be > 1.0)

## Why the Gap?

Three reasons the 60-day real result looks nothing like the paper:

1. **The strategy's edge is rare, huge winners.** It sits and waits for intraday breakouts with 10R targets. In the 60-day window, almost every entry got stopped out (46/~50 trades) and only 1 hit the 10R target. The paper's 33% annual result assumes a distribution of huge winners across a longer period — this 60-day window just missed them.

2. **Free 5-minute data is a constraint.** Proper validation needs multi-year intraday history (available via Alpaca data subscription or similar). Free data goes back only ~60 days.

3. **Intraday regime changes week-to-week.** The paper's 8-year test saw different volatility regimes, Fed cycles, sector rotations. 60 days is two months of sideways/down market — not representative.

## Decision: Why Not Use This

1. **Data depth.** Can't fairly validate a 5-min strategy on 60 days. Would need to pay for Alpaca's historical intraday data or similar.

2. **Capital requirement.** Day trading under Pattern Day Trader rules (US margin accounts <$25k) is limited to 3 trades per 5 rolling days. The strategy would easily hit that limit. A $25k+ account or cash account (no margin) is required for serious day trading.

3. **Better evidence exists for slower strategies.** Momentum rotation (monthly, validated on 7+ years) has as much edge with a much lower capital barrier and simpler mechanics.

4. **Day trading base rates are brutal.** Academic studies (Brazilian futures markets, US retail brokerage data) show ~97% of persistent day traders lose money. Even if this strategy's 33% claim is real, it's in a tiny tail of edge, and that edge could easily be regime-dependent (only worked in 2016–2023).

## The Bigger Picture

This isn't a slam on ORB or the paper. It's a reminder that:

- **Even the best-published day-trading evidence has months-long drawdowns.** If you enter into live day trading expecting a "money printer," you'll blow up the account in a losing streak before the edge recovers.
- **Skill matters more than rules.** The traders with audited 8-figure track records (Grittani, Dux, Cameron) don't use published, rule-based strategies. They trade low-float small-caps via tape reading (discretionary, non-automatable). Their edge is microstructure + niche market + years of experience, not a pattern you can code.
- **The lane that IS automatable is slower.** Momentum rotation, factor investing, mean-reversion on slower timeframes — these have published academic edge and are way easier to paper-test and live-trade with small capital.

## Validation Checklist

- [x] Paper's rule set understood and implemented
- [x] Smoke test on available data (60 days free)
- [ ] Multi-year backtest (needs paid data subscription)
- [ ] Walk-forward test on different volatility regimes
- [ ] Live paper trading (would require careful capital management)

**Status:** Research complete, validation incomplete, **not a candidate for Phase 3 given the evidence and constraints.**

## If You Wanted to Pursue This

1. **Subscribe to Alpaca's historical intraday data** (not free) to get 2–5 years of 5-min bars
2. **Run a proper walk-forward test** to see if the 33% holds across different market conditions
3. **Fund a $25k+ account** (or trade in a cash account, no margin) to avoid PDT limits
4. **Expect 2–3 month losing streaks** and be emotionally/capital-wise prepared for them
5. **Keep the position size tiny** — a day-trading loss spiral can wipe an account fast

## Related Notes

- [[Backtest Results & Findings]] — full day-trading research section (TJR/ICT school, audited traders, why day trading isn't the lane)
- [[Strategy Decisions - Momentum Rotation]] — the strategy we *are* pursuing instead
- [[ADR - Python Rules, Not Model Predictions]] — why even a rigged strategy is better than an agent trying to "predict" intraday moves

## Files

- `orb_backtest.py` — the implementation
- `orb_trades.csv` — trade-by-trade results from the 60-day test
- `day_trader_research.md` — full research on the day-trading landscape
