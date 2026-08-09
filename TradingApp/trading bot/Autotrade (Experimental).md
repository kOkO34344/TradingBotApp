---
tags: [autotrade, risk, experimental, execution]
source: autotrade_runner.py
status: "HISTORICAL — the IBKR runner was removed 2026-08-09; FTMO's is live, see [[FTMO Venue]]"
last_updated: 2026-08-09
---

# Autotrade (Experimental)

> [!danger] HISTORICAL — `autotrade_runner.py` was REMOVED on 2026-08-09
> This note describes the IBKR hourly runner, which no longer exists. Its
> `autotrade` block is also gone from `trader_settings.json`.
>
> It matters as the FIRST of three deliberate exceptions to the
> earn-autonomy-with-evidence rule: built 2026-07-24 at the owner's
> twice-confirmed request despite both candidate signals showing no measurable
> edge at that cadence (momentum-hourly IC −0.037 / 48.5%, Kronos-hourly
> −0.081 / 46.4%).
>
> The live unattended path is now `ftmo_runner.py`. See [[FTMO Venue]].


> [!important] There are now TWO autotrade paths, and this note is about the
> IBKR one.
>
> - **`autotrade_runner.py`** (this note) — IBKR, hourly, **OFF**, and the
>   venue itself is retired for new orders anyway (rule 9).
> - **`ftmo_runner.py`** — FTMO, daily at 01:15, **ARMED since 2026-08-06**.
>   See [[FTMO Venue]].
>
> They are deliberately **separate scripts with separate toggles**, and
> `autotrade.enabled` cannot arm FTMO — there is a selftest asserting exactly
> that. The two talk to different brokers and have different limit models; one
> switch covering both would mean you could not reason about FTMO without also
> reasoning about a retired venue.
>
> The FTMO switch is also **not gated on IB Gateway's health**, unlike the
> header kill switch. A dead Gateway has nothing to do with FTMO, and a switch
> you cannot reach when things are going wrong is not a switch.

## ⚠️ Current state (IBKR): OFF — and should stay off for now

`trader_settings.json`'s `autotrade` block reads
`{"enabled": false, "signal": "kronos", "allow_momentum": false}`. It was
armed on 2026-07-25 and has since been turned off. Three things before
re-arming:

**1. Two failure modes are still silent, and unattended trading assumes they
aren't.** From `Handoff.md` (repo root), both must be fixed first:
- `reflect_on_trades.py` calls `connect()` outside any try/except, so a
  refused connection kills the close monitor with **no Telegram**. It was
  already dead for most of 07-26 and 07-27 and looked like a quiet market.
- A rebalance can half-execute — exits run before entries, and an unfilled
  DAY entry limit leaves you in unintended cash with no alert. That is worse
  than doing nothing *or* doing everything.

**2. The signal is gated in code now** (2026-07-28, see
[[Strategy Decisions - Momentum Rotation]]). Kronos is the project's only
runnable signal; momentum raises `SignalDisabled` unless a caller passes
`allow_momentum=True`. **If `autotrade.signal` is ever set to a disabled
signal, the runner REFUSES to fire** — it logs, texts, and places nothing. It
never substitutes the other signal, because `acted=True` in the log with no
record of which signal chose the position is worse than not trading.

**3. Kronos's top-3 is not stable.** Two runs 30 minutes apart on identical
data produced different top-3s (`[AMZN, MSFT, GOOGL]` vs `[AMZN, MSFT, DIS]`)
— on an hourly cadence that is turnover paid for pure sampling noise, against
a signal with no measured edge. See [[Kronos Research Agent]].

**4. The traded universe grew** (14 tickers, see [[Watchlist Context]]) — the
hourly IC screen below was measured against the original set, not this one.

5. **The $300 daily-loss circuit breaker won't necessarily catch a bad
   stop-out** — it only evaluates when an order is about to be placed, not
   continuously. See [[Risk Management System]]'s 2026-07-25 update.

Also relevant: `paper_trader.execute_rebalance()` (which this feature calls
under the hood) had its EUR→USD sizing conversion fixed 2026-07-25 — it
previously needed a live FX quote and would fail with IBKR error 10197 under
certain conditions, which would have made every hourly firing error out
instead of trade. See [[IBKR Integration]].

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
- [[Risk Management System]] — RiskGuard, unchanged by this feature; the daily-loss breaker's pre-trade-only limitation
- [[Watchlist Context]] — the traded universe this now rebalances hourly
- [[IBKR Integration]] — the 2026-07-25 sizing fix that keeps this from erroring out on a missing market-data line
- [[00 MOC - Trading Bot Vault]]
