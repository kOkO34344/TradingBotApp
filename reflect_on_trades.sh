#!/bin/bash
# reflect_on_trades.sh — polls IBKR for newly-closed paper positions and
# triggers a headless self-review agent for each (win -> "why are we
# winning?", loss -> "why are we losing?"). Catches stop-loss/target fills
# that happen with nothing else running, not just paper_trader.py exits.
#
# Invoked periodically by ~/Library/LaunchAgents/com.tradingbotapp.tradereflect.plist.
# Safe to run manually any time: ./reflect_on_trades.sh
# No-op (fast, harmless) if IB Gateway isn't running or nothing new closed.

set -u
cd "$(dirname "$0")" || exit 1
LOG="trade_reflect.log"

echo "=== Trade reflection run: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
.venv/bin/python reflect_on_trades.py >> "$LOG" 2>&1
echo "=== Done: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
echo "" >> "$LOG"
