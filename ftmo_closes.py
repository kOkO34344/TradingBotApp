#!/usr/bin/env python3
"""
ftmo_closes.py — detect and record FTMO positions that closed on their own.

Rule 6 says every fill reaches `trade_journal.csv` or it did not happen. Until
2026-08-08 the FTMO venue only honoured that for exits the runner PLACED. A
stop or a take-profit firing between firings left no journal row, no alert and
no reflection — the account changed and the record did not.

This project has already paid for that hole once, on the other venue. GOOGL's
GTC stop gapped through on 2026-07-23, the position closed, and NOTHING
recorded it for two days. The fix there was `reflect_on_trades.py`'s two-tier
close detection. This is the FTMO counterpart, and it is better positioned than
its IBKR sibling in one specific way: cTrader will hand back the actual closing
DEAL, so a detected close carries the venue's own price and P&L rather than a
reconstruction. IBKR's tier 2 writes no reflection because it has no realized
P&L to build a prompt from; here we have one.

**Why this got urgent on 2026-08-08.** Every FTMO entry now carries a
take-profit as well as a stop. A target fires on exactly the outcomes most
worth recording, so the venue gained a second — and much likelier — way to
close a position without the runner's involvement on the same day.

How it works
------------
Tier 1 is the live `ProtoOAExecutionEvent` stream, which `ftmo_session` already
exposes via `on_execution`. It is nearly free and catches a close that happens
while a session is up. It also catches almost nothing, because the runner's
session lives ~2 minutes per firing out of an hour.

Tier 2 is reconciliation, and it is the one that does the work: remember which
positions were open at the end of each run, and on the next run ask the venue
what is open NOW. Anything remembered but no longer there closed on its own.
Then ask for that position's deals and journal what actually happened.

Three properties that are not negotiable
----------------------------------------
1. **A read that failed is not an account that is flat.** Every "position
   vanished" conclusion requires a SUCCESSFUL read. `reflect_on_trades.py`
   journalled a phantom full liquidation against two positions that were open
   the whole time, because an empty best-effort position cache is
   indistinguishable from a flat account. cTrader differs from IBKR here — a
   `ProtoOAReconcileReq` response is a real answer rather than a cache that may
   have silently timed out — but the cost of being wrong is identical, so a
   diff that would close EVERYTHING is confirmed by a second read before
   anything is written.
2. **Detection time is not event time.** The deal carries its own
   `executionTimestamp` and that is what the journal row is stamped with. The
   time we noticed goes in the detail. The runner fires only inside the trading
   window, so a Sunday close is genuinely discovered on Monday — the row must
   not claim it happened then.
3. **Journal first, then forget.** The state file is only updated after the row
   is written. Losing the record is unrecoverable and losing the state costs a
   duplicate row on the next run, which is visible and fixable. Rule 6 picks
   the direction.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# A closing deal is looked for within this much of the position first being
# seen. Generous on purpose: the runner can be down for a day (it was, for the
# whole of 2026-08-08), and a window that assumes prompt detection would miss
# exactly the closes this module exists to catch.
LOOKBACK_MS = 30 * 24 * 60 * 60 * 1000


@dataclass
class TrackedPosition:
    """What we remember about an open position between one-shot invocations.

    Deliberately a SNAPSHOT rather than a reference to the venue: the whole
    point is to still know what a position was after the venue has stopped
    reporting it.
    """
    position_id: int
    symbol: str
    side: str
    volume: int
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    first_seen_ms: int = 0

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "TrackedPosition":
        return TrackedPosition(
            position_id=int(d["position_id"]), symbol=str(d.get("symbol", "?")),
            side=str(d.get("side", "BUY")), volume=int(d.get("volume", 0)),
            entry_price=float(d.get("entry_price", 0.0)),
            stop_loss=d.get("stop_loss"), take_profit=d.get("take_profit"),
            first_seen_ms=int(d.get("first_seen_ms", 0)))

    @staticmethod
    def from_position(p, now_ms: int) -> "TrackedPosition":
        return TrackedPosition(
            position_id=p.position_id, symbol=p.symbol, side=p.side,
            volume=p.volume, entry_price=p.entry_price,
            stop_loss=p.stop_loss, take_profit=p.take_profit,
            first_seen_ms=now_ms)


def snapshot(positions, now_ms: int) -> dict[str, dict]:
    """Positions as they should be persisted. Keys are STRINGS — this goes
    through JSON, where an int key would come back as a string anyway, and a
    dict whose key type depends on whether it has been round-tripped is a
    reliable source of lookup misses."""
    return {str(p.position_id): TrackedPosition.from_position(p, now_ms).to_json()
            for p in positions}


def load_tracked(raw: dict | None) -> dict[int, TrackedPosition]:
    out: dict[int, TrackedPosition] = {}
    for key, value in (raw or {}).items():
        try:
            tp = TrackedPosition.from_json({**value, "position_id":
                                            value.get("position_id", key)})
        except (TypeError, ValueError, KeyError):
            continue          # a malformed entry is dropped, never guessed at
        out[tp.position_id] = tp
    return out


def diff_positions(remembered: dict[int, TrackedPosition],
                   live_ids) -> tuple[list[TrackedPosition], list[int]]:
    """Pure. -> (positions that vanished, ids that are new since last run).

    Vanished means "was remembered, is not reported now". It does NOT by
    itself mean closed — see `looks_like_a_phantom`, and the caller's
    obligation to have read successfully.
    """
    live = {int(i) for i in live_ids}
    vanished = [tp for pid, tp in sorted(remembered.items()) if pid not in live]
    new = sorted(i for i in live if i not in remembered)
    return vanished, new


def looks_like_a_phantom(remembered: dict[int, TrackedPosition],
                         live_ids) -> bool:
    """Does this diff have the shape of the 2026-07-25 phantom liquidation?

    True when EVERY remembered position vanished at once and there was more
    than one of them. Simultaneous closes do happen — a FLATTEN does exactly
    that — so this is not a refusal, it is a demand for a second opinion. The
    caller re-reads the venue and only proceeds if the second read agrees.

    One position vanishing is the ordinary case (a stop or a target fired) and
    is not treated as suspicious; requiring confirmation for every single close
    would make the common path twice as slow for no information.
    """
    live = {int(i) for i in live_ids}
    return len(remembered) > 1 and not (set(remembered) & live)


def classify_close(tp: TrackedPosition, close_price: float,
                   tolerance_pct: float = 0.25) -> str:
    """Best-effort guess at WHY a position closed, from where it closed.

    Explicitly a guess, and named as one everywhere it is reported. cTrader
    does not label the closing deal "this was your stop" — the deal knows the
    price, not the intent — so this compares the fill against the levels we
    recorded when the position was opened.

    **Side matters, and comparing DISTANCES instead of sides gets the most
    important case backwards.** A stop that gaps through fills BEYOND its
    level, not near it: GOOGL's 326.06 stop filled at the 321.13 open, 1.5%
    away. Nearest-level matching calls that "neither", which is precisely the
    close this project has already failed to record once. So a fill at or
    beyond a level counts as that level being hit, however far beyond it went —
    a long closing at or below its stop is a stop-out, full stop.

    The percentage tolerance only applies BETWEEN the two levels, where a fill
    close to one of them is more likely to be it than a coincidence.

    Returns one of: stop, take_profit, manual-or-unknown.
    """
    if close_price <= 0:
        return "manual-or-unknown"
    stop, target = tp.stop_loss, tp.take_profit
    long_side = tp.side.upper() == "BUY"

    # At or beyond the level — includes every gap, which is the point.
    if stop and (close_price <= stop if long_side else close_price >= stop):
        return "stop"
    if target and (close_price >= target if long_side else close_price <= target):
        return "take_profit"

    # Strictly between the two. Nearest wins, but only if it is actually near.
    candidates = []
    if stop:
        candidates.append(("stop", abs(close_price - stop) / close_price))
    if target:
        candidates.append(("take_profit", abs(close_price - target) / close_price))
    if not candidates:
        return "manual-or-unknown"
    label, rel = min(candidates, key=lambda c: c[1])
    return label if rel <= tolerance_pct / 100.0 else "manual-or-unknown"


def close_record(tp: TrackedPosition, deals: list[dict],
                 detected_at: datetime) -> dict:
    """Pure. Build the journal-ready record of a position that has closed.

    Works from the venue's own closing deals when they are available and
    degrades honestly when they are not: a close we cannot price is reported
    as a close with an UNKNOWN price, never as a close at the entry price or
    at zero. A fabricated number in the journal is worse than a missing one,
    because only the missing one is visible as missing.
    """
    closing = [d for d in deals if d.get("closed")]
    rec = {
        "position_id": tp.position_id, "symbol": tp.symbol, "side": tp.side,
        "volume": tp.volume, "entry_price": tp.entry_price,
        "stop_loss": tp.stop_loss, "take_profit": tp.take_profit,
        "detected_at": detected_at.astimezone(timezone.utc).isoformat(),
        "close_price": None, "net_pnl": None, "gross_profit": None,
        "swap": None, "commission": None, "closed_at": None,
        "reason": "manual-or-unknown", "priced": False,
    }
    if not closing:
        return rec

    last_ms = max(d["execution_ms"] for d in closing)
    gross = sum(d.get("gross_profit", 0.0) for d in closing)
    swap = sum(d.get("swap", 0.0) for d in closing)
    comm = sum(d.get("close_commission", 0.0) + d.get("commission", 0.0)
               for d in closing)
    # Volume-weighted, because a position can be closed in parts and a bare
    # mean would report a price the account never got.
    vol = sum(d.get("closed_volume") or d.get("volume") or 0 for d in closing)
    if vol > 0:
        price = sum((d.get("closed_volume") or d.get("volume") or 0)
                    * d["execution_price"] for d in closing) / vol
    else:
        price = closing[-1]["execution_price"]

    rec.update({
        "close_price": price or None,
        "gross_profit": gross, "swap": swap, "commission": comm,
        "net_pnl": gross + swap - abs(comm),
        "closed_at": datetime.fromtimestamp(last_ms / 1000.0,
                                            tz=timezone.utc).isoformat(),
        "closed_ms": last_ms,
        "reason": classify_close(tp, price or 0.0),
        "priced": bool(price),
    })
    return rec


def describe(rec: dict) -> str:
    """One human line for a log or a text message."""
    pnl = rec.get("net_pnl")
    price = rec.get("close_price")
    money = f"{pnl:+,.2f} USD" if pnl is not None else "P&L UNKNOWN"
    at = f"@ {price:,.5f}" if price else "@ price UNKNOWN"
    when = rec.get("closed_at") or f"detected {rec['detected_at']}"
    return (f"{rec['symbol']} {rec['side']} {rec['volume']} closed {at} "
            f"({rec['reason']}) {money} — {when}")


def reconcile(session, remembered: dict[int, TrackedPosition],
              now: datetime, confirm_read=None) -> dict:
    """Find positions that closed on their own and build their records.

    Impure only in that it reads the venue. Writes nothing — journalling and
    state are the caller's, so that the "journal before you forget" ordering
    lives in one place instead of being duplicated per call site.

    Raises whatever the session raises. That is deliberate: a failed read must
    reach the caller as a failure, never as an empty result that reads like
    "nothing closed".
    """
    live = session.refresh_positions()
    live_ids = [p.position_id for p in live]

    if looks_like_a_phantom(remembered, live_ids):
        # Every remembered position gone at once. Real when a FLATTEN ran, and
        # also exactly what a bad read looks like. Ask again before writing.
        second = (confirm_read or session.refresh_positions)()
        second_ids = [p.position_id for p in second]
        if set(second_ids) != set(live_ids):
            return {"closed": [], "live": second, "new": [],
                    "unconfirmed": True,
                    "note": ("two consecutive reads disagreed about which "
                             "positions are open — recording nothing this "
                             "cycle rather than guessing")}
        live, live_ids = second, second_ids

    vanished, new_ids = diff_positions(remembered, live_ids)
    now_ms = int(now.timestamp() * 1000)
    records = []
    for tp in vanished:
        try:
            start = (tp.first_seen_ms or now_ms) - 60_000
            deals = session.deals_for_position(tp.position_id, start, now_ms)
        except Exception as e:                                # noqa: BLE001
            # An unpriceable close is still a close. Record it as one, with the
            # reason we could not price it, rather than dropping the event.
            rec = close_record(tp, [], now)
            rec["note"] = f"could not fetch deals: {e}"
            records.append(rec)
            continue
        records.append(close_record(tp, deals, now))

    return {"closed": records, "live": live, "new": new_ids,
            "unconfirmed": False, "note": ""}


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    class P:
        def __init__(self, pid, sym="ETHUSD", side="BUY", vol=210,
                     entry=1917.42, sl=1799.1, tp=2196.98):
            self.position_id, self.symbol, self.side = pid, sym, side
            self.volume, self.entry_price = vol, entry
            self.stop_loss, self.take_profit = sl, tp

    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    print("tracking survives the JSON round trip:")
    snap = snapshot([P(1), P(2, "EURUSD")], now_ms)
    back = load_tracked(json.loads(json.dumps(snap)))
    check("both positions come back", set(back) == {1, 2})
    check("keys are ints after loading, whatever JSON did to them",
          all(isinstance(k, int) for k in back))
    check("the snapshot keeps the entry price we will need after it is gone",
          back[1].entry_price == 1917.42)
    check("the snapshot keeps the stop and target for classification",
          back[1].stop_loss == 1799.1 and back[1].take_profit == 2196.98)
    check("a malformed entry is dropped, not guessed at",
          load_tracked({"9": {"position_id": "not-an-int"}}) == {})

    print("diffing remembered against live:")
    remembered = load_tracked(snapshot([P(1), P(2), P(3)], now_ms))
    vanished, new = diff_positions(remembered, [1, 3, 4])
    check("the missing position is reported vanished",
          [v.position_id for v in vanished] == [2])
    check("a position we never saw before is reported new", new == [4])
    check("nothing vanishes when everything is still there",
          diff_positions(remembered, [1, 2, 3])[0] == [])
    check("an empty memory reports nothing vanished (first ever run)",
          diff_positions({}, [1, 2])[0] == [])

    print("the phantom-liquidation shape demands a second opinion:")
    check("ALL of several positions vanishing is suspicious",
          looks_like_a_phantom(remembered, []))
    check("ONE position closing is ordinary, not suspicious",
          not looks_like_a_phantom(remembered, [1, 3]))
    check("a single remembered position closing is not suspicious either",
          not looks_like_a_phantom(load_tracked(snapshot([P(1)], now_ms)), []))
    check("nothing remembered is never a phantom",
          not looks_like_a_phantom({}, []))

    class _S:
        """A session whose two reads disagree — the exact failure being guarded."""
        def __init__(self): self.reads = 0
        def refresh_positions(self):
            self.reads += 1
            return [] if self.reads == 1 else [P(1), P(2), P(3)]
        def deals_for_position(self, *a): raise AssertionError("must not be called")

    out = reconcile(_S(), remembered, now)
    check("disagreeing reads record NOTHING rather than 3 phantom closes",
          out["closed"] == [] and out["unconfirmed"])
    check("...and say so, instead of reporting a quiet success",
          "disagreed" in out["note"])

    print("a real close is priced from the venue's own deal:")
    deals = [{"closed": True, "execution_ms": now_ms - 3600_000,
              "execution_price": 2196.98, "closed_volume": 210, "volume": 210,
              "gross_profit": 587.0, "swap": -1.5, "close_commission": 4.0,
              "commission": 0.0}]
    rec = close_record(remembered[1], deals, now)
    check("the close price is the deal's price", rec["close_price"] == 2196.98)
    check("net P&L nets swap and commission off gross",
          abs(rec["net_pnl"] - (587.0 - 1.5 - 4.0)) < 1e-9)
    check("it is marked priced", rec["priced"])
    check("closed_at is the DEAL's time, not detection time",
          rec["closed_at"].startswith("2026-08-08T11:00"))
    check("detected_at is recorded separately and differs",
          rec["detected_at"] != rec["closed_at"])
    check("hitting the target is classified as take_profit",
          rec["reason"] == "take_profit")

    print("classification is a guess from price, and says so when unsure:")
    check("a fill at the stop reads as stop",
          classify_close(remembered[1], 1799.1) == "stop")
    # The GOOGL shape: the stop was 326.06 and it filled at the 321.13 open,
    # 1.5% BEYOND. Nearest-level matching calls that "neither" and loses the
    # one close this project has already failed to record.
    check("a gap THROUGH the stop still reads as stop",
          classify_close(remembered[1], 1780.0) == "stop")
    check("a gap far through the stop still reads as stop, at any distance",
          classify_close(remembered[1], 900.0) == "stop")
    check("a fill at the target reads as take_profit",
          classify_close(remembered[1], 2196.98) == "take_profit")
    check("a gap THROUGH the target still reads as take_profit",
          classify_close(remembered[1], 2400.0) == "take_profit")
    check("a fill between the two, near neither, is manual-or-unknown",
          classify_close(remembered[1], 1950.0) == "manual-or-unknown")
    check("a fill between the two but hugging the stop reads as stop",
          classify_close(remembered[1], 1801.0) == "stop")
    # A short inverts every comparison: its stop is ABOVE entry.
    shortp = TrackedPosition(7, "EURUSD", "SELL", 100, 1.1600,
                             stop_loss=1.1700, take_profit=1.1400)
    check("a short closing ABOVE its stop reads as stop, not take_profit",
          classify_close(shortp, 1.1750) == "stop")
    check("a short closing BELOW its target reads as take_profit",
          classify_close(shortp, 1.1350) == "take_profit")
    check("side is not ignored: the same price means opposite things",
          classify_close(shortp, 1.1750)
          != classify_close(TrackedPosition(8, "EURUSD", "BUY", 100, 1.1600,
                                            stop_loss=1.1500,
                                            take_profit=1.1700), 1.1750))
    check("no stop and no target recorded -> unknown, never a coin flip",
          classify_close(TrackedPosition(9, "X", "BUY", 1, 100.0),
                         100.0) == "manual-or-unknown")

    print("an unpriceable close is still recorded as a close:")
    bare = close_record(remembered[1], [], now)
    check("the row exists", bare["position_id"] == 1)
    check("the price is None, NOT zero and NOT the entry price",
          bare["close_price"] is None)
    check("P&L is None rather than a fabricated 0.00", bare["net_pnl"] is None)
    check("it is not marked priced", not bare["priced"])
    check("describe() says UNKNOWN out loud", "UNKNOWN" in describe(bare))

    class _Broken:
        def refresh_positions(self): return [P(1), P(3)]
        def deals_for_position(self, *a): raise RuntimeError("venue timeout")

    out2 = reconcile(_Broken(), remembered, now)
    check("a deal fetch that fails still yields a close record",
          len(out2["closed"]) == 1 and out2["closed"][0]["position_id"] == 2)
    check("...and records why it could not be priced",
          "could not fetch deals" in out2["closed"][0].get("note", ""))

    class _Dead:
        def refresh_positions(self): raise RuntimeError("session is not connected")

    raised = False
    try:
        reconcile(_Dead(), remembered, now)
    except RuntimeError:
        raised = True
    check("a FAILED read raises — it never reads as 'nothing is open'", raised)

    print("partial closes are volume-weighted, not averaged:")
    split = [{"closed": True, "execution_ms": now_ms - 7200_000,
              "execution_price": 2000.0, "closed_volume": 200, "volume": 200,
              "gross_profit": 100.0, "swap": 0.0, "close_commission": 0.0,
              "commission": 0.0},
             {"closed": True, "execution_ms": now_ms - 3600_000,
              "execution_price": 3000.0, "closed_volume": 10, "volume": 10,
              "gross_profit": 20.0, "swap": 0.0, "close_commission": 0.0,
              "commission": 0.0}]
    rec2 = close_record(remembered[1], split, now)
    check("the price is weighted by volume, not a bare mean of 2500",
          abs(rec2["close_price"] - (2000 * 200 + 3000 * 10) / 210) < 1e-6)
    check("P&L sums across every closing deal", abs(rec2["net_pnl"] - 120.0) < 1e-9)
    check("closed_at is the LAST deal's time", rec2["closed_ms"] == now_ms - 3600_000)

    print("\nFAILED" if failures else
          "\nAll ftmo_closes offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Detect FTMO positions that closed without the runner.")
    ap.add_argument("--selftest", action="store_true",
                    help="run offline checks; needs no credentials")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
