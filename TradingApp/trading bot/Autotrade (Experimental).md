---
tags: [autotrade, risk, experimental, execution]
source: autotrade_runner.py
status: "Built, loaded, OFF by default"
last_updated: 2026-07-24
---

# Autotrade (Experimental)

## What it is

An unattended hourly rebalancing loop — no human `y/n` approval, unlike
everything else in Phase 3. Built 2026-07-24 at the owner's explicit,
twice-confirmed request, **despite both eligible signals showing no
measurable edge at this cadence** (see below). This is the one deliberate,
documented exception to [[ADR - Python Rules, Not Model Predictions]]'s
approval-gate philosophy — not a quiet erosion of it. See CLAUDE.md rule 7.

## Why it exists despite the negative evidence

The owner asked for a trade toggle: flip it on, the agent trades until
flipped off. Before building it, two things got tested first (per the
project's usual "backtest before you trust it" discipline):

1. **Daily-cadence Kronos backtest** (2026-07-23, see [[Kronos Research Agent]]) —
   no edge (IC 0.036, 50% hit rate).
2. **Hourly-cadence IC screen** (2026-07-24, `KronosAI/kronos_ic_hourly.py`,
   run specifically because the owner wanted faster-than-monthly rebalancing) —
   also no edge, for BOTH candidate signals:
   - Momentum-style ranking (trailing 400-bar return): IC **-0.037**, hit rate **48.5%**
   - Kronos (same LOOKBACK/PRED_LEN bar counts, hourly data): IC **-0.081**, hit rate **46.4%**
   - 336 pooled (date, ticker) pairs, both indistinguishable from noise.

Told this twice, the owner chose to build it anyway as a live paper
experiment — an informed, deliberate choice to observe an unvalidated
signal running unattended, not a validation result being ignored.

## How it works

- **Toggle:** `trader_settings.json`'s `autotrade: {enabled, signal}`. Set
  via `trader_app.py` menu item 8 (or edit the JSON). **Defaults to off.**
- **Schedule:** `com.tradingbotapp.autotrade.plist`, hourly 16:00-23:00
  local time (this machine runs EEST/EET) — a superset of NYSE hours
  year-round. `autotrade_runner.py` does its own authoritative
  America/New_York market-hours check on every firing (`zoneinfo`) — most
  firings will no-op even when the toggle is on, simply because the
  schedule window is wider than the actual market hours it contains.
- **Signal:** `autotrade_signals.py` computes a live hourly ranking
  (momentum-style trailing return, or Kronos's forecast) — same bar-count
  parameters (`LOOKBACK=400`, `PRED_LEN=20`) the IC screen tested.
- **Execution:** runs through `paper_trader.execute_rebalance(...,
  auto_approve=True)` — the SAME function the human-approved path uses,
  extracted specifically so both paths can never diverge in risk handling.
  RiskGuard's notional/position/daily-loss limits are fully unchanged;
  the only thing removed is the interactive prompt.
- **Notifications:** texts on any executed trade or error; silent on
  no-op cycles (same convention as the trade-reflection automation). Every
  cycle still gets one line in `autotrade_runner.log` — that's what to
  check to confirm it's actually alive, since Telegram silence on a quiet
  day is expected, not a failure signal.

## Real risk to watch for (not just theoretical)

RiskGuard's limits guard against a single bad order or a single bad day —
they don't guard against **slow bleed from turnover costs** on a no-edge
signal trading far more often than the validated monthly cadence. Nothing
currently caps total trades/day or tracks cumulative churn cost
specifically. `trade_journal.csv` (cross-referenced against
`autotrade_runner.log` timestamps) is the audit trail for catching this —
it needs to actually be checked periodically, not just trusted to be fine
because nothing's texted an alert.

## A bug worth remembering

The first version of `autotrade_runner.py` imported `autotrade_signals`
(which pulls in `torch`) at module level — meaning every hourly firing
would pay torch's load cost even with the toggle off. Caught before
shipping by testing the disabled path with `torch` unavailable in the
environment; fixed by moving the import to be lazy, after the
enabled/market-hours checks. The lesson generalizes: anything that fires
on a schedule regardless of its own on/off state should be cheap in the
off state — check that explicitly, don't assume it.

## To disable

- **Toggle off** (safest, keeps the launchd job installed for later):
  `trader_app.py` menu item 8.
- **Stop it firing entirely:**
  `launchctl unload ~/Library/LaunchAgents/com.tradingbotapp.autotrade.plist`

## Related Notes

- [[Kronos Research Agent]] — the daily-cadence backtest this feature's design built on
- [[ADR - Python Rules, Not Model Predictions]] — the approval-gate philosophy this is the documented exception to
- [[Risk Management System]] — RiskGuard, unchanged by this feature
- [[00 MOC - Trading Bot Vault]]
