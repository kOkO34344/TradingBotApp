#!/bin/bash
# run_research_agent_watchlist.sh — runs research_agent.py across the whole
# watchlist and texts one consolidated digest (direction + confidence per
# ticker) when done, instead of pinging once per ticker.
#
# Usage:
#   ./run_research_agent_watchlist.sh                # full watchlist
#   ./run_research_agent_watchlist.sh AAPL MSFT       # subset
#   ./run_research_agent_watchlist.sh --group Tech    # one watchlist group
#   ./run_research_agent_watchlist.sh --list-groups
#
# Detached, survives closing the terminal:
#   nohup ./run_research_agent_watchlist.sh > /dev/null 2>&1 & disown

set -u
cd "$(dirname "$0")" || exit 1
.venv/bin/python3 run_research_agent_watchlist.py "$@"
