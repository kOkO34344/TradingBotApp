---
tags: [moc, index]
status: "Live"
last_updated: 2026-07-25
---

# Trading Bot — Map of Contents

This is the central index for the Trading Bot project. The codebase lives at `/Users/kaloyanivanov/TradingBotApp`; this vault is a synced knowledge base.

## Quick Status

| Phase                        | Status                              | Key Files                                                                                                                          |
| ---------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 0** (setup)          | ✅ Done                              | Python 3.13, Claude Code, IBKR account live                                                                                        |
| **Phase 1** (research agent) | 🟡 Real output, grading not due yet | `research_agent.py` → 14 notes in `research_log/` (re-run 2026-07-25), oldest from 2026-07-20/21 — 5-*trading*-day grading horizon lands ~2026-07-30, not yet |
| **Phase 2** (backtesting)    | ✅ Done, action taken                | SMA rejected (beats 1/10), momentum rotation identified (16.6% CAGR)                                                               |
| **Phase 3** (paper trading)  | ✅ Built and LIVE                    | `paper_trader.py` live since 2026-07-21. **GOOGL closed 2026-07-23** (gapped through its stop, ~-$422, found + backfilled 2026-07-25). Current: AAPL(15)/JNJ(19), both GTC-protected. |
| **Phase 4** (tiny live)      | ⏸ Later                             | After 2–3 months of paper evidence                                                                                                 |

**Most urgent right now:**
1. **Nothing is overdue.** Grading isn't meaningful until notes age ≥5 *trading* days (~2026-07-30) — don't run `grade_calls.py` early just to have run it (it was re-run 2026-07-25: still 0/76 graded, as expected).
2. Periodically check the live paper positions (AAPL/JNJ) are healthy — stops present **and GTC**. This is now partly automated: `reflect_on_trades.py` gained a second detection tier 2026-07-25 (position-snapshot diff) specifically because its execution-based tier silently missed the GOOGL close below — see [[Risk Management System]]. Still worth an eyeball check periodically, not just trusting the automation.
3. Next watchlist research re-run due ~2026-08-01 (weekly cadence, now `run_research_agent_watchlist.py`, supports `--group`); next `paper_trader.py` rebalance whenever the owner runs it (monthly, manual, no scheduler yet).
4. Momentum rotation still hasn't had portfolio-level walk-forward validation — worth doing, not currently blocking anything.
5. **2026-07-23:** Kronos (foundation-model forecaster) is integrated and now backtested — see [[Kronos Research Agent]]. Result: **no measurable forecasting skill found** (Spearman IC 0.036, 50% hit rate, in the one honest post-cutoff window its own training data allows). Stays wired in as opt-in only (`trader_app.py` menu item 7, `paper_trader.py --signal kronos`); momentum stays the default and the only validated signal.
6. **2026-07-24:** Unattended "autotrade" toggle built — see [[Autotrade (Experimental)]]. An hourly IC screen showed no edge for EITHER candidate signal (momentum-hourly IC -0.037, Kronos-hourly IC -0.081, both ~coin-flip hit rates) but it was built anyway per explicit owner request, as a deliberate live paper experiment. **Currently ON, signal=kronos** as of 2026-07-25 — the owner armed it; first live firing is the next NYSE market open. RiskGuard is unaffected, but see #7 below for a real limit on what it actually protects against.
7. **New 2026-07-25:** Watchlist is now named groups (`trader_app.py` menu 9) with validated symbols, not a raw comma-separated string — see [[Watchlist Context]]. Also: the $300 daily-loss circuit breaker is a **pre-trade gate, not a monitor** — it reads IBKR's live `RealizedPnL` only when an order is about to be placed, so it did nothing for GOOGL's stop firing on its own. Worth knowing before treating autotrade as automatically loss-capped.

## The Vault by topic

### 🎯 **Strategy & Evidence**
- [[Strategy Decisions - SMA Crossover]] — why it was tested, why it lost (1/10 beat B&H)
- [[Strategy Decisions - Momentum Rotation]] — the winning strategy family; monthly rebalance, top-3 selection
- [[Strategy Decisions - Opening Range Breakout]] — day-trading research; the ORB paper's 33% claim vs. real 60-day test (-12.6%)
- [[Backtest Results & Findings]] — full numbers, all strategies, why day trading isn't the lane

### 🔧 **Risk & Execution**
- [[Risk Management System]] — RiskGuard code enforcement ($5k notional limit, 5 positions, $300 daily loss circuit breaker, stop required)
- [[IBKR Integration]] — paper account live (port 4002, DUQ903866), bracket orders, audit trail
- [[Trade Journal Structure]] — what goes in `trade_journal.csv` every order attempt/fill/block
- [[Autotrade (Experimental)]] — unattended hourly rebalancing toggle, built 2026-07-24 despite no measurable edge at that cadence; OFF by default, the one documented exception to human-approval-gated execution

### 🧠 **Research & Grading**
- [[Research Agent Workflow]] — how `research_agent.py` works, what it outputs, where the notes live
- [[Kronos Research Agent]] — the project's second research agent (quantitative forecast, foundation model); integrated into the app and `paper_trader.py`; backtested 2026-07-23, no measurable edge found
- [[Call Grading System]] — how `grade_calls.py` scores calls (long > +0.5%, short < -0.5%, no-edge ±2%), calibration rules
- [[Graded Calls Tracker]] — running log of research notes graded; accuracy by confidence bucket

### 📊 **Reference & Definitions**
- [[Indicators Reference]] — RSI, ATR, SMA, EMA, MACD, VWAP, Bollinger Bands (math + implementation notes)
- [[Watchlist Context]] — the 14-ticker watchlist, now stored as named groups (`watchlist.py`) with validated symbols; not synced from IBKR (no API for that)
- [[Market Regimes]] — how to identify bull/bear/sideways; implications for signal reliability

### 🏗️ **Architecture & Decisions**
- [[ADR - IBKR vs Alpaca]] — why the project uses Interactive Brokers instead of the originally recommended Alpaca
- [[ADR - Momentum over SMA]] — why momentum rotation earned the right to Phase 3 (evidence), SMA didn't
- [[ADR - Python Rules, Not Model Predictions]] — why order execution uses hard-coded risk limits, not agent self-regulation
- [[ADR - Paper Trading Gate]] — why full autonomy on real money is a v3+ goal, not v1

### 📋 **Project Management**
- [[Phase Milestones Dashboard]] — what's done/in-progress/blocked/next by phase, with deadlines
- [[Next Build Steps]] — prioritized work queue (grade calls, build `paper_trader.py`, validate momentum)
- [[Plan]] — original 5-phase plan with status updates
- [[The App]] — project overview and current snapshot
- [[README_trader_app]] — how to run the terminal app, menu reference

---

## File structure (for sync)

```
/Users/kaloyanivanov/TradingBotApp/
├── TradingApp/
│   └── trading bot/               ← You are here (Obsidian vault root)
│       ├── .obsidian/             ← Vault config (git-ignored normally)
│       ├── 00 MOC - Trading Bot Vault.md  ← This file
│       ├── Plan.md
│       ├── The App.md
│       ├── Backtest Results & Findings.md
│       ├── README_trader_app.md
│       └── [NEW] All the topic notes created below
├── backtest_results.csv
├── graded_calls.csv
├── orb_trades.csv
├── trader_app.py
├── research_agent.py
├── ibkr_service.py
├── sma_crossover_backtest.py
├── strategy_shootout.py
├── grade_calls.py
└── [research_log/]  ← 12 real research notes, one per ticker
```

---

## How to use this vault

**Typical workflow:**

1. **Before trading:** Check [[Phase Milestones Dashboard]] and [[Next Build Steps]] for what's due
2. **Research decision:** Read the relevant [[Strategy Decisions - *]] note + [[Backtest Results & Findings]]
3. **Risk check:** Review [[Risk Management System]] before proposing or approving a trade
4. **After execution:** Log it in [[Trade Journal Structure]] (automated by `trade_journal.csv` in code) and verify it's in [[Graded Calls Tracker]]
5. **Weekly:** Run `grade_calls.py --csv`, update [[Graded Calls Tracker]] with latest calibration

**Wikilinks:** Click any [[double-bracketed note]] to navigate. Backlinks (bottom of each note) show what references it.

---

## Key decisions baked into this project

- **No day trading as the primary lane** — the evidence (97% failure rate, even the best-published strategy has losing months) argues for swing/position trading instead
- **Rules-based execution, human-in-the-loop approval** — the agent proposes, you approve, code executes; full autonomy only after months of paper evidence
- **Evidence-driven over intuition** — all strategy claims are backtested in/out-of-sample; all research agent calls are graded against actual price action
- **Risk is in code** — RiskGuard limits, daily-loss circuit breaker, stop-required rule are all enforced in `ibkr_service.py`, not left to model judgment
- **Paper trading is mandatory** — Phase 3 lasts 2–3 months minimum before any real capital is risked

---

## Current open questions / decisions pending

1. **Grade the research notes once they're old enough** (~2026-07-25+) — `grade_calls.py --csv`, weekly from there
2. **Momentum rotation at portfolio level** — should it get the same walk-forward rigor the SMA got, even though it's already live on paper?
3. **research_agent.py vs trading_agent_service.py** — which research backend to commit to? (Lower priority — focus on grading first)
4. **Web UI** — items 1-2 are done and real fills now exist, so it's *legitimately* unblocked (not just deferred). Still lower priority than more research/trading cycles, which is the evidence this project is actually gated on.

---

## Maintenance

This vault is manually synced with the codebase. If you change code structure or add files, update this MOC. If you update strategy parameters or add tickers, update [[Watchlist Context]]. If you run `grade_calls.py`, update [[Graded Calls Tracker]].
