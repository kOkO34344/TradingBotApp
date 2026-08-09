#!/usr/bin/env python3
"""
trade_journal.py — the append-only trade journal, and the ONE place that knows
its column set.

Rule 6: every order attempt, block and fill goes to `trade_journal.csv`. If it
is not in the journal, it did not happen.

This module was extracted from the IBKR adapter on 2026-08-06, when FTMO
arrived and the journal needed a `venue` column plus a writer that was not
bolted to `ib_async` — importing a broker adapter to record an order on a
different broker is the wrong dependency. IBKR was removed entirely on
2026-08-09 and this file outlived it, which is the extraction paying off: the
audit trail did not have to move when the venue did.

**`venue="ibkr"` is still a valid value and always will be.** The journal holds
46 rows from that account and they are not going anywhere: an audit trail you
prune when a venue is retired is not an audit trail. What was removed is the
CODE, never the record of what it did.

WHY THE MIGRATION LIVES IN THE WRITER
-------------------------------------
`journal()` historically wrote the header only when the file did not exist, so
simply extending the column list would have appended 12-value rows under an
11-column header. Every reader uses `csv.DictReader`, which drops the extra
value into the `None` restkey and reports no error at all — the corruption
would have been silent, in the one file the project's audit trail depends on.
That trap is recorded in CLAUDE.md; this module is the fix.

So the writer checks the header and migrates ONCE, in place, before appending.
Self-healing rather than "run the migration script first", for the same reason
`secrets_store.resolve()` falls back to the legacy paths: both writers are on
unattended paths, and a half-applied migration must degrade to "still works",
never to "the audit trail silently stopped lining up".

The migration is lossless — every pre-existing row is an IBKR row, so it is
backfilled `venue=ibkr` — and it is verified by reading the result back before
the original is replaced. A timestamped backup is kept. It is idempotent: a
file already carrying the column is left untouched.

Offline selftest:  python3 trade_journal.py --selftest
Inspect/migrate the real journal:  python3 trade_journal.py --migrate
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
JOURNAL_FILE = BASE_DIR / "trade_journal.csv"

# `venue` is appended LAST on purpose. Every reader is a DictReader so column
# order is not load-bearing for them, but appending keeps any positional reader
# that predates this change reading the same first eleven fields it always did.
JOURNAL_COLUMNS = ["timestamp", "event", "symbol", "sec_type", "action",
                   "quantity", "price", "stop", "target", "status", "detail",
                   "venue"]

# The column set as it stood before rule 9. Kept named rather than inlined as
# `[:-1]` so that adding a THIRD column later cannot silently redefine what
# "the legacy layout" meant for the rows already on disk.
LEGACY_COLUMNS = ["timestamp", "event", "symbol", "sec_type", "action",
                  "quantity", "price", "stop", "target", "status", "detail"]

# Every row written before the column existed came from the IBKR paper account.
# This is a statement of fact about the file's history, not a default for new
# callers — `append()` requires venue explicitly.
LEGACY_VENUE = "ibkr"

VALID_VENUES = ("ibkr", "ftmo")


class JournalError(RuntimeError):
    """The journal is in a state the writer refuses to guess about."""


def read_header(path: Path) -> list[str] | None:
    """The header currently on disk, or None if there is no usable file."""
    if not path.exists():
        return None
    try:
        with open(path, newline="") as fh:
            first = next(csv.reader(fh), None)
    except OSError:
        return None
    return first or None


def needs_migration(path: Path) -> bool:
    """True when a file exists whose header predates the `venue` column."""
    header = read_header(path)
    if header is None:
        return False
    return "venue" not in header


def migrate(path: Path, *, keep_backup: bool = True) -> dict:
    """Add the `venue` column to an existing journal, losslessly and verifiably.

    Writes to a temporary file in the same directory and `os.replace()`s it
    into position, so a crash mid-write leaves the original intact rather than
    a half-rewritten audit file. The result is read back and checked against
    what went in BEFORE the swap — a migration that cannot prove it preserved
    every field is abandoned, not shipped.
    """
    header = read_header(path)
    if header is None:
        return {"migrated": False, "reason": "no existing journal"}
    if "venue" in header:
        return {"migrated": False, "reason": "already migrated",
                "rows": _count_rows(path)}
    if header != LEGACY_COLUMNS:
        raise JournalError(
            f"unexpected journal header, refusing to migrate.\n"
            f"  on disk: {header}\n"
            f"  expected: {LEGACY_COLUMNS}\n"
            f"A header this writer does not recognise is a file it must not "
            f"rewrite — inspect it by hand.")

    with open(path, newline="") as fh:
        original = list(csv.DictReader(fh))

    # A ragged row (more values than the header) would already have been losing
    # data into DictReader's restkey. Surface it rather than baking it in.
    ragged = [i for i, r in enumerate(original, start=2) if r.get(None)]
    if ragged:
        raise JournalError(
            f"rows {ragged} carry more values than the header has columns; "
            f"the file is already misaligned and needs a human before any "
            f"automated rewrite.")

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".journal-",
                                    suffix=".csv")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=JOURNAL_COLUMNS)
            w.writeheader()
            for row in original:
                out = {c: row.get(c, "") for c in LEGACY_COLUMNS}
                out["venue"] = LEGACY_VENUE
                w.writerow(out)

        # Verify BEFORE replacing anything.
        with open(tmp, newline="") as fh:
            rebuilt = list(csv.DictReader(fh))
        if len(rebuilt) != len(original):
            raise JournalError(
                f"row count changed during migration "
                f"({len(original)} -> {len(rebuilt)}); aborted, "
                f"original untouched.")
        for i, (before, after) in enumerate(zip(original, rebuilt), start=2):
            for col in LEGACY_COLUMNS:
                if (before.get(col) or "") != (after.get(col) or ""):
                    raise JournalError(
                        f"row {i} column {col!r} changed during migration "
                        f"({before.get(col)!r} -> {after.get(col)!r}); "
                        f"aborted, original untouched.")
            if after.get("venue") != LEGACY_VENUE:
                raise JournalError(
                    f"row {i} did not receive venue={LEGACY_VENUE!r}; "
                    f"aborted, original untouched.")

        backup = None
        if keep_backup:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            backup = path.with_suffix(f".csv.bak-{stamp}")
            shutil.copy2(path, backup)

        os.replace(tmp, path)
        return {"migrated": True, "rows": len(rebuilt),
                "backup": str(backup) if backup else None}
    finally:
        if tmp.exists():
            tmp.unlink()


def _count_rows(path: Path) -> int:
    with open(path, newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def append(path: Path, values: dict) -> None:
    """Append one row, migrating the header first if it is out of date.

    Takes a dict rather than positional arguments so a caller can never write
    a value into the wrong column, and writes through `csv.DictWriter` against
    `JOURNAL_COLUMNS` so an unknown key is a loud error rather than a silently
    shifted row.
    """
    venue = values.get("venue")
    if venue not in VALID_VENUES:
        raise JournalError(
            f"venue must be one of {VALID_VENUES}, got {venue!r}. "
            f"Rule 6 rows are read back later to reconcile against a specific "
            f"broker; an unlabelled row cannot be reconciled against either.")
    unknown = set(values) - set(JOURNAL_COLUMNS)
    if unknown:
        raise JournalError(f"unknown journal column(s): {sorted(unknown)}")

    if needs_migration(path):
        migrate(path)

    new = not path.exists()
    row = {c: values.get(c, "") for c in JOURNAL_COLUMNS}
    row.setdefault("timestamp", "")
    if not row["timestamp"]:
        row["timestamp"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=JOURNAL_COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    import io
    import contextlib

    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def raises(fn, needle=""):
        try:
            fn()
        except Exception as e:                                # noqa: BLE001
            return needle.lower() in str(e).lower()
        return False

    tmpdir = Path(tempfile.mkdtemp(prefix="journal-selftest-"))

    def legacy_file(rows) -> Path:
        p = tmpdir / f"legacy-{len(list(tmpdir.iterdir()))}.csv"
        with open(p, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(LEGACY_COLUMNS)
            for r in rows:
                w.writerow(r)
        return p

    print("column set:")
    check("venue is the last column", JOURNAL_COLUMNS[-1] == "venue")
    check("the first eleven columns are unchanged from the legacy layout",
          JOURNAL_COLUMNS[:11] == LEGACY_COLUMNS)

    print("detecting an out-of-date header:")
    old = legacy_file([["2026-07-21T10:00:00", "SUBMIT", "AAPL", "STK", "BUY",
                        15, 328.04, 309.10, "", "Filled", "first rebalance"]])
    check("a legacy file needs migration", needs_migration(old))
    check("a missing file does not", not needs_migration(tmpdir / "nope.csv"))

    print("migration is lossless and verified:")
    res = migrate(old)
    check("it reports having migrated", res["migrated"] is True)
    check("row count is preserved", res["rows"] == 1)
    with open(old, newline="") as fh:
        rows = list(csv.DictReader(fh))
    check("the header now carries venue", read_header(old) == JOURNAL_COLUMNS)
    check("existing rows are backfilled venue=ibkr",
          rows[0]["venue"] == "ibkr")
    check("every original field survived untouched",
          rows[0]["symbol"] == "AAPL" and rows[0]["quantity"] == "15"
          and rows[0]["detail"] == "first rebalance"
          and rows[0]["stop"] == "309.1")
    check("a backup was written", res["backup"] and Path(res["backup"]).exists())

    print("migration is idempotent:")
    again = migrate(old)
    check("a second run is a no-op", again["migrated"] is False)
    check("...and says why", again["reason"] == "already migrated")

    print("an unrecognised header is refused, never rewritten:")
    weird = tmpdir / "weird.csv"
    with open(weird, "w", newline="") as fh:
        csv.writer(fh).writerow(["timestamp", "something", "else"])
    check("refuses to migrate a header it does not recognise",
          raises(lambda: migrate(weird), "refusing to migrate"))
    check("...and leaves the file alone",
          read_header(weird) == ["timestamp", "something", "else"])

    print("an already-misaligned file is refused:")
    ragged = tmpdir / "ragged.csv"
    with open(ragged, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(LEGACY_COLUMNS)
        w.writerow(["t", "SUBMIT", "AAPL", "STK", "BUY", 1, 2, 3, 4, "ok",
                    "detail", "EXTRA VALUE"])
    check("a row with more values than columns raises",
          raises(lambda: migrate(ragged), "misaligned"))

    print("append():")
    fresh = tmpdir / "fresh.csv"
    append(fresh, {"event": "SUBMIT", "symbol": "BTCUSD", "action": "BUY",
                   "quantity": 1, "venue": "ftmo"})
    with open(fresh, newline="") as fh:
        rows = list(csv.DictReader(fh))
    check("a new file gets the full header", read_header(fresh) == JOURNAL_COLUMNS)
    check("the row carries its venue", rows[0]["venue"] == "ftmo")
    check("a timestamp is filled in automatically", bool(rows[0]["timestamp"]))
    check("unset columns are empty strings, not None", rows[0]["target"] == "")

    print("append() self-heals a legacy file rather than misaligning it:")
    legacy = legacy_file([["2026-07-21T10:00:00", "SUBMIT", "JNJ", "STK", "BUY",
                           19, 249.98, 237.61, "", "Filled", "rebalance"]])
    append(legacy, {"event": "SUBMIT", "symbol": "XAUUSD", "action": "BUY",
                    "quantity": 1, "venue": "ftmo"})
    with open(legacy, newline="") as fh:
        rows = list(csv.DictReader(fh))
    check("the legacy row is still readable and now labelled ibkr",
          rows[0]["symbol"] == "JNJ" and rows[0]["venue"] == "ibkr")
    check("the new FTMO row landed in the right columns",
          rows[1]["symbol"] == "XAUUSD" and rows[1]["venue"] == "ftmo")
    check("no value leaked into DictReader's restkey (the silent-corruption "
          "failure this module exists to prevent)",
          all(r.get(None) is None for r in rows))

    print("append() refuses a row it could not reconcile later:")
    check("an unknown venue is refused",
          raises(lambda: append(fresh, {"event": "X", "venue": "kraken"}),
                 "venue must be one of"))
    check("a missing venue is refused",
          raises(lambda: append(fresh, {"event": "X"}), "venue must be one of"))
    check("an unknown column is refused",
          raises(lambda: append(fresh, {"event": "X", "venue": "ftmo",
                                        "notional": 5}), "unknown journal column"))

    print("a failed migration leaves no temp files behind:")
    leftovers = [p for p in tmpdir.iterdir() if p.name.startswith(".journal-")]
    check("no .journal-* temp files remain", not leftovers)

    with contextlib.redirect_stdout(io.StringIO()):
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\nFAILED" if failures else
          "\nAll trade_journal offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The trade journal and its schema.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--selftest", action="store_true",
                   help="Offline checks against temp files. Touches nothing real.")
    g.add_argument("--migrate", action="store_true",
                   help="Add the venue column to the real trade_journal.csv.")
    g.add_argument("--describe", action="store_true",
                   help="Report the real journal's header and row count.")
    args = ap.parse_args()

    if args.migrate:
        if not JOURNAL_FILE.exists():
            print(f"no journal at {JOURNAL_FILE}")
            return 1
        print(f"journal: {JOURNAL_FILE}")
        print(f"  header before: {read_header(JOURNAL_FILE)}")
        res = migrate(JOURNAL_FILE)
        print(f"  {res}")
        print(f"  header after:  {read_header(JOURNAL_FILE)}")
        return 0
    if args.describe:
        header = read_header(JOURNAL_FILE)
        print(f"journal: {JOURNAL_FILE}")
        print(f"  exists: {JOURNAL_FILE.exists()}")
        print(f"  header: {header}")
        if header:
            print(f"  rows:   {_count_rows(JOURNAL_FILE)}")
            print(f"  needs migration: {needs_migration(JOURNAL_FILE)}")
        return 0
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
