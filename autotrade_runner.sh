#!/bin/bash
# autotrade_runner.sh — invoked hourly by launchd during a window that
# covers NYSE hours (see com.tradingbotapp.autotrade.plist). The actual
# "is autotrade on, is the market open" decision is made inside
# autotrade_runner.py (America/New_York-aware) — this wrapper just runs it.
#
# Safe to run manually any time: ./autotrade_runner.sh
# (it will no-op unless autotrade.enabled is true in trader_settings.json
# AND the market is actually open — pass args through for manual testing,
# e.g. ./autotrade_runner.sh --force --dry-run)

set -u
cd "$(dirname "$0")" || exit 1
.venv/bin/python3 autotrade_runner.py "$@"
