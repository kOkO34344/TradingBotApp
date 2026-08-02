#!/usr/bin/env python3
"""
ftmo_audit.py — append-only audit trail for every FTMO rule decision.

`trade_journal.csv` records what the bot DID (rule 6). This records why it was
allowed to, which is a different question and needs a different file. When an
account fails, the useful forensic question is rarely "what orders went out" —
it is "what did the rule engine believe about equity at 14:32:07, and what
number did it act on". That has to be written down at the moment of the
decision, because afterwards the equity that caused it is gone.

WHY JSONL AND NOT CSV. The trade journal is a fixed 11-column schema and CSV
suits it. A rule evaluation is not flat: it carries a variable-length list of
reasons and a dozen metrics that differ by product. Forcing that into columns
would either lose the reasons or produce a sparse table nobody can read. One
JSON object per line stays greppable, parses without quoting hell, and appends
atomically enough for this purpose.

WHY NOT EVERY EVALUATION. The monitor is event-driven at tick rate. Logging
every evaluation would be some millions of lines a day and would bury the four
that matter. So this records DECISIONS and CHANGES: posture transitions, order
allow/refuse verdicts, position lifecycle, day rollovers, breaches — plus a
rate-limited heartbeat snapshot so a quiet day still leaves a trail proving the
monitor was alive and what it was seeing.

THIS MODULE MUST NEVER BREAK THE TRADING LOOP. A failed write is reported to
stderr and swallowed. That is a deliberate inversion of rule 6's "if it isn't
in the journal it didn't happen": for the AUDIT log, an exception propagating
out of a logging call could prevent a FLATTEN from being executed, and losing
an audit line is strictly less bad than failing to close a position that is
breaching a limit. `write_failures` counts them so the condition is visible
rather than silent. The trade journal keeps the opposite policy.

Files are one per FTMO day (`ftmo_audit/YYYY-MM-DD.jsonl`), aligned to the
00:00 CE(S)T boundary rather than the host's midnight — so "the day we
breached" is one file, matching the boundary the rules themselves use.

Offline selftest:  python3 ftmo_audit.py --selftest
Read back a day:   python3 ftmo_audit.py --report [YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

import ftmo_rules as fr

BASE_DIR = Path(__file__).parent
AUDIT_DIR = BASE_DIR / "ftmo_audit"

# A quiet day still gets a line this often, so an empty stretch is provably
# "nothing happened" rather than "the monitor was dead".
SNAPSHOT_INTERVAL_S = 300.0


def _jsonable(value):
    """Convert dataclasses and tuples into something json can serialise."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


class AuditLog:
    """Append-only JSONL trail, one file per FTMO day."""

    def __init__(self, directory: Path = AUDIT_DIR,
                 snapshot_interval_s: float = SNAPSHOT_INTERVAL_S):
        self.directory = Path(directory)
        self.snapshot_interval_s = snapshot_interval_s
        self._last_snapshot_at: datetime | None = None
        self.write_failures = 0
        self.written = 0

    def path_for(self, moment: datetime) -> Path:
        return self.directory / f"{fr.ftmo_day(moment).isoformat()}.jsonl"

    # ------------------------------------------------------------- writing

    def _write(self, record: dict, now: datetime) -> bool:
        """Append one record. Returns False on failure rather than raising.

        Opened, written and closed per record on purpose: a long-lived handle
        buys throughput this does not need, and costs the guarantee that a
        record is on disk before the next decision is taken. If the process
        dies immediately after a BREACH, that breach must already be in the
        file.
        """
        try:
            self.directory.mkdir(exist_ok=True)
            line = json.dumps(_jsonable(record), separators=(",", ":"),
                              sort_keys=True, default=str)
            with open(self.path_for(now), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
            self.written += 1
            return True
        except Exception as e:
            # Deliberately swallowed — see the module docstring. An audit-log
            # failure must never stop a flatten from being executed.
            self.write_failures += 1
            print(f"AUDIT WRITE FAILED ({self.write_failures}): "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            return False

    def _base(self, kind: str, now: datetime) -> dict:
        return {"ts": now.isoformat(), "ftmo_day": fr.ftmo_day(now).isoformat(),
                "kind": kind}

    def record_verdict(self, kind: str, verdict: fr.RuleVerdict, now: datetime,
                       **extra) -> bool:
        """Log a rule evaluation with every number it used.

        The metrics are flattened in rather than nested under the verdict, so
        a later `grep '"posture":"BREACHED"'` or a pandas read_json gets usable
        columns without unwrapping.
        """
        rec = self._base(kind, now)
        rec.update({
            "can_open": verdict.can_open,
            "must_flatten": verdict.must_flatten,
            "breached": verdict.breached,
            "reasons": list(verdict.reasons),
            "daily_loss_used": verdict.daily_loss_used,
            "daily_soft": verdict.daily_soft,
            "daily_flatten": verdict.daily_flatten,
            "daily_hard": verdict.daily_hard,
            "drawdown_used": verdict.drawdown_used,
            "drawdown_soft": verdict.drawdown_soft,
            "drawdown_flatten": verdict.drawdown_flatten,
            "drawdown_hard": verdict.drawdown_hard,
            "drawdown_floor_equity": verdict.drawdown_floor_equity,
            "profit_usd": verdict.profit_usd,
            "profit_target_usd": verdict.profit_target_usd,
            "target_reached": verdict.target_reached,
            "min_days_met": verdict.min_days_met,
            "consistency_ok": verdict.consistency_ok,
            "can_pass": verdict.can_pass,
        })
        rec.update(extra)
        return self._write(rec, now)

    def record_event(self, event, now: datetime, **extra) -> bool:
        """Log a MonitorEvent — posture change, position lifecycle, rollover."""
        if getattr(event, "verdict", None) is not None:
            return self.record_verdict(
                event.kind, event.verdict, now,
                posture=event.posture, detail=event.detail,
                equity=event.equity, **extra)
        rec = self._base(event.kind, now)
        rec.update({"posture": getattr(event, "posture", None),
                    "detail": getattr(event, "detail", ""),
                    "equity": getattr(event, "equity", None)})
        rec.update(extra)
        return self._write(rec, now)

    def record_decision(self, symbol: str, sizing, now: datetime,
                        verdict: fr.RuleVerdict | None = None, **extra) -> bool:
        """Log an order decision — accepted or refused, with the sizing maths."""
        rec = self._base("ORDER_ACCEPTED" if getattr(sizing, "accepted", False)
                         else "ORDER_REFUSED", now)
        rec.update({"symbol": symbol,
                    "accepted": getattr(sizing, "accepted", False),
                    "volume": getattr(sizing, "volume", 0),
                    "units": getattr(sizing, "units", 0.0),
                    "entry_price": getattr(sizing, "entry_price", None),
                    "stop_price": getattr(sizing, "stop_price", None),
                    "risk_at_stop": getattr(sizing, "risk_at_stop", 0.0),
                    "budget_remaining": getattr(sizing, "budget_remaining", 0.0),
                    "reasons": list(getattr(sizing, "reasons", ()))})
        rec.update(extra)
        if verdict is not None:
            rec["rule_reasons"] = list(verdict.reasons)
            rec["daily_loss_used"] = verdict.daily_loss_used
            rec["drawdown_used"] = verdict.drawdown_used
        return self._write(rec, now)

    def snapshot(self, verdict: fr.RuleVerdict, now: datetime,
                 force: bool = False, **extra) -> bool:
        """Rate-limited heartbeat so a quiet day still proves the monitor ran.

        Returns False when suppressed by the interval, which is NOT a failure —
        check `write_failures` for that.
        """
        if not force and self._last_snapshot_at is not None:
            if (now - self._last_snapshot_at).total_seconds() < self.snapshot_interval_s:
                return False
        self._last_snapshot_at = now
        return self.record_verdict("SNAPSHOT", verdict, now, **extra)

    # ------------------------------------------------------------- reading

    def read_day(self, day: datetime | str) -> list[dict]:
        """Load one day's records. A corrupt line is skipped, not fatal.

        Partial recovery matters here: a torn final line from a killed process
        must not make the whole day unreadable, which is exactly when the file
        is most needed.
        """
        if isinstance(day, str):
            path = self.directory / f"{day}.jsonl"
        else:
            path = self.path_for(day)
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"kind": "UNPARSEABLE", "raw": line[:200]})
        return out

    def days_available(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem for p in self.directory.glob("*.jsonl"))


def report(day: str | None = None, directory: Path = AUDIT_DIR) -> int:
    """Human-readable replay of one day's decisions."""
    log = AuditLog(directory)
    days = log.days_available()
    if not days:
        print(f"No audit files in {directory}/ yet.")
        return 0
    day = day or days[-1]
    records = log.read_day(day)
    if not records:
        print(f"No records for {day}. Available: {', '.join(days)}")
        return 1

    print(f"FTMO audit — {day}  ({len(records)} records)\n")
    counts: dict[str, int] = {}
    worst_daily = worst_dd = 0.0
    for r in records:
        counts[r.get("kind", "?")] = counts.get(r.get("kind", "?"), 0) + 1
        worst_daily = max(worst_daily, r.get("daily_loss_used") or 0.0)
        worst_dd = max(worst_dd, r.get("drawdown_used") or 0.0)
        if r.get("kind") == "SNAPSHOT":
            continue
        ts = (r.get("ts") or "")[11:19]
        detail = r.get("detail") or "; ".join(r.get("reasons") or [])
        eq = r.get("equity")
        eq_s = f" equity {eq:,.2f}" if isinstance(eq, (int, float)) else ""
        print(f"  {ts}  {r.get('kind', '?'):<16}{eq_s}  {detail[:96]}")

    print(f"\n  worst daily loss reached: {worst_daily:,.2f}")
    print(f"  worst drawdown reached:   {worst_dd:,.2f}")
    print("  record kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline checks. Writes only into a temp directory."""
    import tempfile
    from zoneinfo import ZoneInfo
    import ftmo_monitor as fm
    import ftmo_sizing as fz
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    cfg = fr.FTMOConfig(product="2step", phase="challenge", initial_capital=25_000.0)
    TZ = ZoneInfo("Europe/Prague")
    T0 = datetime(2026, 8, 3, 12, 0, tzinfo=TZ)

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        print("file naming follows the FTMO day, not the host day:")
        log = AuditLog(d)
        check("noon Prague -> that day's file",
              log.path_for(T0).name == "2026-08-03.jsonl")
        late_utc = datetime(2026, 8, 3, 23, 30, tzinfo=ZoneInfo("UTC"))
        check("23:30 UTC is already the NEXT Prague day",
              log.path_for(late_utc).name == "2026-08-04.jsonl")

        print("verdict records carry every number the decision used:")
        flat = fr.AccountState(equity=25_000, balance=25_000, day_start_balance=25_000)
        v = fr.evaluate(cfg, flat)
        check("write succeeds", log.record_verdict("SNAPSHOT", v, T0))
        recs = log.read_day(T0)
        check("one record on disk", len(recs) == 1)
        r = recs[0]
        for key in ("daily_loss_used", "daily_soft", "daily_hard", "drawdown_used",
                    "drawdown_hard", "profit_usd", "can_open", "must_flatten",
                    "breached", "can_pass", "reasons", "ts", "ftmo_day"):
            check(f"  record carries {key}", key in r)
        check("reasons survive as a list", isinstance(r["reasons"], list))

        print("append-only:")
        log.record_verdict("SNAPSHOT", v, T0)
        log.record_verdict("SNAPSHOT", v, T0)
        check("records accumulate, never overwrite", len(log.read_day(T0)) == 3)
        check("written counter tracks it", log.written == 3)

        print("a breach is on disk immediately:")
        dead = fr.AccountState(equity=23_700, balance=23_700, day_start_balance=25_000)
        log.record_verdict("BREACHED", fr.evaluate(cfg, dead), T0)
        found = [x for x in log.read_day(T0) if x.get("breached")]
        check("breach record present", len(found) == 1)
        check("...and flagged breached", found[0]["breached"] is True)
        check("...with the loss that caused it",
              abs(found[0]["daily_loss_used"] - 1300.0) < 1e-9)

        print("monitor events round-trip:")
        log2 = AuditLog(d / "sub")
        m = fm.EquityMonitor(cfg, balance=25_000.0, now=T0)
        p = fm.OpenPosition(1, 1, "EURUSD", "BUY", 1000.0, 100.0, 99.75)
        for ev in m.on_position_opened(p, T0):
            log2.record_event(ev, T0)
        m.on_quote(1, 100.0, 100.02, T0)
        for ev in m.on_quote(1, 98.95, 98.97, T0):
            log2.record_event(ev, T0)
        recs = log2.read_day(T0)
        kinds = [x["kind"] for x in recs]
        check("POSITION_OPENED logged", "POSITION_OPENED" in kinds)
        check("posture change logged", fm.BLOCKED in kinds)
        blocked = [x for x in recs if x["kind"] == fm.BLOCKED][0]
        check("posture change carries the verdict metrics", "daily_loss_used" in blocked)
        check("...and the equity at that moment", blocked["equity"] < 25_000)

        print("order decisions record the sizing maths:")
        log3 = AuditLog(d / "dec")
        spec = fz.SymbolSpec(1, "EURUSD", 100, 100, 1_000_000_00)
        sized = fz.size_position(spec, 25_000, 1.0, 1.0850, 1.0750, 1.0, 1000.0)
        log3.record_decision("EURUSD", sized, T0, verdict=v)
        rec = log3.read_day(T0)[0]
        check("accepted order logs as ORDER_ACCEPTED", rec["kind"] == "ORDER_ACCEPTED")
        check("volume recorded", rec["volume"] > 0)
        check("risk recorded", rec["risk_at_stop"] > 0)
        check("stop recorded", rec["stop_price"] == 1.0750)
        refused = fz.size_position(spec, 25_000, 1.0, 1.0850, 1.0750, 1.0, 0.0)
        log3.record_decision("EURUSD", refused, T0)
        rec = log3.read_day(T0)[1]
        check("refusal logs as ORDER_REFUSED", rec["kind"] == "ORDER_REFUSED")
        check("...with volume 0", rec["volume"] == 0)
        check("...and the reason preserved", len(rec["reasons"]) > 0)

        print("snapshots are rate-limited, not dropped silently:")
        log4 = AuditLog(d / "snap", snapshot_interval_s=300.0)
        check("first snapshot writes", log4.snapshot(v, T0))
        from datetime import timedelta
        check("a snapshot 60s later is suppressed",
              log4.snapshot(v, T0 + timedelta(seconds=60)) is False)
        check("...and nothing was written", len(log4.read_day(T0)) == 1)
        check("301s later it writes again",
              log4.snapshot(v, T0 + timedelta(seconds=301)))
        check("force overrides the interval",
              log4.snapshot(v, T0 + timedelta(seconds=302), force=True))
        check("suppression is not counted as a failure", log4.write_failures == 0)

        print("a write failure never raises into the caller:")
        broken = AuditLog(Path("/dev/null/cannot/exist"))
        result = broken.record_verdict("SNAPSHOT", v, T0)
        check("returns False rather than raising", result is False)
        check("failure counted", broken.write_failures == 1)
        check("...and a second failure counted", (broken.record_verdict("X", v, T0),
                                                  broken.write_failures)[1] == 2)

        print("a torn last line does not lose the day:")
        log5 = AuditLog(d / "torn")
        log5.record_verdict("SNAPSHOT", v, T0)
        with open(log5.path_for(T0), "a", encoding="utf-8") as fh:
            fh.write('{"kind":"BREACHED","ts":"2026-08-0')  # killed mid-write
        recs = log5.read_day(T0)
        check("good records still parse", len(recs) == 2)
        check("the torn line is flagged, not fatal",
              recs[1]["kind"] == "UNPARSEABLE")
        check("...and its content is preserved for forensics",
              "BREACHED" in recs[1]["raw"])

        print("day listing and missing days:")
        check("days_available lists written days",
              log.days_available() == ["2026-08-03"])
        check("reading an absent day returns empty, not an error",
              log.read_day("2020-01-01") == [])

    print("\nFAILED" if failures else "\nAll FTMO audit-log selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FTMO rule-decision audit log.")
    ap.add_argument("--selftest", action="store_true", help="Run offline checks and exit.")
    ap.add_argument("--report", nargs="?", const="", metavar="YYYY-MM-DD",
                    help="Replay a day's decisions (default: the most recent).")
    args = ap.parse_args()
    if args.report is not None:
        return report(args.report or None)
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
