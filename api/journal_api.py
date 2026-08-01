"""
journal_api.py — reads trade_journal.csv for the web UI.

Rule 6: "If it's not in the journal, it didn't happen." This module is the
read side of that, and it has one job beyond parsing CSV — presenting the
journal's *corrections* honestly.

The journal is append-only and has been wrong twice in ways that mattered:

  * 2026-07-27: two bracket orders that actually FILLED were journalled
    `Cancelled` a second after placement. The corrections were appended as
    `RESULT_CORRECTED` rows; the originals were deliberately left in place.
  * 2026-07-25: `reflect_on_trades.py` wrote phantom `CLOSE_DETECTED` rows
    for two positions that were never closed, later annotated by `NOTE`
    rows saying so.

So a naive reader of this file reports events that did not happen. This
module keeps every row (nothing is hidden — that's the audit trail) but
marks superseded rows `superseded: true` with a pointer to what corrected
them, and marks disputed rows `disputed: true` with the note text. The UI
greys them out rather than dropping them.

Nothing here writes. Journal writes stay in `ibkr_service.journal()`.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

JOURNAL_FILE = BASE_DIR / "trade_journal.csv"

# Events that represent a position being opened / closed, i.e. the ones worth
# drawing on a chart. NOTE and BLOCKED never produced a fill by definition.
FILL_EVENTS = {"RESULT", "RESULT_CORRECTED", "CLOSE_FILLED",
               "CLOSE_DETECTED", "CLOSE_RECONSTRUCTED"}
OPEN_EVENTS = {"SUBMIT", "RESULT", "RESULT_CORRECTED"}
CLOSE_EVENTS = {"CLOSE_FILLED", "CLOSE_DETECTED", "CLOSE_RECONSTRUCTED"}

# Statuses that mean "this order reached the market", as IBKR reports them.
FILLED_STATUSES = {"filled", "closed"}


def _num(value):
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _ts(value: str) -> int | None:
    """ISO timestamp -> UNIX seconds, for aligning markers to bars."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


@dataclass
class JournalRow:
    index: int
    timestamp: str
    epoch: int | None
    event: str
    symbol: str
    sec_type: str
    action: str
    quantity: float | None
    price: float | None
    stop: float | None
    target: float | None
    status: str
    detail: str
    superseded: bool = False
    superseded_by: int | None = None
    disputed: bool = False
    dispute_note: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "epoch": self.epoch,
            "event": self.event,
            "symbol": self.symbol,
            "secType": self.sec_type,
            "action": self.action,
            "quantity": self.quantity,
            "price": self.price,
            "stop": self.stop,
            "target": self.target,
            "status": self.status,
            "detail": self.detail,
            "superseded": self.superseded,
            "supersededBy": self.superseded_by,
            "disputed": self.disputed,
            "disputeNote": self.dispute_note,
        }


def load_rows(path: Path = JOURNAL_FILE) -> list[JournalRow]:
    """Parse the journal and annotate corrections. Newest last."""
    if not path.exists():
        return []
    rows: list[JournalRow] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for i, rec in enumerate(csv.DictReader(fh)):
            rows.append(JournalRow(
                index=i,
                timestamp=(rec.get("timestamp") or "").strip(),
                epoch=_ts((rec.get("timestamp") or "").strip()),
                event=(rec.get("event") or "").strip(),
                symbol=(rec.get("symbol") or "").strip(),
                sec_type=(rec.get("sec_type") or "").strip(),
                action=(rec.get("action") or "").strip(),
                quantity=_num(rec.get("quantity")),
                price=_num(rec.get("price")),
                stop=_num(rec.get("stop")),
                target=_num(rec.get("target")),
                status=(rec.get("status") or "").strip(),
                detail=(rec.get("detail") or "").strip(),
            ))
    _annotate_corrections(rows)
    return rows


def _annotate_corrections(rows: list[JournalRow]) -> None:
    """Mark rows a later row overturned.

    A `RESULT_CORRECTED` supersedes the most recent earlier `RESULT` for the
    same symbol. A `NOTE` whose status or detail disowns an event ("phantom",
    "fabricated", "disregard") disputes the nearest earlier row for that
    symbol. Both are heuristics over free text, so they only ever *annotate*
    — no row is ever dropped, and the raw text ships with it so the owner can
    judge for himself.
    """
    dispute_markers = ("phantom", "fabricat", "disregard", "did not happen",
                       "never occurred", "never happened", "incorrect")

    for row in rows:
        if row.event == "RESULT_CORRECTED":
            for earlier in reversed(rows[:row.index]):
                if earlier.symbol == row.symbol and earlier.event == "RESULT":
                    earlier.superseded = True
                    earlier.superseded_by = row.index
                    break
        elif row.event == "NOTE":
            blob = f"{row.status} {row.detail}".lower()
            if not any(m in blob for m in dispute_markers):
                continue
            for earlier in reversed(rows[:row.index]):
                if earlier.symbol != row.symbol or earlier.event == "NOTE":
                    continue
                if earlier.event in CLOSE_EVENTS or earlier.event in FILL_EVENTS:
                    earlier.disputed = True
                    earlier.dispute_note = row.detail or row.status
                    break


def markers_for(symbol: str, path: Path = JOURNAL_FILE) -> list[dict]:
    """Chart markers for one symbol: entries, exits and stop levels.

    Superseded and disputed rows are excluded here — a chart marker is an
    assertion that something happened at a price and a time, and the whole
    point of the correction tracking is that some of those assertions are
    known false. They remain visible on the journal screen, where the
    contradiction is the information.
    """
    symbol = symbol.upper()
    out: list[dict] = []
    for row in load_rows(path):
        if row.symbol.upper() != symbol or row.epoch is None:
            continue
        if row.superseded or row.disputed:
            continue
        if row.event not in FILL_EVENTS:
            continue
        if row.event in ("RESULT", "RESULT_CORRECTED"):
            if row.status.lower() not in FILLED_STATUSES:
                continue
        is_close = row.event in CLOSE_EVENTS
        side = (row.action or ("SELL" if is_close else "BUY")).upper()
        out.append({
            "time": row.epoch,
            "event": row.event,
            "side": side,
            "kind": "exit" if is_close else "entry",
            "price": row.price,
            "stop": row.stop,
            "quantity": row.quantity,
            "status": row.status,
            "detail": row.detail,
            "text": f"{side} {row.quantity:g}" if row.quantity else side,
        })
    out.sort(key=lambda m: m["time"])
    return out


def summary(path: Path = JOURNAL_FILE) -> dict:
    """Counts for the dashboard, honest about corrections."""
    rows = load_rows(path)
    by_event: dict[str, int] = {}
    for r in rows:
        by_event[r.event] = by_event.get(r.event, 0) + 1
    return {
        "total": len(rows),
        "byEvent": by_event,
        "superseded": sum(1 for r in rows if r.superseded),
        "disputed": sum(1 for r in rows if r.disputed),
        "blocked": by_event.get("BLOCKED", 0),
        "lastTimestamp": rows[-1].timestamp if rows else None,
        "path": str(path),
    }


def _selftest() -> int:
    """`python3 api/journal_api.py` — runs against the real journal."""
    failures = []

    def check(name, cond):
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    rows = load_rows()
    check("journal loaded", len(rows) > 0)
    check("every row has an event", all(r.event for r in rows))
    check("timestamps parsed", all(r.epoch for r in rows if r.timestamp))

    # The 2026-07-27 corrections must be detected, or the UI repeats the
    # false 'Cancelled' record that hid two real positions for a day.
    corrected = [r for r in rows if r.event == "RESULT_CORRECTED"]
    check("RESULT_CORRECTED rows present", len(corrected) > 0)
    check("each correction superseded an earlier RESULT",
          all(any(x.superseded_by == c.index for x in rows) for c in corrected))

    # The 2026-07-25 phantom closes must be flagged, or the chart draws exits
    # for positions that were never closed.
    disputed = [r for r in rows if r.disputed]
    check("phantom CLOSE_DETECTED rows flagged as disputed", len(disputed) >= 2)
    check("disputed rows carry the explanation",
          all(r.dispute_note for r in disputed))

    jnj = markers_for("JNJ")
    check("no exit marker drawn for the phantom JNJ close",
          not any(m["kind"] == "exit" for m in jnj))

    googl = markers_for("GOOGL")
    check("GOOGL's reconstructed close still produces a marker",
          any(m["event"] == "CLOSE_RECONSTRUCTED" for m in googl))

    aapl = markers_for("AAPL")
    check("AAPL has an entry marker", any(m["kind"] == "entry" for m in aapl))
    check("markers are time-sorted", all(
        a["time"] <= b["time"] for a, b in zip(aapl, aapl[1:])))

    s = summary()
    check("summary counts all rows", s["total"] == len(rows))
    check("summary reports corrections", s["superseded"] > 0)

    check("missing file returns empty, not a crash",
          load_rows(Path("/nonexistent/trade_journal.csv")) == [])

    print(f"\n{len(failures)} failure(s)." if failures else "\nAll journal checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
