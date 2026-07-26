#!/bin/bash
# run_kronos_backtest_notify.sh — runs the Kronos backtest and texts Koko's
# phone (via Telegram) when it finishes, fails, or stalls for 20+ minutes.
#
# Usage:
#   ./run_kronos_backtest_notify.sh                       # defaults
#   ./run_kronos_backtest_notify.sh --sample-count 5       # args pass through
#   ./run_kronos_backtest_notify.sh --seed 7
#
# Safe to run in the foreground (you'll see live output as it streams) or
# fully detached from the terminal so it survives closing the window:
#   nohup ./run_kronos_backtest_notify.sh > /dev/null 2>&1 &
#
# One-time setup required before this will actually notify anything — see
# notify.py's module docstring (create TradingBotApp/.env with your
# Telegram bot token + chat id). Without it, this still runs the backtest
# and logs to backtest_logs/, it just skips the phone notification.

set -u
cd "$(dirname "$0")" || exit 1
../.venv/bin/python3 run_kronos_backtest_notify.py "$@"
