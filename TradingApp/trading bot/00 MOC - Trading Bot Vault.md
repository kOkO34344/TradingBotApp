---
tags: [moc, index]
status: "Live — FTMO venue connected 2026-08-05; cleared to trade nothing (all 4 classes failed IC)"
last_updated: 2026-08-05
---

# Trading Bot — Map of Contents

This is the central index for the Trading Bot project. The codebase lives at `/Users/kaloyanivanov/TradingBotApp`; this vault is a synced knowledge base.

## Quick Status

| Phase                        | Status                              | Key Files                                                                                                                          |
| ---------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 0** (setup)          | ✅ Done                              | Python 3.13, Claude Code, IBKR account live                                                                                        |
| **Phase 1** (research agent) | 🟡 First 38 grades in — **no detectable skill** | `research_agent.py` → 14 notes (all still 2026-07-25). Graded 2026-08-03: 26% vs a 39% chance base rate, p=0.13. 38 pending at 21d. See [[Graded Calls Tracker]] |
| **Phase 2** (backtesting)    | ✅ Done, action taken                | SMA rejected (beats 1/10), momentum rotation identified (16.6% CAGR)                                                               |
| **Phase 3** (paper trading)  | ✅ Built, now RETIRED IN PLACE       | `paper_trader.py`. IBKR places no new orders as of 2026-08-02. Three positions still open and monitored: **JNJ(19) / DIS(52) / AMZN(21)**, all verified GTC-stopped 2026-08-02. |
| **FTMO venue**               | 🟢 CONNECTED, 🔴 cleared to trade **nothing** | Five modules, 294 offline selftests. Account 48137229, $25,000, FULL_ACCESS, 202 symbols. **All four asset classes failed their IC screen 2026-08-03.** See [[FTMO Venue]] |
| **Phase 4** (tiny live)      | ⏸ Locked                            | Unchanged — real capital still gated on paper evidence that does not exist yet |

**Most urgent right now (rewritten 2026-08-05):**

The two things that were blocking are both resolved, and both resolved
*negatively*. The venue connected; the screens failed. What is left is not
engineering.

1. **Grow the evidence — this is the only thing that actually unblocks
   anything.** Every note in `research_log/` is still dated **2026-07-25**, so
   the book has not grown in 11 days. Re-run
   `run_research_agent_watchlist.py`, then `grade_calls.py --csv` as the 38
   open calls mature at 21d. See [[Graded Calls Tracker]].
2. **Nothing may be enabled on FTMO.** All four asset classes failed their IC
   screen on 2026-08-03 (no |t| above 1.55; the matched momentum baseline
   failed all four too). A failed screen is not "needs a better
   configuration" — re-running with different tickers until one passes is the
   parameter-tuning the honest-backtesting rule forbids. See
   [[Kronos Research Agent]].
3. **Prove server-side stops attach at entry on FTMO.** Needs a real order, so
   it cannot be checked read-only. The entire FTMO risk model assumes it.
4. The three open IBKR positions stay monitored until they close naturally.
   `reflect_on_trades.py` and its launchd job keep running. **Verify stops are
   GTC, not merely present**, whenever checking.
5. Momentum rotation still has no portfolio-level walk-forward validation, and
   the broad-universe test written on 2026-07-23 has still never been run.
   Worth remembering that momentum is the *only* strategy family that ever
   earned Phase 3, and it is currently gated off.


**Standing context (durable, not a to-do list):**

- **The project has ZERO real graded calls and never has had any.** The four
  that existed until 2026-07-28 came from *synthetic test notes* that had been
  deleted, and were reported daily as a track record. See
  [[Call Grading System]].
- **Kronos is the main signal and momentum is disabled in code** (owner
  decision 2026-07-28, `signal_policy.py`). The evidence has not changed:
  Kronos shows no measurable skill (Spearman IC 0.036, 50% hit rate) and scored
  *worse* than momentum on the only head-to-head screen. Being the focus is a
  research direction, not a result. See [[Kronos Research Agent]].
- **Two deliberate exceptions to the earn-autonomy-with-evidence rule now
  exist**, and both were made with the evidence stated first:
  [[Autotrade (Experimental)]] (built 2026-07-24 despite an hourly IC screen
  showing no edge for either candidate signal; currently OFF) and the
  fully-unattended [[FTMO Venue]] (2026-08-02). Neither is precedent.
- **RiskGuard's daily-loss breaker is a pre-trade gate, not a monitor.** It is
  consulted only when an order is being placed, so it did nothing when GOOGL's
  stop fired on its own. As of 2026-08-02 the *visibility* half is fixed —
  `reflect_on_trades.py` now evaluates the breach condition every 30 minutes
  and alerts — but enforcement is unchanged. This is exactly why the FTMO
  venue uses a continuous equity monitor instead. See
  [[Risk Management System]].
- The watchlist is named groups with validated symbols, edited via
  `trader_app.py` menu 9 — not a raw string. See [[Watchlist Context]].

## The Vault by topic

### 🎯 **Strategy & Evidence**
- [[Strategy Decisions - SMA Crossover]] — why it was tested, why it lost (1/10 beat B&H)
- [[Strategy Decisions - Momentum Rotation]] — the winning strategy family; monthly rebalance, top-3 selection
- [[Strategy Decisions - Opening Range Breakout]] — day-trading research; the ORB paper's 33% claim vs. real 60-day test (-12.6%)
- [[Backtest Results & Findings]] — full numbers, all strategies, why day trading isn't the lane

### 🔧 **Risk & Execution**
- [[Risk Management System]] — RiskGuard code enforcement ($5k notional limit, 5 positions, $300 daily loss circuit breaker, stop required)
- [[FTMO Venue]] — **the trading venue as of 2026-08-02**; cTrader Open API, Challenge rule engine, continuous equity monitor, unattended
- [[IBKR Integration]] — paper account (port 4002, DUQ903866); RETIRED IN PLACE 2026-08-02, still monitoring three open positions
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
