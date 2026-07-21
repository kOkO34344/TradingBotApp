#!/bin/bash
# daily_vault_sync.sh — runs Claude Code headlessly to keep the Obsidian vault
# (TradingApp/trading bot/*.md) in sync with actual project state. Scoped to
# ONLY edit/commit files under "TradingApp/trading bot/" — never touches code.
#
# Invoked daily by ~/Library/LaunchAgents/com.tradingbotapp.vaultsync.plist.
# Safe to run manually any time: ./daily_vault_sync.sh

set -u
cd "$(dirname "$0")" || exit 1
LOG="vault_sync.log"

echo "=== Vault sync run: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

PROMPT="Sync the Obsidian vault under 'TradingApp/trading bot/' to match the
actual current state of this project. You are only allowed to read files
anywhere in the repo, and to Edit/commit files under 'TradingApp/trading
bot/' — never touch, stage, or commit any file outside that directory.

Steps:
1. Read CLAUDE.md (project root) for current phase status and work queue —
   this is the authoritative source of truth.
2. Check git log, research_log/ (file dates/count), trade_journal.csv, and
   risk_limits.json for anything CLAUDE.md doesn't already summarize.
3. Compare against the living-status vault files: '00 MOC - Trading Bot
   Vault.md', 'Phase Milestones Dashboard.md', 'Next Build Steps.md', 'IBKR
   Integration.md', 'Risk Management System.md', 'Call Grading System.md',
   'Plan.md', 'The App.md', 'README_trader_app.md'.
4. Update ONLY what's actually stale (status tables, phase markers, dates,
   'not started'/'overdue' framing that's no longer true). Do NOT touch the
   ADR notes or 'Strategy Decisions - *.md' files — those are static
   decision records, not living status, and don't go stale just because the
   project moved forward.
5. If genuinely nothing changed since the last sync, make no edits and no
   commit — don't manufacture busywork.
6. If you made edits, stage ONLY the files you changed under 'TradingApp/
   trading bot/' and commit with a clear message starting with 'Daily vault
   sync:'. Never use git add -A or git add ..
7. Print a one-paragraph summary of what changed (or 'no changes needed')."

claude -p "$PROMPT" \
  --permission-mode acceptEdits \
  --allowedTools "Read Edit Bash(git status) Bash(git diff:*) Bash(git add:*) Bash(git commit:*) Bash(git log:*) Bash(date:*)" \
  >> "$LOG" 2>&1

echo "=== Done: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
echo "" >> "$LOG"
