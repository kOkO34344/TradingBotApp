#!/bin/bash
# ftmo_reconcile.sh — wrapper for `ftmo_runner.py --reconcile`
# (com.tradingbotapp.ftmoreconcile). Runs every 30 minutes, ALL DAY, EVERY DAY.
#
# It places no orders, loads no model, and deliberately ignores both the arm
# toggle and the trading window: recording what the account did is not trading.
#
# TWO REASONS IT RUNS 24/7, and the second one is easy to miss.
#
# 1. RULE 6. Since 2026-08-11 the runner only fires 16:30-23:00 Mon-Fri, so a
#    stop or take-profit firing at 02:00 on a Saturday would otherwise go
#    unjournalled until Monday afternoon — 62 hours. That is the same hole the
#    2026-07-23 GOOGL close fell through for two days.
#
# 2. THE DAILY LIMIT'S BASELINE. The FTMO day rolls at 00:00 Europe/Prague =
#    01:00 Sofia, which the new window does NOT cover. `advance_state()`
#    samples day_start_balance at roll time, so without a job running near the
#    boundary the roll would happen at 16:30 — 15.5 hours late — and every
#    overnight move would be excluded from the daily loss. A limit that
#    under-reports is worse than no limit, because it looks like one.
#
# caffeinate is deliberately absent: this is a ~15-second job and holding a
# power assertion 48 times a day to cover it is not a trade worth making.
set -u
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python3 ftmo_runner.py --reconcile "$@"
