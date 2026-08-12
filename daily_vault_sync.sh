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

# claude reads its credentials from the login keychain and needs USER set to
# find them; without it every run dies with "Not logged in · Please run /login"
# while the account is perfectly logged in. launchd's environment is minimal,
# so this is derived rather than assumed.
export USER="${USER:-$(id -un)}"

# The claude path is PINNED, the same way every other launcher here pins
# .venv/bin/python3. launchd runs this via `bash -lc`, which sources
# ~/.bash_profile — and that file holds only the conda init block. The
# `export PATH="$HOME/.local/bin:$PATH"` that makes `claude` resolvable lives
# in ~/.zshrc, which bash never reads. So a bare `claude` is not found under
# launchd while working perfectly from an interactive shell, which is exactly
# how this failed silently every night from 2026-08-01 to 2026-08-11.
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
if [ ! -x "$CLAUDE_BIN" ]; then
  CLAUDE_BIN="$(command -v claude || true)"
fi
if [ -z "$CLAUDE_BIN" ] || [ ! -x "$CLAUDE_BIN" ]; then
  echo "FAILED: claude CLI not found (tried \$HOME/.local/bin/claude and PATH=$PATH)" >> "$LOG"
  echo "=== Done (failed): $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
  echo "" >> "$LOG"
  exit 1
fi

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

"$CLAUDE_BIN" -p "$PROMPT" \
  --permission-mode acceptEdits \
  --allowedTools "Read Edit Bash(git status) Bash(git diff:*) Bash(git add:*) Bash(git commit:*) Bash(git log:*) Bash(date:*)" \
  >> "$LOG" 2>&1

echo "=== Done: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
echo "" >> "$LOG"
