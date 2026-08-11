#!/bin/bash
# _run.sh — the single entry point launchd-style hooks go through, invoked by
# .claude/settings.json as:   .claude/hooks/_run.sh <hook-name>
#
# WHY a wrapper instead of inlining the command in settings.json.
#
# 1. The interpreter pin is load-bearing, exactly as it is in ftmo_runner.sh.
#    `python3` on this machine resolves to conda base (/opt/anaconda3), which
#    is a PARTIAL match for this project: pandas and yfinance yes, torch no.
#    The guards themselves only need the stdlib, but selftest_guard re-invokes
#    the interpreter to RUN a module's --selftest, and running that under the
#    wrong prefix reports a packaging problem that is really a path problem.
#    `.venv/bin/python3` is a symlink to the conda binary — that is normal for
#    a venv and is NOT the same environment: pyvenv.cfg moves sys.prefix to
#    the project venv, which is where torch actually lives.
#
# 2. Shell quoted inside JSON quoted inside a shell is where hooks go to die.
#    A file can be read, diffed, and tested by piping it a sample event.
#
# 3. It is tracked in git and re-read on every invocation, so changing a guard
#    needs no settings reload. Same reasoning as keeping caffeinate in
#    ftmo_runner.sh rather than the plist.
#
# It FAILS OPEN on anything structural — a missing hook file, no interpreter,
# a bad name. A guard that blocks every edit because its own plumbing broke is
# a worse failure than the one it was written to prevent.
set -u

name="${1:-}"
[ -n "$name" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
    root="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)" || exit 0
fi

hook="$root/.claude/hooks/$name.py"
[ -f "$hook" ] || exit 0

py="$root/.venv/bin/python3"
if [ ! -x "$py" ]; then
    py="$(command -v python3 2>/dev/null)" || exit 0
fi
[ -n "$py" ] || exit 0

exec "$py" "$hook" "$root"
