---
tags: [project-management, phases, roadmap]
status: "FTMO armed and unattended; IBKR removed 2026-08-09. DAILY LOSS LIMIT BREACHED 2026-08-11 — the rule engine has refused every entry since, and the account is flat."
last_updated: 2026-08-12
---

# Phase Milestones Dashboard

> [!danger] 2026-08-11 — Phase 3 ran an account through one of its own hard limits
> `BREACH: daily loss 1,294.78 >= limit 1,250.00`. Four positions closed that
> day and the account has been flat since; every firing after 10:31 journalled
> `BLOCKED`. The runner is still armed — it is the **rule engine**, not the
> switch, that is holding it, which is the design working.
>
> This is the first time a limit in this project has been breached rather than
> merely tested. Two facts belong next to each other: the daily ceiling was
> raised that same day, leaving **$6.25** between our flatten tier and FTMO's
> $1,250 cliff, and the account cleared it by $44.78. See [[FTMO Venue]].
>
> **Nothing about Phase 3's exit criteria has been met, and this moves the
> evidence in the wrong direction.** Record it that way.

> [!warning] Three things this dashboard must not be read as saying
> 1. **IBKR is gone, not paused.** Its code, routes, screens and launchd jobs
>    were removed on 2026-08-09. Any milestone below that mentions paper
>    positions, RiskGuard or Gateway is history — kept for the incidents that
>    produced the rules, not as current state.
> 2. **"Armed" is not "working", and it is not "profitable".** The account is
>    armed, firing on schedule, and has just breached its daily limit.
> 3. **The sleep fix moved and is still unverified.** `caffeinate -i` is now
>    held by `ftmo_watch.sh` for the whole session, not by `ftmo_runner.sh`
>    per firing. It has never been verified under the real failure condition,
>    because an agent session holds the Mac awake by itself. Closing the lid
>    sleeps the machine regardless of any assertion.


> [!important] 2026-08-02 — the venue changed
> FTMO replaced IBKR as the trading venue (owner decision). IBKR is **retired
> in place**: no new orders, but it keeps monitoring its three open positions
> until they close naturally. The phase model below still describes the IBKR
> track, which remains accurate for those positions. The FTMO track is new and
> lives in [[FTMO Venue]] — it is deliberately NOT a phase, because it did not
> earn its way through this ladder; it was an owner decision made with the
> evidence position stated.

> [!warning] 2026-08-05 — where the project actually stands
> Two things resolved since, and both point the same way.
>
> **The FTMO venue is connected** (account 48137229, $25,000, FULL_ACCESS, 202
> symbols) and **cleared to trade nothing.** All four asset classes were
> IC-screened on 2026-08-03 and all four failed — no |t| above 1.55, with the
> matched momentum baseline failing all four as well.
>
> **The first 38 real graded calls landed 2026-08-03**: 26% correct against a
> 39% chance base rate, p=0.13. Indistinguishable from guessing, on a sample
> that is really closer to one observation than to 38.
>
> So the machinery is essentially finished and the evidence is still absent.

> [!danger] 2026-08-06 — the bot is now trading unattended
> `ftmo_runner.py` is **armed**. As first armed it fired at 01:15 daily and
> ranked a 14-symbol basket into four positions at 1% each, attaching a
> server-side stop to every entry with no human approval. **All three of those
> numbers have since changed** — see the 2026-08-11 revisions below and in
> [[FTMO Venue]] — but the shape has not: Kronos ranks, the sizer sizes, a
> stop rides the same request as the entry, and nobody approves anything.
>
> **This fires on a signal that failed the IC screen on all four asset
> classes.** It is the third recorded exception to rule 5, taken deliberately
> with the evidence stated first — not a gate that was quietly re-run until
> something passed. See [[FTMO Venue]] for the full record.
>
> The capital is simulated, so Phase 4 (real money on IBKR) stays locked and
> this is not a paper-before-real-money breach. What is genuinely new is that
> **no human approves an order any more.** Every limit still applies: the rule
> engine, the 1%-per-trade and portfolio caps, the stop attached at entry, and
> the FLATTEN path that is never gated by any limit.
>
> What to watch, in order: did the stops actually attach; does the daily limit
> evaluate against a real day-start balance (it needs `ftmo_runner_state.json`
> to persist); and does the selection churn between runs when the rank-4/5 gap
> narrows.
> That is not a failure of the build — it is the evidence gates doing exactly
> what they were put there to do, refusing before an order was placed. The
> honest summary of this project right now: **it can trade, and it has not
> earned the right to.**

This is the quick-reference status of each phase. Detailed rationale lives in [[Plan]]; this is the "where's the bot right now" snapshot.

## Phase 0: Environment & Basics ✅ DONE

**What it was:** Set up Python, Claude Code, IBKR paper account, dependencies.

**Status:** ✅ Complete
- Python 3.13 installed
- Claude Code working locally
- IBKR paper account `DUQ903866` live and verified 2026-07-21
- All dependencies in `requirements.txt` installed

**Exit criteria:** Could pull price history and run a script — ✅ Met

---

## Phase 1: Research Agent (No Execution) 🟡 FIRST GRADES IN — NO DETECTABLE SKILL YET

**What it is:** An agent that reads market data (price, volume, technicals, fundamentals) and writes a grounded research thesis with direction, confidence, risks, and levels.

**Status:** 🟡 **GRADED, AND THE ANSWER IS NO DETECTABLE SKILL**

> [!note] Superseded below — updated 2026-08-12
> The bullets in this section date from 2026-07-21 and describe grading as
> not-yet-due. It has since run. **38 calls graded at 5d on 2026-08-03: 26%
> correct against a 39% chance base rate, p=0.13** — indistinguishable from
> guessing in either direction, on a sample where all 38 share one market week
> and are therefore much closer to *one* observation than to 38.
>
> `research_log/` now holds **52 notes**, the newest dated **2026-08-03/04**,
> so the book did grow. `grade_calls.py --csv` has **not** been re-run since:
> 14 fresh 5d calls are gradeable now and all 52 remain pending at 21d. That
> re-run is the cheapest evidence available to this project.
> See [[Call Grading System]] and [[Graded Calls Tracker]].

- ✅ `research_agent.py` built and working
- ✅ 12 real research notes generated (2026-07-20/21), one per watchlist ticker (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR)
- ✅ Spot-check (AAPL, NVDA) shows grounded reasoning, no invented levels, calibrated confidence
- ⏳ `grade_calls.py --csv` **not run yet, correctly** — the earliest note (AAPL, 07-20) is only ~1 day old as of 2026-07-21, well short of even the 5-day forward-return horizon. Running it now would just show everything "pending"; there's no evidence being withheld by waiting.

**Exit criteria (from [[Plan]]):** "Run-on-demand script that turns a ticker into a sourced thesis you'd show someone" + evidence the thesis is any good.

**Status:** ✅ Met for "produces output" / ⏳ Grading pending on timing, not urgency.

**Next action:** Run `python3 grade_calls.py --csv` once notes are ≥5 days old (~2026-07-25+), then weekly from there. Also re-run `research_agent.py` on the watchlist weekly (next due ~2026-07-28) so the evidence base keeps growing.

**Fallback decision pending:** Compare `research_agent.py` (Claude) vs `trading_agent_service.py` (TradingAgents library) — lower priority, but still an open loop if one of them is clearly better after grading starts.

---

## Phase 2: One Narrow Strategy + Rigorous Backtesting ✅ DONE (ACTION TAKEN)

**What it was:** Backtest one simple rule rigorously (in/out-of-sample), then extend to a strategy family shootout.

**Status:** ✅ Complete — **action taken on findings**
- ✅ SMA 20/50 crossover backtested (2010–2026, daily bars, 0.1%/trade cost, in/out-of-sample split)
- ✅ Result: **beats 0/10 tickers vs SPY, 1/10 vs own buy-and-hold** — per plan's exit criteria, rejected
- ✅ 5-strategy shootout run: only **momentum rotation** (16.6% CAGR, -21.6% DD) competitive
- ✅ Day-trading research (TJR/ICT school, audited traders, ORB paper) completed — confirmed day trading isn't the lane

**Exit criteria:** "Beats buy-and-hold after costs, or iterate to something that does."

**Status:** ✅ Exit criteria met by iterating — momentum rotation identified as the live candidate

**Why momentum won:** Consistent with academic literature (Jegadeesh & Titman, Blitz, et al.). Only strategy family that nearly matched SPY return with *third less* drawdown.

**Caveats:** Hand-picked tickers (survivor bias), not walk-forward tested, not portfolio-level validated yet.

---

## Phase 3: Unattended Trading on Simulated Capital 🔴 LIVE ON FTMO — AND BREACHED

> [!important] What Phase 3 is NOW — read this before the section below
> Phase 3 stopped being "paper trading with human approval" on 2026-08-02.
> It is **unattended trading on FTMO's simulated Challenge capital**, with no
> approval step at all. Current state as of **2026-08-12**:
>
> - Armed, firing **every 15 minutes inside 16:30–23:00 Sofia, Mon–Fri**.
> - Ranking **101 symbols** from the venue's own capture, capped at 3 per
>   asset class, `top_n` 5, **1.65% risk per trade**.
> - **Daily loss limit BREACHED 2026-08-11** (1,294.78 vs 1,250.00). Account
>   flat; every entry refused since.
> - `ftmo_watch.py` watches equity and can flatten the book unattended — the
>   **fourth** exception to the earn-autonomy-with-evidence rule.
> - **An LLM is never in the intraday firing loop.** Rules fire at machine
>   speed; the agent reasons at research speed.
>
> Everything below this callout describes the **IBKR paper-trading era**
> (2026-07-21 to 2026-08-02). It is kept because those incidents produced most
> of the project's rules — not because any of it is current. See
> [[FTMO Venue]].

### Historical: Paper Trading with Human Approval (IBKR, 2026-07-21 → 2026-08-02)

**What it was:** Deploy momentum rotation live against paper account; agent proposes rebalance → you approve y/n → code executes → journal.

**Status:** ✅ **Built 2026-07-21, executed for real the same day; venue retired 2026-08-02 and removed 2026-08-09**
- ✅ IBKR connection verified live (2026-07-21, paper account, IB Gateway port 4002)
- ✅ `ibkr_service.py` hardened: `verify_paper_account()`, RiskGuard ($5k limit, 5 max positions, $300 daily loss, stop required), `place_bracket_order`, `trade_journal.csv` audit trail
- ✅ All 18 offline checks passing in `ibkr_service.py --selftest` (re-confirmed after the GTC fix below)
- ✅ **`paper_trader.py` built** — fresh momentum signal → diff vs live IBKR positions → printed proposal → explicit y/n → bracket-order execution, sized from RiskGuard's risk budget (`qty = floor(NetLiq_usd * risk_pct / (2*ATR))`, clamped to max order notional)
- ✅ **First real rebalance executed 2026-07-21**: bought GOOGL (14 sh), AAPL (15 sh), JNJ (19 sh) — see [[IBKR Integration]] for full detail
- ⚠️ **Real bug found and fixed same day**: the bracket stop leg defaulted to TIF=DAY (IBKR's default), so it silently expired at end of session, leaving all three positions briefly unprotected. Fixed (`tif="GTC"` in `ibkr_service.py`) and all three positions were manually re-protected with fresh GTC stops. See [[IBKR Integration]] and [[Risk Management System]].
- No scheduler yet — owner runs `paper_trader.py` manually. Cron/launchd only after a few clean manual cycles.

**Exit criteria:** "2–3 months of paper logs showing strategy beats benchmark, approval log showing human gating works."

**Status:** ✅ Started 2026-07-21 — now accumulating the 2-3 months of evidence the exit criteria requires.

**Open items (not blockers, just not yet done):**
1. Momentum rotation hasn't had portfolio-level walk-forward validation — worth doing, doesn't block continued paper trading
2. Phase 1 grading has produced **zero real graded calls so far** — the 4 that `graded_calls.csv` carried until 2026-07-28 came from deleted *synthetic test notes*. First genuine 5d grades land ~2026-07-29. See [[Call Grading System]].

**✅ UNBLOCKED 2026-07-28.** The 07-27 "blocker" was two problems, and the
scarier-looking one turned out not to exist:

1. ✅ **Fixed 07-27** — RiskGuard's notional cap applied to *exits* as well as
   entries, so both AAPL and JNJ (bought under the then-$5,000 cap,
   appreciated past it to ~$5,007/$5,005) were **un-exitable**. A limit that
   blocks a close raises risk. Both the cap and the daily-loss breaker are now
   gated on `opening`. Limits also rescaled to 50000/2000/8. See
   [[Risk Management System]].
2. ✅ **Fixed 07-28, and it was never a Gateway problem.** IBKR error **10349**
   was blamed on an Order Preset needing a GUI fix. Direct probe against the
   paper account disproved that: our own `place_bracket_order` built the
   parent `LimitOrder` with **no `tif` at all**, the preset filled in the
   blank with DAY, and *announced* it. The error's `reqId` is always the
   parent's; the stop leg always carried explicit `tif="GTC"` and IBKR held it
   as GTC throughout. It is a **warning, not a rejection** — both legs stayed
   `PreSubmitted`. Setting `tif="DAY"` on the parent removes it entirely.
   **No Gateway change is or was needed.** See [[IBKR Integration]].

**The account was never what the record said.** The 07-27 rebalance did not
place zero trades — **AMZN 21 @ 232.73 and DIS 52 @ 95.39 filled**, and only
MSFT missed. The journal recorded all three as `Cancelled` because
`place_bracket_order` snapshotted the parent's status one second after
submission and never looked again. The account ran two positions ahead of
every record for a full day.

Holdings verified directly against IBKR 2026-07-28 — **four positions, each
with a live full-quantity GTC stop**:

| Symbol | Qty | Avg cost | Stop |
|---|---|---|---|
| AAPL | 15 | 328.04 | 309.10 |
| JNJ | 19 | 249.98 | 237.61 |
| DIS | 52 | 95.39 | 90.83 |
| AMZN | 21 | 232.73 | 217.74 |

**Next action:** supervised Kronos rebalance at the next open (16:30 local /
09:30 ET), human-approved, then let evidence accumulate toward the 2-3 month
exit criteria. Three risks remain open and are planned in `Handoff.md` — a
silently-dying close monitor, Kronos's unstable top-3, and half-completed
rebalances. **Unattended autotrade stays off until the first two are done.**

---

## Phase 4: Tiny Real Capital ⏸ LATER (NOT YET APPLICABLE)

**What it is:** Switch from paper to live with $100–$1000, keep all safeguards, re-evaluate monthly.

**Status:** ⏸ Not applicable yet

**Prerequisites:** 2–3 months of Phase 3 paper trading showing:
- Strategy beats SPY after costs
- Approval gate is working (you're not just rubber-stamping)
- No catastrophic bugs in execution layer
- Emotional readiness to watch real P&L

**Target date:** Q4 2026 at earliest (Phase 3 lasts 2–3 months, so if Phase 3 starts now, Phase 4 ~Oct/Nov 2026)

---

## Work Queue by Priority

> [!important] The current queue is in [[Next Build Steps]] — 2026-08-12
> Everything under this heading is the **July IBKR-era** queue and is kept for
> its reasoning, not as instructions. The live queue in one line: decide what
> happens after the breach, re-run `grade_calls.py --csv` on the 14 fresh 5d
> calls, and fix the night band's window so the UI stops under-drawing
> firings.

**NOT YET DUE (don't do early) — RESOLVED, this is history:**

- ~~**`grade_calls.py --csv`** — wait until notes are ≥5 days old
  (~2026-07-25+)~~ — it ran on 2026-08-03. 38 graded, no detectable skill.

**ONGOING (recurring operational tasks, not one-time builds):**

1. **Re-run `research_agent.py` on the watchlist weekly** (next due ~2026-07-28) — see [[Research Agent Workflow]]
2. **Re-run `paper_trader.py`** for the next monthly rebalance, or on-demand — `--dry-run` first if unsure what it'll propose
3. **Sanity-check live paper positions periodically** — stops present *and GTC* (not just "present" — a DAY stop looks fine for hours then silently vanishes, see [[IBKR Integration]]), no daily-loss circuit breaker trips, journal matches what's actually on IBKR

**MEDIUM (worth doing, not blocking anything):**

4. **Walk-forward test momentum rotation at portfolio level**
   - What: Roll forward the momentum parameters (12-month lookback, top-3) across different time periods; add the max-N-positions constraint
   - Time: ~2 hours (reuse backtest code)
   - Impact: Checks if parameters are robust or if they overfit this period — momentum rotation is already live on paper, so this validates a strategy already running, not one about to start

5. **Decide on `research_agent.py` vs `trading_agent_service.py`**
   - Once Phase 1 grading is live, pick which research backend to keep
   - Lower priority than paper trading

**LATER, but legitimately unblocked now:**

6. **Web UI** (`TraderAppFullStack.txt` spec) — the original blocker ("a dashboard before fills exist would display zeros") no longer applies: real fills exist in `trade_journal.csv`. Still lower priority than more research/trading cycles.

---

## Current Open Risks

> [!danger] The three that actually matter on 2026-08-12
> | Risk | Where it stands |
> |---|---|
> | **$6.25 between the flatten tier and FTMO's hard cliff** | Set 2026-08-11, breached the same day. Less than one tick of index-CFD slippage; a gapping stop clears it without noticing. Unaddressed. |
> | **The Mac sleeping mid-run** | The assertion moved to `ftmo_watch.sh` and covers 16:30–23:00 Sofia. **Never verified under the real failure condition** — agent sessions hold the machine awake. A lid-close still ends it, and there is no software fix. |
> | **Trading a signal with no measured edge on any class** | Unchanged, and now with a breached account behind it. Four IC screens, two horizons, all failed. Closing this needs research cycles, not code. |
>
> A fourth, smaller one: the web UI's night band still draws a 16:30 → 11:30
> session as 20 hourly slots while the runner fires 27 times to 23:00, so the
> one screen built to make missed firings visible is currently mis-drawing them.

The table below is the **IBKR-era** risk register, kept for the record:

| Risk | Mitigation |
|---|---|
| Phase 1 agent is not calibrated yet | Graded 2026-08-03: 38 calls, 26% vs a 39% chance rate, p=0.13 — no detectable skill, and the sample is really one market week |
| Momentum parameters overfit this period | Walk-forward test — worth doing even though the strategy is already live on paper |
| **REALIZED, then fixed:** bracket stop defaulted to TIF=DAY and silently expired end-of-session, leaving live positions unprotected for a period | Fixed 2026-07-21 (`tif="GTC"` in `place_bracket_order`); all 3 positions manually re-protected same day. Ongoing mitigation: verify `order.tif == "GTC"` when checking positions, not just that a stop exists — see [[IBKR Integration]] |
| Approve-and-forget psychology | Journal log review weekly; explicit tracking of approval vs auto-fills |
| Market regime changes between backtest and live | Dual-momentum (cash filter) in settings, drawdown circuit breaker in RiskGuard |
| Paper account is EUR-denominated with no live market-data subscription | `paper_trader.py` converts NetLiquidation to USD via EURUSD rate and requests delayed data (`reqMarketDataType(3)`) — any *other* future code touching the account must do the same, don't assume USD/live data |

---

## Phase Timeline (Realistic Estimate)

| Phase | Duration | Actual dates | Status |
|---|---|---|---|
| **Phase 0** | 1–2 weeks | ✅ Done | ✅ Done |
| **Phase 1** | 2–3 weeks | ✅ 2026-06 to 2026-07 | 🟡 Output ready, grading pending |
| **Phase 2** | 3–4 weeks | ✅ 2026-07 | ✅ Done, findings acted on |
| **Phase 3** | 2–3 months | ✅ Started 2026-07-21 | ✅ Live — first rebalance executed, accumulating evidence through ~2026-10 |
| **Phase 4** | 1–3 months+ | ⏳ ~2026-10+ | ⏸ Only after Phase 3 passes |

**Total before Phase 4:** ~4–5 months from scratch. Currently at ~5 weeks in, Phase 3 clock now running.

---

## Dependencies & Blockers

```
Phase 3 (paper trading): unblocked, live since 2026-07-21.
  Phase 1 grading was never a hard blocker for Phase 3 (the trading loop
  is rules-based, not agent-based) — it informs confidence in the
  research agent specifically, on its own timeline (~2026-07-25+).

Phase 4 (live capital) blocked on:
  ├─ 2–3 months of Phase 3 logs (clock started 2026-07-21)
  └─ Calibrated approval behavior proven
```

---

## Success Criteria

**Phase 1:** Confidence > 55% across all buckets, high > mid > low, sustained over 4+ weeks

**Phase 2:** ✅ Already met — moved to momentum rotation instead of SMA

**Phase 3:** 2+ months of paper > SPY after costs, approval log shows human gating works

**Phase 4:** 2+ consecutive months of live > SPY after costs, zero account-wipe events

---

See [[00 MOC - Trading Bot Vault]] for the full vault index, and [[Plan]] for the detailed reasoning behind each phase.
