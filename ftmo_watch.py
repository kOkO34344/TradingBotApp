#!/usr/bin/env python3
"""
ftmo_watch.py — the continuous equity watcher. Long-lived, always on.

This is the DRIVER that `ftmo_monitor.py` never had.

Until 2026-08-11 `ftmo_monitor.EquityMonitor` was instantiated in exactly one
place: its own selftest. CLAUDE.md, `ftmo_runner.py`'s docstring and
`ftmo_smoke_order.py` all described it as "the continuous watcher" and it had
never run as a process, because no process existed to run it. The protection
rule 3 is written around was documented, tested, and absent.

**THIS IS THE FOURTH UNATTENDED PATH IN THIS PROJECT, AND THE FIRST THAT CAN
PLACE AN ORDER WITHOUT A SCHEDULE.** The others are the retired
`autotrade_runner.py`, the FTMO runner, and the IC-screen override. Flag it the
same way: it is an owner decision (2026-08-11, taken with the alert-only
alternative stated first), not a precedent, and not something to extend by
analogy. What it removes is the human in the loop on a flatten; every limit
still applies.

WHY IT CAN ACT
Every FTMO limit is measured on equity INCLUDING floating P&L, so the account
can fail with no order placed and nothing realised. The runner fires 27 times
inside a 6.5-hour window; the other 17.5 hours of every weekday, and all
weekend, nothing was watching. With `buffer_pct` at 0.01 there is $6.25
between the flatten threshold and FTMO's hard cliff. An alert into a phone at
04:00 is not protection.

WHAT IT DOES NOT DO
It does not open positions, ever. It has no signal, no sizer and no forecast;
it cannot import torch. The only order it can place is a CLOSE.

Usage:
  python3 ftmo_watch.py --selftest    offline, no network, no credentials
  python3 ftmo_watch.py --dry-run     connects and watches, closes NOTHING
  python3 ftmo_watch.py               live
"""

from __future__ import annotations

import argparse
import inspect
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "TelegramBot"))

import ftmo_monitor as fm          # noqa: E402
import ftmo_rules as fr            # noqa: E402
import ftmo_runner as fru          # noqa: E402
import ftmo_session as fs          # noqa: E402
# Imported for VOLUME_SCALE only — the single source of truth for the
# centi-unit conversion. The watcher never sizes anything; the selftest
# asserts that by naming the sizing FUNCTIONS, not by banning the import,
# because duplicating the constant here is how a 100x error gets in.
import ftmo_sizing as fz           # noqa: E402
import trade_journal as tj         # noqa: E402

try:
    from notify import send_telegram
except Exception:                                             # noqa: BLE001
    def send_telegram(*_a, **_k):                             # pragma: no cover
        return False

PRAGUE = ZoneInfo("Europe/Prague")
LOG_PATH = BASE_DIR / "ftmo_watch.log"
VENUE = "ftmo"


# One watcher at a time. launchd starts this hourly as a SUPERSET (see the
# plist), so without a lock a session would accumulate one watcher per hour,
# each holding its own caffeinate assertion and its own broker session.
WATCH_LOCK = BASE_DIR / "ftmo_watch.lock"


class WindowClosed(Exception):
    """The trading session ended. Not an error — the watcher's job is done."""

# How often to sweep the quote cache and beat the monitor's heartbeat.
POLL_S = 1.0
# How often to re-read positions and balance from the venue. The runner opens
# and closes positions behind our back 27 times a day, and an execution event
# can be missed across a reconnect, so the authoritative read is on a timer
# rather than assumed from the event stream. Same two-tier reasoning as
# ftmo_closes: the stream is nearly free and catches almost nothing.
RESYNC_S = 30.0
# Back-off between reconnection attempts after the session drops.
RECONNECT_S = 20.0


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def tell(message: str) -> None:
    """Phone alert that can never break the watcher. The watcher staying up is
    worth more than any single message arriving."""
    try:
        send_telegram(message)
    except Exception:                                         # noqa: BLE001
        pass


def to_monitor_position(p: fs.Position) -> fm.OpenPosition:
    """A venue position in the terms the monitor values it in.

    A position with NO stop is given a stop equal to its entry, so
    `risk_at_stop()` reports 0 rather than an invented number. The watcher is
    not the right place to guess what a missing stop would have been — the
    runner's UNPROTECTED check is what notices that, and inventing a distance
    here would feed a fiction into the portfolio budget.
    """
    return fm.OpenPosition(
        position_id=p.position_id,
        symbol_id=p.symbol_id,
        symbol_name=p.symbol,
        side=p.side,
        units=abs(p.volume) / fz.VOLUME_SCALE,
        entry_price=p.entry_price,
        stop_price=p.stop_loss if p.stop_loss else p.entry_price,
    )


class Watcher:
    """Streams the account into an EquityMonitor and acts on what comes out."""

    def __init__(self, dry_run: bool = False, journal_path: Path | None = None,
                 notifier=None):
        self.dry_run = dry_run
        self.journal_path = journal_path or tj.JOURNAL_FILE
        self.notify = notifier or tell
        self.session: fs.FTMOSession | None = None
        self.monitor: fm.EquityMonitor | None = None
        self._fed: dict[int, float] = {}      # symbol_id -> last quote ts fed
        self._last_resync = 0.0
        # Held for the process lifetime. Must stay referenced: letting it be
        # collected closes the file and drops the flock, which would make the
        # single-watcher guarantee look like it works while protecting nothing.
        self._lock = None

    # ------------------------------------------------------------- plumbing

    def connect(self) -> None:
        specs = fs._load_specs_quietly()
        self.session = fs.FTMOSession(specs=specs)
        self.session.start()
        log(f"connected, account {self.session.account_id}")

    def build_monitor(self) -> None:
        """Seed the monitor from persisted state, never from initial_capital.

        `ftmo_runner_state.json` holds the day-start balance the DAILY LIMIT is
        measured against. Seeding from the live balance instead would restart
        every daily loss at zero on each reconnect — a limit that can never
        trip, which is precisely the failure ftmo_runner_state.json exists to
        prevent. If there is no state file we fall back to the live balance,
        which is the same choice advance_state makes on a first run.
        """
        settings = fru.load_settings()
        cfg = fru.autotrade_config(settings)
        config = fru.config_from(cfg)
        acct = self.session.account()
        stored = fru.load_state()
        now = datetime.now(PRAGUE)

        if stored is None:
            log("no runner state — seeding the day baseline from the live "
                "balance")
            day_start = acct["balance"]
            highest = acct["balance"]
            days, profits = 0, ()
        else:
            day_start = stored.day_start_balance
            highest = stored.highest_eod_balance
            days, profits = stored.trading_days, tuple(stored.daily_profits)

        self.monitor = fm.EquityMonitor(
            config, balance=acct["balance"], now=now,
            highest_eod_balance=highest, trading_days=days,
            daily_profits=profits)
        # The monitor seeds day_start_balance from `balance`; override it with
        # the persisted baseline, which is the whole point of the state file.
        self.monitor.day_start_balance = day_start
        log(f"monitor seeded: balance {acct['balance']:,.2f}, day baseline "
            f"{day_start:,.2f}, trailing high {highest:,.2f}")

    def resync(self, now: datetime) -> list[fm.MonitorEvent]:
        """Re-read the book from the venue and reconcile it into the monitor.

        A FAILED READ IS NOT AN EMPTY ACCOUNT. This propagates the exception to
        the caller, which keeps the previous view and retries — it must never
        return "no positions" from a read that did not happen. That mistake
        journalled a phantom liquidation on 2026-07-25 and is the single most
        expensive shape of bug this project has produced.
        """
        positions = self.session.refresh_positions()      # raises on failure
        acct = self.session.account()
        events = list(self.monitor.on_balance(acct["balance"], now))

        live = {p.position_id: p for p in positions}
        known = set(self.monitor.positions)

        for pid in known - set(live):
            # Closed behind our back. ftmo_closes owns the JOURNAL row for
            # this — writing one here too would duplicate the audit trail, and
            # the reconcile job already runs every 30 minutes. The monitor
            # only needs to stop valuing it.
            events += self.monitor.on_position_closed(
                pid, realized_pnl=0.0, new_balance=acct["balance"], now=now)
        for pid, p in live.items():
            if pid not in known:
                events += self.monitor.on_position_opened(
                    to_monitor_position(p), now)

        if live:
            self.session.subscribe([p.symbol for p in live.values()])
        return events

    def feed_quotes(self) -> list[fm.MonitorEvent]:
        """Push new ticks from the session cache into the monitor.

        Each quote is fed with ITS OWN timestamp, never with `now`. Feeding a
        stale quote under a fresh timestamp would tell the monitor the book is
        currently valued when it is not, and staleness detection — the whole
        UNKNOWN posture — would silently stop working. That is the failure
        mode this project keeps rediscovering under different names.
        """
        events: list[fm.MonitorEvent] = []
        for pos in list(self.monitor.positions.values()):
            q = self.session.quotes.get(pos.symbol_id)
            if q is None or not q.bid or not q.ask or not q.ts:
                continue
            if self._fed.get(pos.symbol_id) == q.ts:
                continue
            self._fed[pos.symbol_id] = q.ts
            events += self.monitor.on_quote(
                pos.symbol_id, q.bid, q.ask,
                datetime.fromtimestamp(q.ts, PRAGUE))
        return events

    # -------------------------------------------------------------- actions

    def flatten(self, reason: str, now: datetime) -> None:
        """Close everything. NO rule engine, NO sizer, NO limit in front.

        Rule 3: a limit caps NEW exposure, and anything that can block an exit
        raises risk. Each position is closed INDEPENDENTLY so one failure
        cannot strand the rest — the 2026-07-27 shape, where a single blocked
        close left the whole book open.

        The positions are re-read from the venue first. Acting on the
        monitor's cached view would be acting on a belief; a flatten is the
        one action where being wrong is most expensive.
        """
        log(f"FLATTEN: {reason}")
        self.notify(f"\U0001f6a8 FTMO FLATTEN\n{reason}\nclosing the book now")
        try:
            positions = self.session.refresh_positions()
        except Exception as e:                                # noqa: BLE001
            log(f"  cannot read positions to flatten: {e} — NOT assuming flat")
            self.notify(f"⚠ FTMO flatten could not read the book: {e}")
            return

        if not positions:
            log("  nothing open")
            return

        closed, failed = [], []
        for p in positions:
            if self.dry_run:
                log(f"  DRY RUN would close {p.symbol} ({p.volume})")
                continue
            try:
                self.session.close_position(p.position_id, p.volume)
                tj.append(action="EXIT", symbol=p.symbol, sec_type="cfd",
                          venue=VENUE, quantity=p.volume, status="sent",
                          detail=f"watcher flatten: {reason}; "
                                 f"position {p.position_id}",
                          path=self.journal_path)
                closed.append(p.symbol)
                log(f"  closed {p.symbol}")
            except Exception as e:                            # noqa: BLE001
                tj.append(action="ERROR", symbol=p.symbol, sec_type="cfd",
                          venue=VENUE, quantity=p.volume, status="failed",
                          detail=f"watcher flatten failed: {e}",
                          path=self.journal_path)
                failed.append(f"{p.symbol}: {e}")
                log(f"  FAILED to close {p.symbol}: {e}")

        self.notify(f"FTMO flatten done\nclosed: {', '.join(closed) or 'none'}"
                    + (f"\nFAILED: {'; '.join(failed)}" if failed else ""))

    def handle(self, events: list[fm.MonitorEvent], now: datetime) -> None:
        for ev in events:
            log(str(ev))
            if ev.kind == fm.WARNING:
                self.notify(f"⚠ FTMO early warning\n{ev.detail}")
            elif ev.kind == fm.BLOCKED:
                self.notify(f"\U0001f7e1 FTMO blocked — no new entries\n"
                            f"{ev.detail}")
            elif ev.kind == fm.UNKNOWN:
                self.notify(f"❓ FTMO equity UNKNOWN — quotes stale\n"
                            f"{ev.detail}")
            elif ev.kind == fm.BREACHED:
                self.notify(f"\U0001f6a8 FTMO BREACHED\n{ev.detail}")
            elif ev.kind == "DAY_ROLLED":
                self.notify(f"FTMO day rolled\n{ev.detail}")
            # FLATTEN is handled last and separately, so the alert above has
            # already been sent even if closing raises.
            if ev.kind == fm.FLATTEN:
                self.flatten(ev.detail, now)

    # ----------------------------------------------------------------- loop

    def run_forever(self) -> int:
        # Refuse to even connect outside the session, so a stray invocation
        # costs one settings read rather than a broker login. Same shape as
        # the runner checking the window before opening its audit log.
        allowed, why = fru.within_trading_window(datetime.now(PRAGUE))
        if not allowed:
            log(f"outside the trading window — not starting: {why}")
            return 0

        # launchd starts this hourly so a crashed session recovers within the
        # hour and a host-timezone change cannot miss the open. The lock is
        # what makes that superset safe: the second and later starts of a
        # session exit here, in about a fifth of a second, holding nothing.
        self._lock = fru.acquire_run_lock(WATCH_LOCK)
        if self._lock is None:
            log("a watcher is already running — leaving it alone")
            return 0

        while True:
            try:
                self.connect()
                self.build_monitor()
                now = datetime.now(PRAGUE)
                self.handle(self.resync(now), now)
                self._last_resync = time.monotonic()
                self.notify("\U0001f441 FTMO watcher started"
                            + (" (DRY RUN — closes nothing)"
                               if self.dry_run else ""))
                self._loop()
            except WindowClosed as e:
                log(f"session over — stopping: {e}")
                self.notify(f"\U0001f441 FTMO watcher stopped — {e}\n"
                            f"the Mac is free to sleep; venue-side stops and "
                            f"targets remain in force")
                return 0
            except KeyboardInterrupt:
                log("stopped by hand")
                return 0
            except Exception as e:                            # noqa: BLE001
                log(f"ERROR: {type(e).__name__}: {e} — reconnecting in "
                    f"{RECONNECT_S:.0f}s")
                self.notify(f"⚠ FTMO watcher dropped: "
                            f"{type(e).__name__}: {e}\nreconnecting")
            finally:
                if self.session is not None:
                    try:
                        self.session.stop()
                    except Exception:                         # noqa: BLE001
                        pass
                    self.session = None
            time.sleep(RECONNECT_S)

    def _loop(self) -> None:
        while True:
            now = datetime.now(PRAGUE)
            # SESSION-SCOPED BY DECISION (owner, 2026-08-11). This watcher
            # holds the Mac awake for as long as it runs, so "when does it
            # stop" is a battery question as much as a trading one: an
            # assertion held around the clock costs ~20-50Wh a session on a
            # laptop that is usually unplugged, for hours when no order can be
            # placed anyway. It exits at the close and launchd starts it again
            # at the next open.
            allowed, why = fru.within_trading_window(now)
            if not allowed:
                raise WindowClosed(why)
            events = self.feed_quotes()
            # The heartbeat is what notices SILENCE. Every other entry point is
            # driven by an incoming message and so cannot detect messages
            # stopping, which is the case that matters.
            events += self.monitor.heartbeat(now)
            if time.monotonic() - self._last_resync >= RESYNC_S:
                events += self.resync(now)
                self._last_resync = time.monotonic()
            self.handle(events, now)
            time.sleep(POLL_S)


# ---- selftest marker ----
# ------------------------------------------------------------ selftest

def selftest() -> int:
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    print("the watcher can close, and can NEVER open:")
    # Only the IMPLEMENTATION, never this selftest. The checks below search
    # for strings like "place_market" that necessarily appear in the checks
    # themselves, so scanning the whole module would fail every one of them
    # for the wrong reason.
    whole = inspect.getsource(sys.modules[__name__])
    src = whole.split("# ---- selftest marker ----")[0]
    check("it never places a market order",
          "place_market" not in src)
    check("it never imports the model",
          "import kronos_agent" not in src and "kronos_agent" not in src)
    check("it never sizes a position",
          "size_position" not in src and "plan_entry" not in src)
    check("it never plans orders", "plan_orders" not in src)
    check("...and ftmo_sizing is used ONLY for the volume constant",
          src.count("fz.") == 1 and "fz.VOLUME_SCALE" in src)
    check("the only venue write is close_position",
          src.count("self.session.close_position") == 1)

    print("flatten is ungated (rule 3):")
    fsrc = inspect.getsource(Watcher.flatten)
    check("no rule engine in front of the flatten",
          "evaluate(" not in fsrc and "can_open" not in fsrc)
    check("no portfolio budget in front of it",
          "max_position_risk" not in fsrc)
    check("positions are RE-READ from the venue, not taken from cache",
          "refresh_positions()" in fsrc)
    check("a failed read does NOT proceed as if flat",
          "NOT assuming flat" in fsrc and "return" in fsrc)
    check("each close is attempted independently",
          fsrc.count("try:") >= 2)
    check("every close is journalled", "tj.append(" in fsrc)
    check("a failed close is journalled too", 'action="ERROR"' in fsrc)

    print("quotes carry their OWN timestamp, never `now`:")
    qsrc = inspect.getsource(Watcher.feed_quotes)
    check("the quote is fed at its own ts",
          "datetime.fromtimestamp(q.ts" in qsrc)
    check("...and a re-seen tick is not re-fed",
          "self._fed.get(" in qsrc)
    check("the reason is stated where it can be read",
          "staleness detection" in qsrc)

    print("resync propagates a failed read (2026-07-25 phantom liquidation):")
    rsrc = inspect.getsource(Watcher.resync)
    check("no try/except swallowing the position read",
          "refresh_positions()" in rsrc and "try:" not in rsrc)
    check("the reason is written down", "FAILED READ IS NOT AN EMPTY" in rsrc)

    print("the day baseline comes from persisted state, not the live balance:")
    # Whitespace-normalised: these docstrings wrap, so a literal search for a
    # phrase that spans a line break fails for a reason that has nothing to do
    # with what is being asserted.
    bsrc = " ".join(inspect.getsource(Watcher.build_monitor).split())
    check("it loads the runner state file", "fru.load_state()" in bsrc)
    check("it overrides the monitor's own seeding",
          "day_start_balance = day_start" in bsrc)
    check("...and says why a live-balance seed would be wrong",
          "can never trip" in bsrc)

    print("event routing:")
    hsrc = inspect.getsource(Watcher.handle)
    for kind in ("WARNING", "BLOCKED", "UNKNOWN", "BREACHED", "FLATTEN"):
        check(f"{kind} is handled", f"fm.{kind}" in hsrc)
    check("FLATTEN alerts BEFORE it closes, so the message survives a failure",
          hsrc.index("self.notify") < hsrc.index("self.flatten"))

    print("a position with no stop is not given an invented one:")
    p = fs.Position(position_id=1, symbol_id=2, symbol="EURUSD", side="BUY",
                    volume=1000, entry_price=1.1, stop_loss=None,
                    take_profit=None)
    mp = to_monitor_position(p)
    check("a missing stop becomes the entry price, so risk reads 0.0",
          mp.stop_price == p.entry_price and mp.risk_at_stop() == 0.0)
    p2 = fs.Position(position_id=1, symbol_id=2, symbol="EURUSD", side="BUY",
                     volume=1000, entry_price=1.1, stop_loss=1.09,
                     take_profit=None)
    check("a real stop is carried through",
          abs(to_monitor_position(p2).stop_price - 1.09) < 1e-12)

    # cTrader reports volume in CENTI-UNITS. Getting this wrong does not raise;
    # it silently misvalues floating P&L and risk_at_stop by 100x, on the
    # numbers the flatten decision is made from. The first draft of this file
    # had exactly that bug via a hasattr() fallback that never matched.
    check("volume is converted from centi-units to units",
          abs(to_monitor_position(p2).units - 10.0) < 1e-12)
    check("...using ftmo_sizing's constant, not a local copy",
          fz.VOLUME_SCALE == 100)
    check("a 100x error would be visible in risk_at_stop",
          abs(to_monitor_position(p2).risk_at_stop() - 0.1) < 1e-9)

    print("the watcher is SESSION-SCOPED, not 24/7 (2026-08-11):")
    lsrc = inspect.getsource(Watcher._loop)
    check("the loop checks the trading window every pass",
          "within_trading_window" in lsrc)
    check("...and raises WindowClosed rather than looping on",
          "raise WindowClosed" in lsrc)
    rfsrc = inspect.getsource(Watcher.run_forever)
    check("it refuses to CONNECT outside the window",
          rfsrc.index("within_trading_window") < rfsrc.index("self.connect()"))
    check("WindowClosed exits 0 — it is not an error",
          "except WindowClosed" in rfsrc and "return 0" in rfsrc)
    check("...and is caught BEFORE the generic reconnect handler",
          rfsrc.index("except WindowClosed")
          < rfsrc.index("except Exception"))
    check("the reason is recorded where the next reader will find it",
          "battery question" in " ".join(lsrc.split()))

    print("only ONE watcher runs at a time (launchd starts it hourly):")
    check("it takes a lock before connecting",
          rfsrc.index("acquire_run_lock") < rfsrc.index("self.connect()"))
    check("...after the window check, so an out-of-window start is free",
          rfsrc.index("within_trading_window") < rfsrc.index("acquire_run_lock"))
    check("a second watcher exits 0 rather than competing",
          "already running" in rfsrc)
    check("the lock handle is retained on the instance, not discarded",
          "self._lock = fru.acquire_run_lock" in rfsrc)
    check("...and it uses its own lock file, not the runner's",
          "WATCH_LOCK" in rfsrc and WATCH_LOCK != fru.LOCK_FILE)

    print("\nFAILED" if failures else
          "\nAll ftmo_watch offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Continuous FTMO equity watcher.")
    ap.add_argument("--selftest", action="store_true",
                    help="Offline checks; no network, no credentials.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Watch and alert, but close NOTHING.")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return Watcher(dry_run=args.dry_run).run_forever()


if __name__ == "__main__":
    sys.exit(main())
