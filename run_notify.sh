#!/bin/bash
# run_notify.sh — generic "run any script in this project, text my phone
# when it's done" wrapper. Usable from any session (this chat, Claude
# Code, a plain terminal) for any one-shot script — backtests, a single
# research_agent.py ticker run, paper_trader.py --dry-run, etc.
#
# Usage:
#   ./run_notify.sh sma_crossover_backtest.py
#   ./run_notify.sh orb_backtest.py
#   ./run_notify.sh strategy_shootout.py
#   ./run_notify.sh KronosAI/kronos_backtest.py --sample-count 5
#   ./run_notify.sh research_agent.py AAPL
#
# Detached, survives closing the terminal:
#   nohup ./run_notify.sh <script> [args...] > /dev/null 2>&1 & disown
#
# NOT for recurring/polling jobs (reflect_on_trades.sh runs every 30 min
# via launchd) — see CLAUDE.md's automation section for why.

set -u
cd "$(dirname "$0")" || exit 1
.venv/bin/python3 run_notify.py "$@"
