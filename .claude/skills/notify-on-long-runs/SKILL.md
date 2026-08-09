---
name: notify-on-long-runs
description: How to get phone notifications from TradingBotApp scripts. Use when starting a backtest, research run, or any long-running one-shot script (run_notify.sh wrapper), when adding a new recurring or polling script that should notify, or when deciding whether a given script already notifies on its own and must NOT be wrapped.
---

# Phone notifications (TelegramBot/) — use this for anything long-running

One Telegram bot, `TelegramBot/notify.py` (`send_telegram(text)`), reused
across the project. Owner gets a phone text; credentials live in
`TelegramBot/.env` (gitignored, see `TelegramBot/README.md` for setup).

**Any future session (Claude Code or otherwise) starting a backtest or
other one-shot script should default to running it through the generic
wrapper, not calling the script directly** — this is how "notify me from
any session" actually holds:

```bash
./run_notify.sh sma_crossover_backtest.py
./run_notify.sh KronosAI/kronos_backtest.py --sample-count 5
./run_notify.sh research_agent.py AAPL
nohup ./run_notify.sh <script> [args] > /dev/null 2>&1 & disown   # detached
```

It texts on start, on a 20-min output stall (possible hang), and on
done/failed with a tail of the output. Full logs land in `run_logs/`.

Other notification points already wired in (do NOT wrap these in
`run_notify.sh` too — they'd double-notify or spam a no-op poller):
- `run_research_agent_watchlist.sh` — loops the whole watchlist, sends
  ONE consolidated direction+confidence digest instead of one per ticker.
- `ftmo_runner.py` — one consolidated text per firing that actually traded,
  listing entries and exits, plus an alert on any error or limit breach.
  Silent on no-op cycles. It wakes ~20x a day and is a no-op most of them,
  so the notification lives INSIDE the script, conditional on a real event —
  never wrap the whole script.
- `ftmo_closes.py` (via the runner's `--reconcile`) — texts on every
  position it finds closed without the runner, with the venue's own closing
  price and P&L.
- `daily_digest.py` — two modes, one script:
  - `--mode morning` (default) via `com.tradingbotapp.dailydigest.plist`,
    07:30 local — "plan the day": due-today/freshness flags + Work Queue
    + Empirical Findings, quoted close to verbatim from `CLAUDE.md`.
  - `--mode evening` via `com.tradingbotapp.dailydigestevening.plist`,
    20:00 local (before the 22:00 vault sync) — "recap the day": today's
    actual activity (trade_journal.csv entries since midnight, new
    research notes logged today, new trade_reflections/ files today),
    plus the same freshness flags as a heads-up for tomorrow.
  No LLM call either way, just file reads — keep CLAUDE.md's Work Queue
  and Empirical Findings sections reasonably current since both digests
  quote them directly. Manual run: `./daily_digest.sh [morning|evening]`.

If you add a new recurring/polling script, give it its own conditional
`send_telegram()` call at the actual event, the same way `ftmo_runner.py`
does — don't put a blanket wrapper around a poller.
