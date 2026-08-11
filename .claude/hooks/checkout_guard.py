#!/usr/bin/env python3
"""checkout_guard.py — PreToolUse hook: refuse a wholesale git revert while
trader_settings.json is modified-but-uncommitted.

WHY THIS EXISTS
---------------
CLAUDE.md, verbatim: "trader_settings.json carries the armed flag and is
tracked in git. If it is left modified-but-uncommitted, a stray
`git checkout .` silently DISARMS the runner. That direction fails safe, but
it fails *quietly* — check the flag before concluding the bot is running."

Quiet is the whole problem. This project has now lost a day to a failure that
announced itself nowhere (22 sleep failures, 19 hours) and two days to a close
that nothing recorded. A state change with no operator visible to see it is
the recurring shape.

It is deliberately NOT a blanket ban on `git checkout .`. It fires only when
that command would actually change the armed flag, and it says which way. A
guard that cries wolf on every revert gets disabled within a week.

Note the flag can move in EITHER direction: reverting a working tree where you
have just armed it disarms the runner, and reverting one where you have just
disarmed it re-arms a bot you thought you had stopped. Both are reported.

Exit 2 blocks the call. Exit 0 allows it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SETTINGS = "trader_settings.json"

# Commands that discard uncommitted work in the tree wholesale. `git stash` is
# included because it hides the change just as effectively, and the operator
# who runs it is usually not thinking about a launchd job.
DESTRUCTIVE = [
    re.compile(r"\bgit\s+checkout\s+(--\s+)?\.(\s|$)"),
    re.compile(r"\bgit\s+checkout\s+.*\btrader_settings\.json\b"),
    re.compile(r"\bgit\s+checkout\s+-f\b"),
    re.compile(r"\bgit\s+restore\s+(--\S+\s+)*(--\s+)?\.(\s|$)"),
    re.compile(r"\bgit\s+restore\s+.*\btrader_settings\.json\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+stash\b(?!\s+(list|show|pop|apply))"),
    re.compile(r"\bgit\s+clean\s+-\S*[fx]"),
]


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _armed(blob: str | None) -> bool | None:
    """The armed flag as ftmo_runner reads it. A missing key is OFF."""
    if blob is None:
        return None
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    ftmo = data.get("ftmo")
    if not isinstance(ftmo, dict):
        return False
    autotrade = ftmo.get("autotrade")
    if not isinstance(autotrade, dict):
        return False
    return bool(autotrade.get("enabled", False))


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    command = (event.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0
    if not any(pattern.search(command) for pattern in DESTRUCTIVE):
        return 0

    # Does this tree actually have an uncommitted change to the flag file?
    status = _git(root, "status", "--porcelain", "--", SETTINGS)
    if status is None or not status.strip():
        return 0  # nothing to lose — let it through

    settings_path = root / SETTINGS
    try:
        working = _armed(settings_path.read_text(encoding="utf-8"))
    except OSError:
        working = None
    committed = _armed(_git(root, "show", f"HEAD:{SETTINGS}"))

    if working is None or committed is None or working == committed:
        # Modified, but not in a way that moves the flag. Say so briefly and
        # allow it — this is the common case and must not be friction.
        return 0

    direction = (
        "DISARM the runner (armed in your tree, disarmed at HEAD)"
        if working and not committed
        else "RE-ARM the runner (disarmed in your tree, armed at HEAD)"
    )

    print(
        f"BLOCKED: this would silently {direction}.\n\n"
        f"  command: {command.strip()}\n"
        f"  {SETTINGS} is modified and its ftmo.autotrade.enabled flag\n"
        f"  differs from HEAD: working={working}  HEAD={committed}\n\n"
        "CLAUDE.md: 'a stray `git checkout .` silently DISARMS the runner. That\n"
        "direction fails safe, but it fails quietly — check the flag before\n"
        "concluding the bot is running.'\n\n"
        "Pick one, deliberately:\n"
        f"  • keep the change:   git add {SETTINGS} && git commit\n"
        f"  • drop only this:    git checkout -- {SETTINGS}   (then re-run)\n"
        "  • it is what you want: run it from a shell yourself.\n\n"
        "The runner fires at :30. Whichever way this flag lands is the state it\n"
        "fires in, and nothing will tell you which.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
