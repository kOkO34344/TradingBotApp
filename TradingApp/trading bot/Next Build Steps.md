---
tags: [roadmap, next-steps, project-management]
status: "Live — prioritized action queue"
last_updated: 2026-07-28
---

# Next Build Steps — Prioritized Action Queue

This is the **exact sequence of work** that makes sense given where the project stands right now. Updated 2026-07-21: **Phase 3 is built and live** — `paper_trader.py` exists and already executed a real rebalance on the paper account. This file previously assumed Phase 3 hadn't started; that's no longer true. Tiers below are reordered to match.

## Tier 0: Open risks — do these before unattended autotrade (rewritten 2026-07-28)

The 2026-07-27 blockers are **both closed**. What replaced them are three
failure modes that are now *visible* rather than silent, but not yet *fixed*.
Full plan with effort estimates lives in `Handoff.md` at the repo root.

### 0.1 ✅ CLOSED — the "Gateway Order Preset" was our own bug

IBKR error **10349** was never a Gateway config problem and never cancelled
anything. `place_bracket_order` built the parent `LimitOrder` with no explicit
`tif`; the Order Preset filled in the blank with DAY and announced it. Proved
by direct probe 2026-07-28 (1-share KO bracket at 50% below market,
unfillable, cancelled immediately): the error's `reqId` is always the
**parent's**, the stop leg always kept `tif="GTC"` at IBKR, and both legs
stayed `PreSubmitted` — a warning, not a rejection. Fixed by setting
`tif="DAY"` on the parent. Re-probe: no 10349 at all.
**Do not reopen this as a Gateway issue.** See [[IBKR Integration]].

### 0.2: Gateway goes unresponsive and the close monitor dies silently

`reflect_on_trades.main()` calls `ibs.connect()` **outside any try/except**, so
a refused connection kills the script. The `.sh` wrapper logs a traceback and
exits 0 — **no Telegram**. The monitor that exists to catch unattended
stop-outs was already dead for most of 07-26 and 07-27, and looked exactly
like a quiet market.

**Fix:** consecutive-failure counter in a small state file; text on the 2nd
consecutive failure (one blip is a sleeping laptop, two at 30-min spacing is a
dead monitor), silence until recovery, then text the recovery. Distinguish
"port closed" from "socket accepted but never answered" — they need different
responses from the human. Surface last-successful-contact in the daily digest.

**Do NOT** auto-restart Gateway from a script: it holds the broker session,
and a restart loop fighting a hung process leaves orders in an unknown state.

### 0.3: A rebalance can half-execute and nothing says so

`execute_rebalance` runs exits first, then entries (to free
`max_open_positions` headroom). Entries are DAY limits at `price * 1.005`. If
price runs away, the exits happened, the entries didn't, and you hold
unintended cash with **no alert**. That is a worse portfolio than either doing
nothing or doing everything — and it is exactly the shape of 07-27.

**Fix:** record the intended target state on approval
(`rebalance_intent.json`), reconcile against actual IBKR positions after the
close, journal `REBALANCE_INCOMPLETE` and text naming the specific legs that
missed. The policy for a missed entry (leave in cash / retry next session /
widen the buffer) is **Koko's call** — do not auto-widen into a market order,
which trades a visible miss for invisible slippage.

### 0.4: Kronos's top-3 flips on a rank-3/4 tie

Two `--dry-run` calls 30 minutes apart on identical closed-market data gave
`[AMZN, MSFT, GOOGL]` then `[AMZN, MSFT, DIS]` — ~$50k of trades decided by
which sampling draw you happened to run. **Measure before patching**: 20 runs
on frozen data to get the actual sampling SD, then consider rotation
hysteresis — and treat that as the strategy change it is, with a real
backtest, per rule 4. Interim rule needing no code: if rank 3 and 4 are within
~1 point, re-run once and only rotate on names present in both draws.

**Gate:** do not enable unattended autotrade until 0.2 and 0.3 are done. The
premise of unattended trading is that failures get noticed without a human
watching.

---

## Tier 1: Not due yet — don't run early

### 1.1: `grade_calls.py` — first real grades ~2026-07-29

```bash
cd /Users/kaloyanivanov/TradingBotApp
.venv/bin/python grade_calls.py --csv
```

**The project has ZERO real graded calls, and never had any.** Run
2026-07-28: 0 graded, 76 pending — verified genuine against the underlying
yfinance data, not just trusted. The 4 grades `graded_calls.csv` carried until
then came from two **synthetic test notes** (`AAPL_2026-05-15`,
`MSFT_2026-06-01`, each literally headed "SYNTHETIC TEST NOTE — not a real
call") deleted in `bdee3c8`. The CSV kept their grades and `daily_digest.py`
reported "4 graded, 0 pending" every morning — fabricated evidence in the one
file that gates autonomy. Overwritten now.

**Measured timing** (`forward_return()` needs `days + 1` bars, so 5d wants
**6** sessions, not 5; and 2026-07-24 has no bar for any ticker):

| Notes | 5d grades | 21d grades |
|---|---|---|
| 07-20 (1) | needs 1 more session | needs 17 more |
| 07-21 (11) | needs 2 more sessions | needs 18 more |
| 07-23 (12) | needs 4 more sessions | needs 20 more |
| 07-25 (14) | needs 5 more sessions | needs 21 more |

**What to do once grades exist (~2026-07-29+):**
- Copy the calibration report into [[Graded Calls Tracker]]
- Read it against the pending book's shape: **74% no-edge, confidence
  clustered at 3-5/10**. A mostly-no-edge, low-confidence book is cheap to be
  "right" about under the ±2% flat band — the win rate will flatter the skill.
- Treat any report claiming grades from notes not in `research_log/` as corrupt

**Frequency:** Weekly from ~2026-07-29 onward.

---

## Tier 2: Worth doing, doesn't block anything

### 2.1: Walk-forward test momentum rotation [2 hours]

**What:** Take the momentum strategy (top-3, 12-month lookback) and validate it on different time windows to check for overfitting.

**How:**
```python
# In strategy_shootout.py or new file, add a walk-forward loop:
for end_date in [2020-01-01, 2021-01-01, 2022-01-01, 2023-01-01, 2024-01-01]:
    start_date = end_date - 5 years
    backtest(momentum, start=start_date, end=end_date)
    print(f"{end_date}: {cagr}%, Sharpe {sharpe}")
```

**Expected output:**
```
2020-01-01: 22.3%, Sharpe 0.91
2021-01-01: 18.5%, Sharpe 0.85
2022-01-01: 16.6%, Sharpe 0.87  ← the original backtest period
2023-01-01: 19.2%, Sharpe 0.89
2024-01-01: 17.8%, Sharpe 0.88
```

**Verdict:** If all periods are 15%+ CAGR with Sharpe > 0.8, the strategy is robust. If one period is 5%, the parameters overfit.

**Why:** The 16.6% number comes from a hand-picked period with strong momentum in these 12 tickers. Fair validation checks if it works in other periods too — worth doing even though the strategy is already live on paper (this validates a running strategy, doesn't gate starting one).

### 2.2: ~~Backtest Kronos before trusting its forecasts~~ — DONE 2026-07-23, result: no edge

**What got built:** `KronosAI/kronos_backtest.py` — a real walk-forward sim
(no lookahead), two stages: (1) pooled Spearman information coefficient
between predicted and realized 20-day return, (2) a portfolio backtest
(Kronos top-3 rotation vs momentum's own ranking vs SPY) run through the
same `simulate_rotation()` engine `trader_app.momentum_backtest` uses
(extracted into a shared function specifically for this comparison).

**Window:** bounded by Kronos's own pretraining cutoff (paper states
training data ends June 2024, test period begins July 2024) — so July
2024 → now was the entire honest evaluation window available, ~24 monthly
rebalances.

**Result:** Spearman IC 0.036, directional hit rate 50.0% (304 pooled
pairs) — no measurable forecasting skill. The portfolio sim happened to
beat SPY (20.99% CAGR / -9.30% DD vs 17.92% / -18.76%) but given the flat
IC that's noise from a 24-decision sample, not real edge, and it lost
badly to momentum rotation (59.07% / -15.60%) on the identical dates/costs.

**Real bug hit along the way:** `pandas.Series.corr(method="spearman")`
silently needs `scipy` (not installed), and this wasn't discovered until
*after* the full 24-date walk-forward loop finished — the run's entire
output was lost with nothing checkpointed. Fixed by replacing it with a
scipy-free rank-based correlation, and by adding a checkpoint
(`kronos_backtest_checkpoint.json`, `--from-checkpoint` to reload) saved
right after the expensive loop, so a crash in reporting can't cost the
compute again.

**Full detail:** [[Kronos Research Agent]], `KronosAI/KronosVault/Kronos
Integration Log.md`. Kronos stays wired in as opt-in (`--signal kronos`)
for reference/re-testing, not because it showed value — momentum remains
the only validated signal.

### 2.3: ~~Build `paper_trader.py`~~ — DONE 2026-07-21

**What actually got built** (real, not the pseudocode originally sketched here):
- Fresh (force-refetched) momentum signal, top-N of watchlist by trailing 12-month return
- Diffs against **live IBKR positions** (`ib.positions()`) — SELL what dropped out of top-N, HOLD what's still in, BUY what's newly in
- Sizing: `qty = floor((NetLiquidation_usd * risk_pct_per_trade%) / (2*ATR))`, clamped to RiskGuard's max order notional using the *buffered* entry price
- Exits cancel the open stop leg and confirm the cancel before flattening; exits run before entries so RiskGuard's max-open-positions headroom is freed first
- Entries go through `place_bracket_order` with a 2×ATR stop (now `tif="GTC"` — see the bug below)
- `--dry-run` connects read-only (`readonly=True`, TWS-enforced as of
  2026-07-25 — previously just a convention, not a socket-level guarantee),
  prints the proposal, asks nothing

**First real run, 2026-07-21:** bought GOOGL (14 sh), AAPL (15 sh), JNJ (19 sh) on paper account DUQ903866. Full detail in [[IBKR Integration]].

**Real bugs hit and fixed during the first run** (not in the original plan, found by actually running it):
1. `place_market_order` always checked RiskGuard as if opening a new position — wrong for a rotation exit. Added an `opening: bool` param.
2. Sizing was clamped against raw market price, but RiskGuard checks notional against the buffered entry price — a size that looked fine could still get blocked (hit on JNJ's first attempt).
3. **The bracket stop leg had no explicit TIF, defaulting to DAY — it silently expired at end of session**, leaving all three positions briefly unprotected. Fixed with `tif="GTC"`; all three positions were manually re-protected same day.
4. The paper account is EUR-denominated with no live market-data subscription — added USD conversion and delayed-data mode.

This is exactly why "blocking: needed before Phase 3 can start" (the original framing) undersold it — building it is what *surfaced* these bugs. The lesson generalizes: get to a real execution as fast as safely possible, because sandboxed logic and a live account behave differently in ways a self-test can't catch.

---

## Tier 3: IN PROGRESS (started 2026-07-21)

### 3.1: Run Phase 3 paper trading [2–3 months]

**What:** Run `paper_trader.py` for real, every month, with a human approval gate. **This is now underway, not upcoming** — first rebalance already executed 2026-07-21.

**Setup — still manual, on purpose:**
- No scheduler yet (cron/launchd deferred until a few clean manual cycles happen)
- Owner runs `paper_trader.py` manually, monthly or on-demand
- Each run's proposal + approval + result is in `trade_journal.csv` — that log IS the approval log

**Approval gate — how it actually works (not the illustrative example originally here):**
`paper_trader.py` prints the full proposed rebalance (SELL/HOLD/BUY per ticker, with sizing already computed from RiskGuard's risk budget) and asks a single `y/n` for the whole batch — not a per-order price-entry negotiation. If a sized order still exceeds `risk_limits.json`'s notional cap, RiskGuard blocks it automatically and it's journaled as `BLOCKED`, not manually adjusted.

**Tracking:**
- Every month, update [[Graded Calls Tracker]] with new grading results (the monthly rebalance choices)
- Update [[Phase Milestones Dashboard]] with paper trading P&L vs SPY

**Success criteria:**
- After 3 months: momentum > SPY after costs
- Approval log shows you're not rubber-stamping (> 10% rejection rate is healthy)
- No execution bugs, no RiskGuard blocks

**Moving to Phase 4:** Only if all three are ✅ for 2+ consecutive months.

### 3.2: Decide: `research_agent.py` vs `trading_agent_service.py` [4 hours]

**Context:** Two Phase-1 candidates exist:
- `research_agent.py` (current, Claude-based, has real output)
- `trading_agent_service.py` (TradingAgents library, never run)

**Decision:** Based on grading calibration from 3.1 and personal preference.

**If sticking with `research_agent.py`:**
- Keep grading it weekly
- Continue weekly runs in Phase 3

**If switching to `trading_agent_service.py`:**
- Run it on the same watchlist
- Grade both in parallel for 2–4 weeks
- Pick the one with better calibration
- Drop the other

**Lower priority:** This doesn't block Phase 3; it's an optimization for Phase 4+.

---

## Tier 4: LATER (Q4 2026+)

### 4.1: Tiny real capital (Phase 4) [1–3 months]

Only after 2+ months of Phase 3 success. Not applicable yet.

**Blocked by:** Phase 3 completion + evidence that momentum beats SPY on paper.

### 4.2: Web UI (`TraderAppFullStack.txt` spec) [20+ hours]

A FastAPI backend + React frontend to:
- Show live P&L, holdings, equity curve
- Manual rebalance trigger (not automated yet)
- Research notes viewer
- Trade journal viewer

**Why it's last:** It's purely informational. The bot works fine in the terminal. A UI is nice-to-have for monitoring, not essential for execution.

**Blocked by:** nothing anymore, technically — Phase 3 has real fills in `trade_journal.csv` now, so the original "would display zeros" objection is gone. Still lower priority than more research/trading cycles, which is the evidence this project is actually gated on.

---

## One-Line Dependency Graph

```
Tier 1: grade_calls.py — on its own timeline (~2026-07-25+), doesn't gate anything else
Tier 2: momentum walk-forward validation — worth doing, doesn't gate Tier 3
Tier 3: paper trading execution (2–3 months) — ALREADY RUNNING since 2026-07-21
  ↓
Tier 4: Phase 4 (live) + Web UI (Web UI is actually unblocked already, just lower priority)
```

---

## How to Use This List

1. **Tier 3 is the main loop now** — this is where the bulk of ongoing time goes: weekly research runs, monthly rebalancing, periodic position/stop health checks
2. **Tier 1 fires on its own schedule** (~2026-07-25+) — don't force it early
3. **Tier 2 is a background task** — do it when there's a slow moment, not urgently
4. **Tier 4 (Phase 4) still 2-3 months away** — the Web UI part of Tier 4 could start anytime, just isn't the priority

---

## Effort Estimate

| Tier | Time | When |
|---|---|---|
| **Tier 1** | 5 min | Starting ~2026-07-25 |
| **Tier 2** | 6–8 hours | Whenever, no deadline |
| **Tier 3** | 20+ minutes/week for 2-3 months | Ongoing, started 2026-07-21 |
| **Tier 4** | 20+ hours | Oct 2026+ (Web UI could start earlier if wanted) |

**Total before Phase 4:** ~30–40 hours active time + 12 weeks passive (running scripts, waiting for P&L data) — clock is now running.

---

## Related Notes

- [[Phase Milestones Dashboard]] — status of each phase
- [[Call Grading System]] — how to grade research notes
- [[Plan]] — the full 5-phase plan
