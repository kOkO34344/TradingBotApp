#!/bin/bash
# daily_digest.sh — texts a "plan the day" (morning) or "recap the day"
# (evening) digest built from CLAUDE.md + a few on-disk freshness/activity
# checks. No LLM call — fast and free.
#
# Invoked daily by two launchd jobs:
#   ~/Library/LaunchAgents/com.tradingbotapp.dailydigest.plist        (07:30, morning)
#   ~/Library/LaunchAgents/com.tradingbotapp.dailydigestevening.plist (20:00, evening)
#
# Safe to run manually any time: ./daily_digest.sh [morning|evening]  (defaults to morning)

set -u
cd "$(dirname "$0")" || exit 1
LOG="daily_digest.log"
MODE="${1:-morning}"

echo "=== Daily digest run ($MODE): $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
.venv/bin/python3 daily_digest.py "$MODE" >> "$LOG" 2>&1
echo "=== Done: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
echo "" >> "$LOG"
