#!/usr/bin/env python3
"""
secrets_store.py — the one place that knows where this project's credentials
live. Resolves PATHS ONLY: it never reads, parses, logs or prints a value.

WHY A FOLDER. Credentials used to sit as two unrelated `.env` files, one at the
repo root (cTrader/FTMO) and one under `TelegramBot/`. Both were gitignored and
neither ever reached a commit — verified against the full history on
2026-08-05 — so this is not a leak being cleaned up. It is that two files in
two places with the same name are easy to miss when auditing, easy to sweep
into a backup by accident, and give no single answer to "what does this machine
hold?". They now live together under `secrets/`, mode 700, one file per
provider, with tracked `.example` templates beside them.

THE LEGACY PATHS STILL WORK, AND THAT IS DELIBERATE. `resolve()` returns the
new location when it exists and falls back to the old one otherwise. Both
consumers are on UNATTENDED paths — `TelegramBot/notify.py` is how every
launchd job reports, and `ftmo_service.py` is the trading venue adapter — so a
credential move must not be able to silently disable them at 03:00. A migration
that half-applies should degrade to "still works", never to "no notifications
and nobody knows".

Ordering matters and is asserted in the selftest: NEW WINS. Once migrated, the
file under `secrets/` is authoritative even if a stale legacy file is still
lying around, so a forgotten copy cannot quietly shadow the real one.

Offline selftest:  python3 secrets_store.py --selftest
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SECRETS_DIR = BASE_DIR / "secrets"

# Every credential file this project uses, by provider name. Adding one here is
# the only step needed for it to be found, documented and ignored correctly.
LEGACY_PATHS = {
    "ctrader": BASE_DIR / ".env",
    "telegram": BASE_DIR / "TelegramBot" / ".env",
}


def secret_path(name: str, secrets_dir: Path | None = None) -> Path:
    """The canonical location for a provider's credentials, migrated or not."""
    return (secrets_dir or SECRETS_DIR) / f"{name}.env"


def resolve(name: str, secrets_dir: Path | None = None,
            legacy: dict | None = None) -> Path:
    """Where `name`'s credentials actually are right now.

    Returns the canonical `secrets/<name>.env` if it exists, else the legacy
    path if THAT exists, else the canonical path — so a caller creating a file
    for the first time writes it to the new location, and an error message
    about a missing file names where it ought to go rather than where it used
    to be.
    """
    legacy = LEGACY_PATHS if legacy is None else legacy
    canonical = secret_path(name, secrets_dir)
    if canonical.exists():
        return canonical
    old = legacy.get(name)
    if old is not None and old.exists():
        return old
    return canonical


def ensure_secrets_dir(secrets_dir: Path | None = None) -> Path:
    """Create `secrets/` mode 700 if absent. Never touches existing contents."""
    d = secrets_dir or SECRETS_DIR
    d.mkdir(mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)  # also tighten a directory that already existed
    return d


def describe() -> str:
    """A human summary of what this machine holds, for an audit — names and
    locations and whether the file is present, never any content."""
    lines = [f"secrets dir: {SECRETS_DIR}"
             f"{'' if SECRETS_DIR.exists() else '   (not created yet)'}"]
    for name in sorted(LEGACY_PATHS):
        p = resolve(name)
        where = "migrated" if p == secret_path(name) else "LEGACY LOCATION"
        state = "present" if p.exists() else "MISSING"
        try:
            mode = oct(p.stat().st_mode & 0o777) if p.exists() else "-"
        except OSError:
            mode = "?"
        lines.append(f"  {name:10} {state:8} {where:16} mode {mode}  {p}")
    return "\n".join(lines)


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline checks. Creates no real secrets and reads no real credentials."""
    import tempfile
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sdir = root / "secrets"
        legacy = {"ctrader": root / ".env", "telegram": root / "tg" / ".env"}
        legacy["telegram"].parent.mkdir()

        print("resolution order:")
        check("nothing anywhere -> the canonical path, not the legacy one",
              resolve("ctrader", sdir, legacy) == sdir / "ctrader.env")

        legacy["ctrader"].write_text("K=V\n")
        check("only a legacy file -> the legacy path (nothing breaks)",
              resolve("ctrader", sdir, legacy) == legacy["ctrader"])

        sdir.mkdir()
        (sdir / "ctrader.env").write_text("K=V\n")
        check("both present -> NEW wins, a stale legacy file cannot shadow it",
              resolve("ctrader", sdir, legacy) == sdir / "ctrader.env")

        check("an unknown provider still resolves to a canonical path",
              resolve("nope", sdir, legacy) == sdir / "nope.env")
        check("a provider with no legacy entry does not raise",
              resolve("telegram", sdir, {}) == sdir / "telegram.env")

        print("ensure_secrets_dir:")
        fresh = root / "fresh"
        ensure_secrets_dir(fresh)
        check("created", fresh.is_dir())
        check("mode 700 — not readable by other users",
              (fresh.stat().st_mode & 0o777) == 0o700)
        (fresh / "keep.env").write_text("x")
        ensure_secrets_dir(fresh)
        check("re-running preserves existing contents",
              (fresh / "keep.env").exists())
        loose = root / "loose"
        loose.mkdir(mode=0o755)
        ensure_secrets_dir(loose)
        check("an existing too-open dir is tightened to 700",
              (loose.stat().st_mode & 0o777) == 0o700)

        print("the public API deals in paths, never in values:")
        check("resolve returns a Path",
              isinstance(resolve("ctrader", sdir, legacy), Path))
        check("secret_path returns a Path",
              isinstance(secret_path("ctrader", sdir), Path))
        # describe() is the one function that reports on real files, so it is
        # the one that could leak. It must name locations and never contents.
        (sdir / "ctrader.env").write_text("SUPER_SECRET_VALUE=hunter2\n")
        text = describe()
        check("describe() never prints a credential value",
              "hunter2" not in text and "SUPER_SECRET_VALUE" not in text)

    print("\nFAILED" if failures else
          "\nAll secrets_store offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Where this project's credentials live (paths only).")
    ap.add_argument("--selftest", action="store_true",
                    help="Offline checks; touches no real credentials.")
    ap.add_argument("--describe", action="store_true",
                    help="Report which credentials exist and where. No values.")
    args = ap.parse_args()
    if args.describe:
        print(describe())
        return 0
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
