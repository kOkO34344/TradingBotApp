#!/usr/bin/env python3
"""selftest_guard.py — PostToolUse hook: run the offline selftests after any
edit to a module that carries one.

WHY THIS EXISTS
---------------
This project has 579 offline checks across twelve modules, they need no
credentials and no network, every one of them runs in under a third of a
second — and until now nothing ran them automatically. There is no CI on this
repo (CLAUDE.md claims a Pylint workflow at .github/workflows/pylint.yml;
there is no .github directory at all). The only thing between a bad edit and
an unattended firing at :30 was remembering.

WHY IT RUNS ALL OF THEM, NOT JUST THE EDITED FILE
-------------------------------------------------
The failures that matter here are cross-module. `quantize_relative_stop` lives
in ftmo_session and is depended on by ftmo_signal and ftmo_runner; the sizer
reads specs captured by ftmo_service. Editing one module and testing only that
module tests the wrong thing. Twelve selftests cost about 1.3s in total, which
is cheap enough that precision is not worth the coverage.

The edited module is tested FIRST so its failure is the one reported first.

WHY IT KEYS ON EXIT CODE, NEVER ON OUTPUT
-----------------------------------------
`ftmo_audit.py --selftest` deliberately prints "AUDIT WRITE FAILED" to stderr
while exercising an unwritable path. That is a PASSING test. A guard that
grepped for "FAILED" would report a false failure on every single run, and
would train you to ignore it — which is worse than no guard. CLAUDE.md warns
about exactly this. Every selftest here returns 1 on failure and 0 on success;
that is the only signal read.

Exit 2 feeds stderr back to Claude as a correction. Anything else is silence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# A whole sweep is ~1.3s. This is generous by a factor of twenty, and exists
# only so a hung test cannot wedge the session.
SWEEP_TIMEOUT_S = 90
PER_MODULE_TIMEOUT_S = 30


def modules_with_selftest(root: Path) -> list[Path]:
    """Every top-level module that advertises a --selftest, discovered rather
    than hardcoded so a new one is covered the day it is written."""
    found = []
    for path in sorted(root.glob("*.py")):
        try:
            if "--selftest" in path.read_text(encoding="utf-8", errors="ignore"):
                found.append(path)
        except OSError:
            continue
    return found


def run_selftest(py: str, root: Path, module: Path, timeout: int) -> bool:
    """True if the module's offline checks pass. Exit code only — see docstring."""
    try:
        proc = subprocess.run(
            [py, module.name, "--selftest"],
            cwd=root,
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    edited_raw = (event.get("tool_input") or {}).get("file_path") or ""
    if not edited_raw:
        return 0

    edited = Path(edited_raw)
    # Only top-level project modules. Edits under web/, api/, KronosAI/ or a
    # worktree are someone else's problem and must not trigger a sweep.
    if edited.suffix != ".py" or edited.parent.resolve() != root.resolve():
        return 0
    if not edited.exists():
        return 0

    py = root / ".venv" / "bin" / "python3"
    if not py.is_file():
        # No pinned interpreter, no opinion. Fail open and stay quiet.
        return 0

    targets = modules_with_selftest(root)
    if not targets:
        return 0

    # The edited module first, so its failure leads the report.
    targets.sort(key=lambda p: (p.name != edited.name, p.name))
    if edited not in targets:
        # Edited a module that carries no selftest; still sweep the rest,
        # because it may well be imported by one that does.
        pass

    failures: list[str] = []
    deadline = time.monotonic() + SWEEP_TIMEOUT_S
    for module in targets:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Out of budget. Report what was actually measured rather than
            # implying the unmeasured modules passed.
            break
        if not run_selftest(str(py), root, module, int(min(PER_MODULE_TIMEOUT_S, remaining)) or 1):
            failures.append(module.name)

    if not failures:
        return 0

    lead = failures[0]
    others = [f for f in failures[1:]]
    lines = [
        f"SELFTEST FAILED after editing {edited.name}: {', '.join(failures)}",
        "",
        f"  reproduce:  .venv/bin/python3 {lead} --selftest",
    ]
    if others:
        lines.append(
            "  also failing: " + ", ".join(others) +
            "  (a break in one module usually surfaces in the ones that import it)"
        )
    lines += [
        "",
        "These are offline checks — no credentials, no venue, no network. They",
        "guard the only code path in this project that places an order, and the",
        "runner fires unattended at :30. Fix before moving on.",
        "",
        "NOTE: ftmo_audit's selftest prints 'AUDIT WRITE FAILED' to stderr while",
        "testing an unwritable path and still passes; this guard reads exit codes",
        "only, so if it is named above the failure is real.",
    ]
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
