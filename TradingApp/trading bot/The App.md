---
tags: [trading-bot, project-overview]
status: "Phase 3 live: 4 open paper positions (AAPL/JNJ/DIS/AMZN), all GTC-stopped. Signal is KRONOS as of 2026-07-28; momentum validated but disabled in code. Launch with ./trader_app.sh. Research grading: 0 real graded calls so far, first ones ~2026-07-29."
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-21
---

# Trading Bot Project — Overview

> [!note] 2026-08-02 — FTMO is now the trading venue
> Five new modules (`ftmo_rules`, `ftmo_monitor`, `ftmo_sizing`, `ftmo_audit`,
> `ftmo_service`) sit alongside the existing app, with 259 offline selftests.
> IBKR is retired in place. The terminal app and web UI remain IBKR-only.
> See [[FTMO Venue]].

This is the index note for the trading-bot project. The actual code lives **outside the vault**, at `/Users/kaloyanivanov/TradingBotApp` on this Mac — these notes are a synced summary of what's there, kept accurate as the project moves forward.

## Where things stand right now

**Backtesting (the original plan's Phase 2) is done, and the result was a real finding, not a bug:** the strategy named in the original plan (20/50-day SMA crossover) **does not beat buy-and-hold**, in-sample or out-of-sample, on any of the 10 original test tickers. A follow-up strategy shootout tested five rule families head-to-head — only **monthly momentum rotation** was competitive, nearly matching SPY's return with a third less drawdown.

→ Full detail, numbers, and sourcing: **[[trading bot/Backtest Results & Findings]]**

**Note on phase numbering:** the project's own `CLAUDE.md` now uses a slightly different phase scheme than the original plan document ([[trading bot/Plan]]) does — it calls the IBKR/broker-infrastructure work "Phase 2" and treats the SMA/momentum backtesting as already folded into the evidence base rather than a numbered phase of its own. This note and [[trading bot/Plan]] keep the original document's phase numbers for consistency; where it matters, both numbering schemes are called out explicitly rather than silently picking one.

**Four real developments since the last sync:**

1. **Phase 1 research agent (`research_agent.py`) has real output now.** All 12 watchlist tickers (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR) have been analyzed for real — the old two synthetic placeholder notes are gone. Spot-checking a couple of the notes (AAPL, NVDA) shows genuinely grounded reasoning: no invented price levels, calibrated confidence, and a majority "no-edge" verdict rather than forced directional calls, consistent with the project's own calibration rules. **`grade_calls.py` correctly hasn't been re-run yet** — the earliest note (07-20) is only ~1 day old as of 2026-07-21, short of the 5-day horizon. Grading starts ~2026-07-25.
2. **The IBKR connection is verified live, not just built.** IBKR had the paper account in address-verification review; that cleared 2026-07-21. A connected smoke test then passed for real: `verify_paper_account()` succeeded against IB Gateway paper (port 4002), account `DUQ903866`, and pulled 45 real rows of AAPL 15-min bars. Settings were updated to match (port 7497 → 4002).
3. **`ibkr_service.py`'s execution layer is hardened AND now exercised for real.** Three enforcement layers sit in code in front of any order: paper-account verification, a `RiskGuard` (limits in `risk_limits.json` — $5,000 max order notional, 5 max positions, $300 daily-loss circuit breaker, stop required), and a `trade_journal.csv` audit trail for every attempt/block/fill. `place_bracket_order` (limit entry + stop, atomic) is the default entry path; bare `place_market_order` is refused unless explicitly overridden. All 18 offline self-test checks pass. **It has now been called against the real (paper) account** — see #4.
4. **Phase 3 (`paper_trader.py`) is built and has executed a real rebalance.** Momentum-rotation signal → diff against live IBKR positions → printed proposal → explicit y/n → bracket-order execution, sized from RiskGuard's risk budget. First run, 2026-07-21: bought GOOGL (14 sh), AAPL (15 sh), JNJ (19 sh) on the paper account. Two real bugs surfaced and got fixed the same day: a RiskGuard position-count check misapplied to closes, and — more seriously — the bracket stop leg defaulted to a DAY time-in-force and **silently expired at end of session**, briefly leaving all three positions unprotected. Fixed to `tif="GTC"`; all three positions were manually re-protected the same day. Full detail: [[IBKR Integration]], [[Risk Management System]].

A fifth, smaller thing: `trading_agent_service.py` (the third-party TradingAgents wrapper, a second Phase-1 candidate) is **still never run** — it hasn't lost any ground to `research_agent.py`, it's just further behind now that `research_agent.py` has real output and this doesn't.

**Real (paper) money is now moving.** Three positions are open on the paper account with real fills in `trade_journal.csv`. No *live* money has moved — per [[trading bot/Plan]], that's Phase 4, locked until months of clean Phase 3 evidence — but Phase 3 itself is no longer a future step, it's running.

## Notes in this folder

| Note | What it's for |
|---|---|
| [[trading bot/Plan]] | The original 5-phase build plan (research agent → backtest → paper trading → tiny live capital), with a status banner showing progress against it |
| [[trading bot/README_trader_app]] | How to install and run `trader_app.py` on this machine, current menu, current file layout, and the IBKR/research-agent details |
| [[trading bot/Backtest Results & Findings]] | The actual numbers: SMA crossover results, the 5-strategy shootout, day-trading research findings, and what they mean for what to build next |

## Current app snapshot

(from `trader_settings.json`, synced 2026-07-23)

- **Watchlist:** AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR, AVGO, ASML (14 now — AVGO and ASML added since last sync) — plus SPY as benchmark
- **SMA windows:** 20 / 50 (default, known to lose to buy-and-hold)
- **Cost model:** 0.1% per trade
- **Starting cash (sim):** $1,141,420 — this now reflects the actual IBKR paper account balance, not the original $10,000 placeholder
- **Risk engine:** off by default — togglable (trend filter + 2×ATR trailing stop + fixed % risk per trade)
- **Momentum rotation:** top 3, 12-month lookback — the strategy with the best evidence so far
- **Kronos forecast:** new menu item (7) — a foundation-model forecaster, analysis only in the app, unvalidated. See [[Kronos Research Agent]].
- **Launch with `./trader_app.sh`, NOT `python3 trader_app.py`** (new 2026-07-28). On this machine `python3` is conda base, which has pandas/rich/yfinance/ib_async but **not torch** — so the app starts and backtests fine and only the Kronos menu fails, with `No module named 'torch'` even though torch is installed in `.venv`. A partial environment is a good disguise. The launcher pins `.venv/bin/python`; the app also warns at startup if it's running under the wrong interpreter, and the Kronos import error now distinguishes "wrong interpreter" from "genuinely missing".
- **IBKR:** port **4002** (IB Gateway paper — switched from TWS's 7497 now that the Gateway connection is the one that's actually verified), client id 9 — read-only in the *app* (`trader_app.py` menu), but `paper_trader.py` (separate script) now executes real orders through this connection. **Signal is Kronos by default** as of 2026-07-28; momentum requires `--signal momentum --allow-momentum` and is otherwise refused.

## Recommended next steps

Phase 3 is live now, so this is no longer a "what to build" list — it's an operating rhythm:

1. **Keep `paper_trader.py` running** — monthly rebalance or on-demand, `--dry-run` first if unsure what it'll propose. Periodically verify open positions have working stops that are **GTC, not DAY** (see the bug in [[IBKR Integration]] / [[Risk Management System]]) — a DAY stop looks identical to GTC for hours before silently vanishing.
2. **Run `python3 grade_calls.py --csv` once notes are ≥5 days old** (~2026-07-25+), then weekly. Not urgent before that point — running early just shows "pending" rows.
3. **Re-run `research_agent.py` on the watchlist weekly** (next due ~2026-07-28) to keep the evidence base growing.
4. Momentum rotation still hasn't been through portfolio-level walk-forward rigor (max-N-positions constraint, drawdown circuit breaker) — worth doing in parallel with the live paper-trading window, not as a gate before it.
5. `trading_agent_service.py` vs. `research_agent.py`: still an open decision, lower priority.
6. Web UI (`TraderAppFullStack.txt`, a FastAPI + React spec found in the folder) is now **legitimately unblocked** — real fills exist in `trade_journal.csv` — but still lower priority than 1-3, which is where the actual evidence this project is gated on comes from.

See [[trading bot/Plan]] for how these map onto the phase structure.
