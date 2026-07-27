---
tags: [strategy, validated, backtest, momentum]
status: "VALIDATED but DISABLED in code — owner decision 2026-07-28"
decision_date: 2026-07-18
source: strategy_shootout.py, trader_app.py (menu option 5)
---

# Strategy Decision: Momentum Rotation (Monthly)

## Executive Summary

**Status:** ✅ **VALIDATED** — only strategy family that competed with SPY on out-of-sample data (2019–present). **But it is DISABLED and will not run.**

> [!warning] Disabled by owner instruction, 2026-07-28
> Momentum does not run again until Koko explicitly asks for it in that
> session. Kronos is the project's main signal. Enforced in code by
> `signal_policy.py`, not by convention: `paper_trader.compute_signal()` and
> `autotrade_signals.compute_live_momentum_hourly()` raise `SignalDisabled`
> unless a caller passes `allow_momentum=True`, and every
> `.get("signal", ...)` fallback now resolves to `kronos` so config drift
> can't resurrect it. Same deliberate opt-in shape as `allow_live=True`.
>
> **This runs against the project's own evidence, deliberately and with the
> owner's knowledge — record it that way, don't rationalize it.** Momentum
> is still the only family that ever earned Phase 3; Kronos measured
> Spearman IC 0.036 / 50.0% daily and IC -0.081 / 46.4% hourly, i.e. the
> *enabled* signal scored worse than the disabled one on the only
> head-to-head screen. Kronos as the focus is a research direction, not a
> validated edge.
>
> To re-enable: `paper_trader.py --signal momentum --allow-momentum`.
> Backtest/research scripts (`strategy_shootout.py`,
> `broad_universe_momentum.py`) are deliberately **not** gated — they place
> no orders, and gating evidence-generation would defeat rule 4.

**Key metric:** **16.6% CAGR**, max drawdown **-21.6%** (SPY: 17.3% CAGR, -33.7% DD). Beat 10/10 tickers' buy-and-hold on average. Sharpe **0.87** (SPY: 0.92).

## The Rule

- **Selection:** Rank the 12-ticker watchlist by trailing 12-month return; hold the top 3 equal-weight
- **Rebalance:** Monthly, at the close on the last trading day of each month
- **Holdings:** If a top-3 name has negative 12-month return (dual momentum filter), optionally hold cash instead (risk-engine mode)
- **Exit all:** On a portfolio-level drawdown trigger (TBD, likely 2× daily ATR or a trend filter — not yet wired into the live system)
- **Cost model:** 0.1% per trade, turnover penalty applied on rebalance

## Results

### Out-of-sample (2019–present)

**Full watchlist momentum rotation (top 3, 12-month lookback):**
- CAGR: **16.6%**
- Max Drawdown: **-21.6%**
- Sharpe: **0.87**
- Months in cash (dual momentum): ~3–4 of 84

**Comparison:**
- Average buy-and-hold (10 original tickers): 16.9% CAGR, -42.4% DD
- SPY buy-and-hold: 17.3% CAGR, -33.7% DD

**Advantage:** Nearly as much return as SPY with a *third* less drawdown than average buy-and-hold. This lines up with academic literature: cross-sectional momentum is among the most robust documented anomalies.

## Why It Works

1. **Momentum is real.** Decades of academic research (Jegadeesh & Titman, Blitz/Hanauer, recent factor research) show that past winners tend to continue outperforming in the medium term (3–12 months), especially when you rebalance regularly to the freshest performers.

2. **Portfolio-level smoothing.** Holding 3 names diversifies away idiosyncratic risk. A single ticker can whipsaw; a momentum basket is more robust.

3. **Reduces large drawdowns.** By rotating *away* from last year's laggards, the strategy avoids sitting in the worst performers during the next phase. Dual momentum (skip cash losers) cuts drawdowns further.

4. **Works on this watchlist specifically.** These 12 are mega-caps with strong momentum signals in this period. A broader universe (S&P 500 constituents, tested dynamically) might flatten the edge — keep that caveat in mind.

## Known Limitations

1. **This backtest is hand-picked tickers.** AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR were all winners over this period. A fair validation would use S&P 500 constituents as of each month (survivorship bias removal). The 16.6% number is a ceiling, not a floor.

2. **Walk-forward validation not yet done.** Parameters (top 3, 12-month lookback) were not re-optimized; they were chosen based on the literature. Still worth a walk-forward pass before going live.

3. **Not tested at portfolio level in the backtester yet.** The `strategy_shootout.py` run held all-in, not the "max 5 positions" constraint mentioned in [[Plan]]. A full portfolio simulator (accounting for overlap between simultaneous signals on different tickers) is needed for reality.

4. **Dual momentum (cash filter) is optional, not mandatory.** The -21.6% DD is with the filter; without it, DD is higher. Effect hasn't been precisely quantified yet.

## Validation Checklist

- [x] Out-of-sample backtest (2019–present)
- [x] Beat benchmark (SPY) on return-adjusted-for-drawdown basis
- [ ] Walk-forward re-optimization test
- [ ] Portfolio-level simulation (max N positions)
- [ ] Real paper-trading evidence (2–3 months minimum)

**What's left before Phase 3 fully green:** Walk-forward pass + portfolio-level rigor. Both are doable before `paper_trader.py` goes live, and should be.

## Implementation Status

**Terminal app:** `trader_app.py` menu option 5 implements this perfectly — shows monthly holdings and equity curve vs. SPY, with optional dual-momentum filter via the risk-engine toggle.

**Live trading:** Not yet wired to [[IBKR Integration]]. `paper_trader.py` will do this: monthly rebalance signal → proposed trades → y/n approval → `place_bracket_order` → journal. This is the immediate next build.

## Related Notes

- [[Backtest Results & Findings]] — the full strategy shootout (all 5 families ranked)
- [[Strategy Decisions - SMA Crossover]] — why this one beat the original candidate
- [[Strategy Decisions - Opening Range Breakout]] — why day trading isn't the lane for now
- [[ADR - Momentum over SMA]] — the decision to advance this instead

## Papers & References

- Jegadeesh & Titman (1993) — "Returns to Buying Winners and Selling Losers" — foundational momentum paper
- Blitz, Hanauer, Vidojevic & Vlemmix (2020) — momentum robustness across markets and time
- Carhart (1997) — momentum as a factor in mutual fund performance

## Files

- `strategy_shootout.py` — runs all 5 families head-to-head
- `trader_app.py` menu option 5 — interactive backtest + holdings view
- `trader_settings.json` — current parameters (top_n=3, lookback_m=12)
