---
tags: [roadmap, next-steps, project-management]
status: "Live — prioritized action queue"
last_updated: 2026-07-27
---

# Next Build Steps — Prioritized Action Queue

This is the **exact sequence of work** that makes sense given where the project stands right now. Updated 2026-07-21: **Phase 3 is built and live** — `paper_trader.py` exists and already executed a real rebalance on the paper account. This file previously assumed Phase 3 hadn't started; that's no longer true. Tiers below are reordered to match.

## Tier 0: BLOCKER — fix before any further trading (added 2026-07-27)

### 0.1: Clear the Gateway Order Preset forcing DAY TIF

**Every bracket order is currently cancelled on arrival.** IBKR error
**10349** — "Order TIF was set to DAY based on order preset" — the preset
overrides the explicit `tif="GTC"` on bracket legs and IBKR cancels rather
than accepts. On 2026-07-27 this killed all three entries of an approved
Kronos rebalance; the account simply held instead of rotating.

**Fix:** IB Gateway → **Global Configuration → Presets** → clear the DAY TIF
override for stocks.

**Then verify before trusting a full rotation:** place one small bracket entry
and confirm it reaches `PreSubmitted`/`Filled` with `order.tif == "GTC"`,
rather than `Cancelled`. This is Gateway-side config — nothing in the codebase
can detect or work around it, and it will silently break every autotrade
firing too.

### 0.2: Restart IB Gateway if API connections hang

Seen 2026-07-27: Gateway kept the port open but stopped answering new API
connections; read-only position checks hung indefinitely. Kill stray python
processes still holding connections, then restart Gateway.

---

## Tier 1: Not due yet — don't run early

### 1.1: `grade_calls.py` — wait for notes to age

```bash
cd /Users/kaloyanivanov/TradingBotApp
python3 grade_calls.py
```

**Why it's still not urgent:** Re-run 2026-07-25 (14 notes' worth of watchlist,
38 total, refreshed the same day) — still 0/76 graded, exactly as expected.
The 5-day horizon is **5 *trading* days**, not calendar days: the oldest
notes (07-20/21) only reach 5 trading days old around **2026-07-30**, three
days later than the original ~07-25 estimate, which conflated the two.

**What to do once it IS due (~2026-07-30+):**
- Copy the calibration report into [[Graded Calls Tracker]]
- If high-conf > 65%, that's a good sign for the research agent specifically (doesn't gate paper trading, which is already running)
- If high-conf < 55%, revisit the agent prompt

**Frequency:** Weekly from ~2026-07-30 onward.

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
