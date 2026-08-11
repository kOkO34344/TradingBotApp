#!/usr/bin/env python3
"""journal_guard.py — PreToolUse hook: refuse in-place edits to the files that
ARE this project's evidence.

WHY THIS EXISTS
---------------
Rule 6 says every order attempt, block and fill goes to trade_journal.csv, and
that the 46 venue=ibkr rows stay forever: "An audit trail you prune when a
venue is retired is not an audit trail." Rule 3 says limits are enforced in
code, never in prompts.

Until this hook, rule 6 was enforced BY PROMPT. That is the arrangement this
project has been bitten by repeatedly, and it is the one thing CLAUDE.md is
most explicit about not trusting.

The calibration files are here for the same reason, and the incident is on the
record: graded_calls.csv carried four grades struck from two SYNTHETIC TEST
NOTES that had already been deleted, and daily_digest.py reported "4 graded,
0 pending" every morning for days — fabricated calibration evidence, presented
as a track record, in the one file that exists to gate autonomy. Grades are
struck once and cached deliberately, because a file the autonomy bar is read
from cannot change when you re-read it. Hand-editing it is how that stops
being true.

WHAT IT DOES NOT DO
-------------------
It blocks the Edit and Write TOOLS. It does not, and cannot, stop
trade_journal.append() — which is the correct writer and must stay
unobstructed, because it runs unattended. Nothing here touches the runner.

Exit 2 blocks the call and shows stderr to Claude. Exit 0 allows it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# path-suffix -> why it is protected, and the sanctioned way to change it
PROTECTED = {
    "trade_journal.csv": (
        "the rule-6 audit trail",
        "Append through trade_journal.append() with an explicit venue. A wrong\n"
        "  row is corrected by APPENDING a RESULT_CORRECTED or NOTE row and\n"
        "  leaving the original in place, annotated — that is what was done for\n"
        "  every 2026-07 incident. Never rewrite history here.\n"
        "  Column changes belong in trade_journal.py's self-healing migration,\n"
        "  never in this file: every reader is a csv.DictReader, which drops a\n"
        "  surplus value into the None restkey and reports NO error.",
    ),
    "graded_calls.csv": (
        "the calibration record the autonomy bar is read from",
        "Re-strike it with `python3 grade_calls.py --csv`, or `--refresh` to\n"
        "  deliberately re-grade. Do not hand-write a grade.",
    ),
    "grading_cache.json": (
        "the struck-once grade cache",
        "Grades are cached precisely so re-reading cannot change them —\n"
        "  yfinance returns slightly different bars run to run and scored the\n"
        "  same book 37%/34%/37%. Use `grade_calls.py --refresh` to re-strike.",
    ),
}

PROTECTED_DIRS = {
    "ftmo_audit": (
        "the runner's structured decision log",
        "It is append-only JSONL written by ftmo_audit.py, and /api/ftmo/timeline\n"
        "  reconstructs the night band from it — including the firings that were\n"
        "  DUE AND DID NOT HAPPEN. Editing it edits the record of what the bot\n"
        "  decided.",
    ),
}


def main() -> int:
    _root = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # unparseable event: fail open, never wedge the session

    target = (event.get("tool_input") or {}).get("file_path") or ""
    if not target:
        return 0

    path = Path(target)
    name = path.name

    what = PROTECTED.get(name)
    if what is None:
        for parent in path.parts:
            if parent in PROTECTED_DIRS and path.suffix == ".jsonl":
                what = PROTECTED_DIRS[parent]
                break

    if what is None:
        return 0

    description, remedy = what
    print(
        f"BLOCKED: {name} is {description} — it is not edited in place.\n\n"
        f"  {remedy}\n\n"
        "Rule 6: if it is not in the journal, it did not happen. Rule 3: limits\n"
        "are enforced in code, never in prompts — which is why this is a hook\n"
        "and not a paragraph. If you genuinely need to rewrite this file, do it\n"
        "deliberately from a shell, where it will be a visible act.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
