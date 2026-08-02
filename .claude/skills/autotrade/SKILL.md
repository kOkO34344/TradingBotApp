---
name: autotrade
description: Operational detail for TradingBotApp's unattended hourly autotrade runner. Use before touching autotrade_runner.py or autotrade_signals.py, when changing the autotrade toggle/schedule/signal in trader_settings.json or com.tradingbotapp.autotrade.plist, or when asked how to enable, disable, or audit autotrade. Read alongside CLAUDE.md rules 7 and 8, which stay authoritative.
---

# Autotrade (experimental, unattended) — `autotrade_runner.py`

**Rule 7 in `CLAUDE.md` governs this file and is not restated here — read it
first.** This skill carries the operational detail only.

Built 2026-07-24. Unattended hourly rebalancing: no y/n prompt, RiskGuard
fully enforced regardless. **Built despite both eligible signals showing no
measurable edge at this cadence** (see Empirical findings in `CLAUDE.md`) — a
deliberate live paper experiment at the owner's explicit, twice-confirmed
request, not because either signal is validated.

- **Toggle:** `trader_settings.json`'s `"autotrade": {"enabled": bool,
  "signal": "kronos", "allow_momentum": false}`. Set via `trader_app.py` menu
  item 8, or edit the JSON directly. Defaults to `enabled: false`. Per rule 8
  the only signal that will actually fire is `kronos`; setting `momentum` here
  makes the runner **refuse to fire and text you**, placing nothing — it never
  silently swaps in the other signal. `trader_app.py` menu 8 says so at the
  moment you pick it, rather than letting a setting look applied and quietly
  do nothing.
- **Schedule:** `com.tradingbotapp.autotrade.plist`, hourly 16:00-23:00 local
  (this machine runs EEST/EET) — a superset of NYSE 9:30-16:00 ET year-round
  (the two DST regimes keep a constant ~7h gap). `autotrade_runner.py` does
  its own authoritative America/New_York market-hours check on every firing
  (`zoneinfo`, not host time) — the launchd schedule only needs to cover the
  window, not match it exactly.
- **Signal:** `autotrade_signals.py` — hourly bars (yfinance, ~2-3yr history,
  separate cache `KronosAI/price_data_hourly_live/` from the backtest's
  `price_data_hourly/`), same LOOKBACK=400/PRED_LEN=20 bar counts the IC
  screen used. `ind.atr()` on hourly bars gives a 14-HOUR stop distance, not
  14-day — deliberately tighter, appropriate for the shorter intended hold.
- **Execution:** `paper_trader.execute_rebalance(..., auto_approve=True)` —
  the exact same sizing/RiskGuard/bracket-order function the human-approved
  path uses (extracted 2026-07-24 specifically so both paths can never
  diverge in risk handling). `client_id=13` (distinct from trader_app's 7,
  paper_trader's 9, reflect_on_trades' 11 — lets all run concurrently).
- **Notifications:** texts on any executed trade or error; silent on no-op
  cycles (same convention as `reflect_on_trades.py`). Every cycle — no-op or
  not — gets one line in `autotrade_runner.log` regardless; that log is what
  to check to confirm it's actually alive, since Telegram silence on a quiet
  market day is expected, not a sign of failure.
- **Known real risk, not just theoretical:** RiskGuard's per-order/position/
  daily-loss limits don't guard against slow bleed from turnover costs on a
  no-edge signal trading far more often than the validated monthly cadence.
  `trade_journal.csv` is the audit trail for catching that — check it
  periodically, don't just assume silence means it's fine.
- **To disable:** turn the toggle off (safest, keeps the job installed for
  later), or `launchctl unload ~/Library/LaunchAgents/com.tradingbotapp.autotrade.plist`
  to stop it firing entirely.
