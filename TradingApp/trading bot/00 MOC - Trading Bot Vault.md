---
tags: [moc, index]
status: "Live — FTMO is the ONLY venue and is ARMED; IBKR removed entirely 2026-08-09"
last_updated: 2026-08-09
---

# Trading Bot — Map of Contents

Central index for the Trading Bot project. The codebase lives at
`/Users/kaloyanivanov/TradingBotApp`; this vault is a synced knowledge base.

> [!important] IBKR was REMOVED on 2026-08-09
> The venue was retired in place on 2026-08-02 and its code deleted a week
> later at the owner's instruction: `ibkr_service.py`, `paper_trader.py`,
> `reflect_on_trades.py`, `autotrade_runner.py`, six `api/` modules, three web
> screens and two launchd jobs. FTMO is the only venue.
>
> **Three IBKR positions were presumed OPEN at removal and could not be
> verified** — JNJ(19), DIS(52), AMZN(21), last confirmed 2026-08-02. Gateway
> had been refusing connections for about a week, so the monitor that was
> supposed to protect them had been failing every 30 minutes and watching
> nothing. Their stops live at the broker. What was given up is the RECORD: if
> one closes, nothing will journal it. Accepted knowingly on a paper account.
> **Do not describe this as a clean wind-down.** They were not closed first.

## Quick Status

| Phase | Status | Key files |
| --- | --- | --- |
| **Phase 0** (setup) | ✅ Done | Python 3.13, Claude Code |
| **Phase 1** (research agent) | 🟡 38 grades in — **no detectable skill** | `research_agent.py` → 14 notes (all still 2026-07-25). Graded 2026-08-03: 26% vs a 39% chance base rate, p=0.13. 38 pending at 21d. See [[Graded Calls Tracker]] |
| **Phase 2** (backtesting) | ✅ Done, action taken | SMA rejected (beat B&H 1/10), momentum rotation identified (~18.5% CAGR) |
| **Phase 3** (unattended trading, simulated capital) | 🔴 **LIVE on FTMO** since 2026-08-06 | `ftmo_runner.py`, hourly at :30 inside a 16:30–11:30 Sofia window, not Sundays. Ten modules, **579 offline selftests**. Account 48137229, $25,000 simulated, 202 symbols. See [[FTMO Venue]] |
| **Phase 4** (real capital) | ⏸ Locked | Not reachable from any code path that currently exists |

**Most urgent right now (rewritten 2026-08-09):**

1. **The runner has been failing to fire.** The night band on the web UI shows
   last session as 1 forced + 7 closed + **12 missed** — twelve scheduled
   firings that left no audit record. Root cause is documented: the Mac sleeps
   on battery mid-run. `ftmo_runner.sh` now wraps the run in `caffeinate -i`,
   but that fix has **never been verified under the real failure condition**,
   because Claude Code's own session holds the machine awake while it works.
   A firing that succeeds during an agent session proves nothing.
2. **Grow the evidence — still the only thing that unblocks anything.** Every
   note in `research_log/` is dated **2026-07-25**. Re-run
   `run_research_agent_watchlist.py`, then `grade_calls.py --csv` as the 38
   open calls mature at 21d. See [[Graded Calls Tracker]].
3. **Be precise about the FTMO override.** All four asset classes failed their
   IC screen twice — 2026-08-03 at a 20-day horizon, 2026-08-08 at 5 days, no
   |t| above 1.55 either time — and the venue trades anyway by explicit owner
   decision. The gate was **not** re-run with different tickers until something
   passed; that would be the parameter-tuning the honest-backtesting rule
   forbids. It was overridden knowingly, once, in the open.
4. Momentum rotation still has no portfolio-level walk-forward validation, and
   the broad-universe test written 2026-07-23 has still never been run. It
   remains the *only* strategy family that ever earned Phase 3, and it is
   gated off in code.

**Standing context (durable, not a to-do list):**

- **The project had ZERO real graded calls until 2026-08-03**, and the four it
  appeared to have before then came from *synthetic test notes* that had been
  deleted — reported daily as a track record. See [[Call Grading System]].
- **Kronos is the main signal and momentum is disabled in code**
  (`signal_policy.py`). Kronos shows no measurable skill (Spearman IC 0.036,
  50% hit rate) and scored *worse* than momentum on the only head-to-head
  screen. Being the focus is a research direction, not a result. See
  [[Kronos Research Agent]].
- **THREE deliberate exceptions to the earn-autonomy-with-evidence rule
  exist**, all made with the evidence stated first, none of them precedent:
  1. The retired IBKR hourly runner — built 2026-07-24 despite an hourly IC
     screen showing no edge for either candidate signal. Removed with its
     venue; see [[Autotrade (Experimental)]] for the history.
  2. The fully-unattended [[FTMO Venue]] — 2026-08-02.
  3. **Kronos firing on FTMO with no asset class having passed a screen** —
     armed 2026-08-06. The original condition was "Kronos may only trade a
     class that passed its own IC screen". No class passed. It trades anyway.

  Note the direction of travel: each exception was larger than the last, and
  the third removes the condition that made the second defensible. Worth
  seeing plainly rather than as three separate footnotes.
- **Every FTMO limit is measured on equity INCLUDING floating P&L**, so the
  account can breach with no order placed. That is why this venue has a
  continuous monitor and not a pre-trade gate. The retired IBKR guard was a
  pre-trade gate and provably could not see that failure — GOOGL's stop fired
  overnight, moved the account $422, and the breaker was simply never
  evaluated. See [[Risk Management System]].
- The watchlist is the **research** universe, not the traded one. FTMO trades
  its own CFD universe derived from the venue's symbol capture. See
  [[Watchlist Context]].

## The Vault by topic

### 🎯 Strategy & Evidence
- [[Strategy Decisions - SMA Crossover]] — why it was tested, why it lost
- [[Strategy Decisions - Momentum Rotation]] — the only family that earned Phase 3
- [[Strategy Decisions - Opening Range Breakout]] — the paper's 33% claim vs a real 60-day test (−12.6%)
- [[Backtest Results & Findings]] — full numbers, all strategies

### 🔧 Risk & Execution
- [[FTMO Venue]] — **the only venue**; cTrader Open API, Challenge rule engine, continuous equity monitor, unattended
- [[Risk Management System]] — how limits are enforced now, and the incidents that shaped them
- [[IBKR Integration]] — 🗄️ **HISTORICAL.** The venue and its code are gone; kept for the incidents that produced most of the project's rules
- [[Autotrade (Experimental)]] — 🗄️ **HISTORICAL.** The IBKR hourly runner, removed 2026-08-09

### 🧠 Research & Grading
- [[Research Agent Workflow]] — how `research_agent.py` works and where notes live
- [[Kronos Research Agent]] — the forecast model; backtested 2026-07-23, no measurable edge found
- [[Call Grading System]] — how `grade_calls.py` scores calls, and why the band is now 0.5× realized sigma
- [[Graded Calls Tracker]] — running log, accuracy by confidence bucket

### 📊 Reference
- [[Watchlist Context]] — the 14-ticker **research** universe, stored as named groups

### 🏗️ Architecture & Decisions
- [[ADR - IBKR vs Alpaca]] — 🗄️ **HISTORICAL.** Both options are moot; the project trades FTMO
- [[ADR - Python Rules, Not Model Predictions]] — why execution uses hard-coded limits, not agent judgment

### 📋 Project Management
- [[Phase Milestones Dashboard]] — done / in progress / blocked, by phase
- [[Next Build Steps]] — prioritized work queue
- [[Plan]] — original 5-phase plan with status updates
- [[The App]] — project overview and current snapshot
- [[README_trader_app]] — terminal app menu reference

## The web UI

A local watch station at `http://localhost:3000`, started with `./run_web.sh`.
**Local only — never deployed.** Four screens since 2026-08-09:

| Route | What it shows |
| --- | --- |
| `/watch` | Equity / balance / floating, the **night band**, the three limit meters, open positions |
| `/signal` | Kronos ranking and sampling spread; the FTMO plan it would produce |
| `/market` | Candles and indicators from the venue's own bars |
| `/ledger` | The trade journal (both venues) and recorded backtests |

**The night band** is the thing worth knowing about: it reconstructs a full
session (16:30 → 11:30 Sofia) from `ftmo_audit/*.jsonl` and draws one cell per
hourly wakeup. **A firing that was due and did not happen is drawn, not
omitted** — that is how the twelve missed firings above are visible at a
glance. It reads the audit files off disk with no venue session, so it still
answers when the broker is unreachable.

The UI **places no orders**. Its only write is arming or disarming the runner.

## Key decisions baked into this project

- **No day trading as the primary lane** — the evidence argues for swing/position trading
- **Rules-based execution** — the agent proposes, code executes; an LLM is never in the intraday firing loop
- **Evidence-driven over intuition** — in/out-of-sample backtests, graded calls, negative results reported not massaged
- **Risk is in code** — limits, thresholds and the stop-required rule live in `ftmo_rules.py` / `ftmo_sizing.py`, not in prompts or model judgment
- **Simulated capital only** — the FTMO Challenge account is simulated; real capital stays locked

## Maintenance

This vault is synced with the codebase — partly by a daily launchd job
(`daily_vault_sync.sh`, 22:00, scoped to this folder only) and partly by hand.
If you change code structure, update this MOC. If you change the watchlist,
update [[Watchlist Context]]. If you run `grade_calls.py`, update
[[Graded Calls Tracker]].
