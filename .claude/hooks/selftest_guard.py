#!/usr/bin/env python3
"""selftest_guard.py — PostToolUse hook: run the offline selftests after any
edit to a module that carries one.

WHY THIS EXISTS
---------------
This project has 762 offline checks across thirteen top-level modules plus 41
in api/ftmo_api.py, they need no credentials and no network, every one of them
runs in under a third of a second — and until this hook existed nothing ran
them automatically. There is no CI on this repo (CLAUDE.md claimed a Pylint
workflow at .github/workflows/pylint.yml; there is no .github directory at
all). The only thing between a bad edit and an unattended firing was
remembering.

WHY api/ IS INCLUDED (2026-08-12)
---------------------------------
It was excluded, and that exclusion hid a real regression for a day.
`api/ftmo_api.py`'s selftest went red on 2026-08-11 when the trading window
changed under it — five failing checks asserting a 16:30->11:30 session and 20
hourly slots — and nothing reported it, because the sweep only ever looked at
top-level modules. The backend is not a lesser tier: it reconstructs the night
band, which is the screen this project relies on to notice that unattended
firings did not happen. A guard that skips the thing watching the watchman is
the wrong shape.

WHY DISCOVERY REQUIRES ARGV HANDLING, NOT JUST THE STRING
---------------------------------------------------------
`api/main.py` contains the text "--selftest" in a docstring and implements
nothing — running it as a script dies with an ImportError, because it is a
package module using relative imports. Discovering by substring would have put
a PERMANENT false failure on every single edit, which by this file's own
reasoning below is worse than no guard at all. So a module qualifies only if
it actually parses the flag: `add_argument("--selftest")` or
`"--selftest" in sys.argv`, the two forms this codebase uses.

WHY IT RUNS ALL OF THEM, NOT JUST THE EDITED FILE
-------------------------------------------------
The failures that matter here are cross-module. `quantize_relative_stop` lives
in ftmo_session and is depended on by ftmo_signal and ftmo_runner; the sizer
reads specs captured by ftmo_service. Editing one module and testing only that
module tests the wrong thing. The whole sweep costs about 1.5s, which is cheap
enough that precision is not worth the coverage.

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
import re
import subprocess
import sys
import time
from pathlib import Path

# A whole sweep is ~1.5s. This is generous by a factor of twenty, and exists
# only so a hung test cannot wedge the session.
SWEEP_TIMEOUT_S = 90
PER_MODULE_TIMEOUT_S = 30

# Directories swept, relative to the project root. "" is the root itself.
# api/ joined on 2026-08-12 — see the docstring for the regression that
# earned it. web/ is TypeScript and has no selftest to run.
SEARCH_DIRS = ("", "api")

# A module qualifies only if it PARSES the flag, never merely mentions it.
# See the docstring: api/main.py names it in prose and would otherwise fail
# every sweep forever.
IMPLEMENTS_SELFTEST = re.compile(
    r"""add_argument\(\s*["']--selftest["']|["']--selftest["']\s+in\s+sys\.argv"""
)


def modules_with_selftest(root: Path) -> list[Path]:
    """Every module that implements a --selftest, discovered rather than
    hardcoded so a new one is covered the day it is written."""
    found = []
    for rel in SEARCH_DIRS:
        directory = root / rel if rel else root
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if IMPLEMENTS_SELFTEST.search(text):
                found.append(path)
    return found


def run_selftest(py: str, root: Path, module: Path, timeout: int) -> bool:
    """True if the module's offline checks pass. Exit code only — see docstring.

    Invoked by its path RELATIVE TO THE ROOT, with the root as cwd. `api/`
    modules must run as `api/ftmo_api.py` from the project root: they import
    top-level siblings like `ftmo_runner`, so running them from inside api/
    would not resolve.
    """
    try:
        rel = module.relative_to(root)
    except ValueError:
        rel = Path(module.name)
    try:
        proc = subprocess.run(
            [py, str(rel), "--selftest"],
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
    # Python under the root or under api/. Edits to web/, KronosAI/ or a
    # worktree are someone else's problem and must not trigger a sweep.
    if edited.suffix != ".py":
        return 0
    try:
        parent = edited.parent.resolve()
    except OSError:
        return 0
    watched = []
    for rel in SEARCH_DIRS:
        try:
            watched.append((root / rel if rel else root).resolve())
        except OSError:
            continue
    if parent not in watched:
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

    # The edited module first, so its failure leads the report. Compared by
    # RESOLVED PATH, not by name: with api/ in the sweep two directories can
    # hold the same filename, and matching on name would promote the wrong one.
    edited_resolved = edited.resolve()

    def order(path: Path) -> tuple[int, str]:
        try:
            same = path.resolve() != edited_resolved
        except OSError:
            same = True
        return (int(same), str(path))

    targets.sort(key=order)

    # Note a module with no selftest still triggers the sweep, because it may
    # well be imported by one that has.

    failures: list[str] = []
    deadline = time.monotonic() + SWEEP_TIMEOUT_S
    for module in targets:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Out of budget. Report what was actually measured rather than
            # implying the unmeasured modules passed.
            break
        if not run_selftest(str(py), root, module, int(min(PER_MODULE_TIMEOUT_S, remaining)) or 1):
            # The RELATIVE path, so the reproduce line below is copy-pasteable
            # for api/ modules as well as top-level ones.
            try:
                failures.append(str(module.relative_to(root)))
            except ValueError:
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
        "runner fires unattended every 15 minutes in-window. Fix before moving on.",
        "",
        "NOTE: ftmo_audit's selftest prints 'AUDIT WRITE FAILED' to stderr while",
        "testing an unwritable path and still passes; this guard reads exit codes",
        "only, so if it is named above the failure is real.",
    ]
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
