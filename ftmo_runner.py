#!/usr/bin/env python3
"""
ftmo_runner.py — unattended Kronos trading on the FTMO venue.

The autotrade path for FTMO. `autotrade_runner.py` is its IBKR counterpart and
the two are deliberately separate scripts: they talk to different brokers, have
different limit models, and must be armable independently. A single toggle
covering both would mean turning FTMO on could not be done without also
reasoning about a retired venue.

WHAT IT DOES, ONCE PER INVOCATION
  connect -> read positions and balance -> ask the rule engine ->
  pull FTMO's own daily bars -> Kronos forecast -> rank -> plan ->
  exits, then entries -> verify every stop against the venue ->
  journal + audit + text.

READ THIS BEFORE TRUSTING ANY OUTPUT OF THIS FILE.

Kronos has **no demonstrated edge on any asset class this project has ever
measured.** All four classes were IC-screened on 2026-08-03 and all four
failed (|t| <= 1.55 in every direction); the matched momentum baseline failed
all four as well. Rule 9 says Kronos may only trade a class that passed its own
screen, so on the evidence this script should fire on nothing.

It runs anyway, on the owner's explicit instruction. **That is the THIRD
deliberate exception to rule 5**, after `autotrade_runner.py` (rule 7) and the
unattended FTMO path (rule 9), and it is recorded the same way in
`ftmo_signal.py`. Flag it as an exception; it is not precedent and it is not a
validated strategy. What autonomy removes is the human approval step, never a
limit — every order still passes the rule engine, the sizer's per-trade and
per-portfolio caps, and the stop validation in `ftmo_session`.

OFF BY DEFAULT. Set `ftmo.autotrade.enabled` in `trader_settings.json`, or use
the arm/disarm control in the web UI. A missing key reads as false.

WHY THE DAY-BOUNDARY STATE IS PERSISTED
The FTMO daily limit is measured against the BALANCE at 00:00 CE(S)T, and the
1-Step trailing floor moves off a completed day's CLOSING balance. A one-shot
script cannot know either without remembering. `ftmo_runner_state.json` carries
exactly the fields `ftmo_rules.AccountState` needs across invocations, and
`advance_state()` rolls it through `ftmo_rules.roll_day()` — the same pure
function the monitor uses — rather than reimplementing the boundary. Without
this the daily limit would evaluate against the current balance every run,
i.e. a daily loss of 0.00 forever: a limit that can never trip.

WHAT THIS SCRIPT DOES NOT DO
It is not the equity monitor. It runs, decides, and exits; between runs nothing
here is watching. `ftmo_monitor.py` is the continuous watcher and the account
can still breach with this script not running, because every FTMO limit is
measured on equity including floating P&L. The stops attached at entry are the
protection that survives this process exiting.

Usage:
  python3 ftmo_runner.py --selftest    offline, no network, no credentials
  python3 ftmo_runner.py --dry-run     live data, full plan, places NOTHING
  python3 ftmo_runner.py --force       run even if the toggle is off
  python3 ftmo_runner.py               normal unattended invocation
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR / "TelegramBot"))

import ftmo_audit as fa           # noqa: E402
import ftmo_rules as fr           # noqa: E402
import ftmo_service as svc        # noqa: E402
import ftmo_session as fs         # noqa: E402
import ftmo_signal as sig         # noqa: E402
import ftmo_sizing as fz          # noqa: E402
import trade_journal as tj        # noqa: E402

try:
    from notify import send_telegram
except Exception:                                             # noqa: BLE001
    def send_telegram(*_a, **_k):                             # pragma: no cover
        return False

SETTINGS = BASE_DIR / "trader_settings.json"
STATE_FILE = BASE_DIR / "ftmo_runner_state.json"
LOG_PATH = BASE_DIR / "ftmo_runner.log"
JOURNAL = tj.JOURNAL_FILE
VENUE = "ftmo"

PRAGUE = ZoneInfo("Europe/Prague")

# ---------------------------------------------------------- trading window
# Owner decision, 2026-08-06: run hourly between 16:30 and 11:30 the next
# morning, every day EXCEPT Sunday, in Sofia time.
#
# THIS CHECK IS AUTHORITATIVE; the launchd schedule is only a superset.
# Exactly the arrangement `autotrade_runner.py` uses for NYSE hours, and for
# the same reason: a plist encodes wall-clock local time and cannot express
# "except Sunday" across a window that wraps midnight without 120 entries,
# while `zoneinfo` handles the DST switch (EEST +03 -> EET +02) and the
# weekday rule in a few lines that can be unit-tested offline with no clock,
# no network and no credentials.
#
# The window WRAPS MIDNIGHT, so it is a union and not a range: a moment
# qualifies when it is at or after 16:30, OR at or before 11:30. Writing this
# as `OPEN <= t <= CLOSE` would be empty for every t, which is the obvious way
# to get it silently wrong.
#
# "Except Sunday" is applied to the CALENDAR day in Sofia, so Saturday's
# evening leg (16:30-23:30) runs and Sunday's morning leg does not. That is
# the literal reading of the instruction and it is the one that can be checked
# by looking at a clock, rather than asking which session a firing "belongs
# to" across a midnight boundary.
TRADING_TZ = ZoneInfo("Europe/Sofia")
WINDOW_OPEN = dtime(16, 30)     # inclusive
WINDOW_CLOSE = dtime(11, 30)    # inclusive, the NEXT morning
CLOSED_WEEKDAY = 6              # Monday=0 ... Sunday=6


def within_trading_window(now: datetime) -> tuple[bool, str]:
    """Is `now` inside the configured trading window? Pure, tz-aware.

    Returns (allowed, reason) so the caller can log WHY it did nothing —
    a runner that no-ops without saying which rule stopped it is
    indistinguishable from one that is broken.

    A naive datetime is refused rather than assumed to be local, the same
    stance `ftmo_rules.ftmo_day()` takes. Host-local time is not the venue's
    time and guessing has already cost this project real money elsewhere.
    """
    if now.tzinfo is None:
        raise RunnerError(
            "within_trading_window needs a timezone-aware datetime; a naive "
            "one would be interpreted as host-local time, which is not "
            "necessarily Sofia")
    local = now.astimezone(TRADING_TZ)
    stamp = local.strftime("%a %H:%M %Z")

    if local.weekday() == CLOSED_WEEKDAY:
        return False, f"Sunday — the runner does not trade on Sundays ({stamp})"

    t = local.time()
    if t >= WINDOW_OPEN or t <= WINDOW_CLOSE:
        return True, f"inside the 16:30-11:30 Sofia window ({stamp})"
    return False, (f"outside the 16:30-11:30 Sofia window ({stamp}) — "
                   f"the gap between 11:30 and 16:30 is deliberate")


class RunnerError(RuntimeError):
    """A condition the runner refuses to trade through."""


# ------------------------------------------------------------------ logging

def log(msg: str, path: Path = LOG_PATH) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ settings

def load_settings(path: Path = SETTINGS) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def autotrade_config(settings: dict) -> dict:
    """The `ftmo.autotrade` block, with safe defaults for every key.

    Absent keys read as OFF and as the most conservative value, so a settings
    file that predates this feature — or one a failed edit truncated — can only
    ever mean "do not trade", never "trade with a default limit".
    """
    block = (settings.get("ftmo") or {}).get("autotrade") or {}
    return {
        "enabled": bool(block.get("enabled", False)),
        "risk_pct": float(block.get("risk_pct", 1.0)),
        "margin_pct": float(block.get("rotation_margin_pct", 1.0)),
        "top_n": int(block.get("top_n", sig.TOP_N)),
        "sample_count": int(block.get("sample_count", 10)),
        "product": str(block.get("product", "2step")),
        "buffer_pct": float(block.get("buffer_pct", 0.20)),
        "initial_capital": float(block.get("initial_capital", 25_000.0)),
    }


def config_from(cfg: dict) -> fr.FTMOConfig:
    return fr.FTMOConfig(product=cfg["product"], buffer_pct=cfg["buffer_pct"],
                         initial_capital=cfg["initial_capital"])


# -------------------------------------------------------- day-boundary state

@dataclass
class RunnerState:
    """What the rule engine needs remembered between one-shot invocations."""
    ftmo_day: str
    day_start_balance: float
    highest_eod_balance: float
    trading_days: int = 0
    daily_profits: list = field(default_factory=list)
    opened_today: bool = False

    def to_json(self) -> dict:
        return {"ftmo_day": self.ftmo_day,
                "day_start_balance": self.day_start_balance,
                "highest_eod_balance": self.highest_eod_balance,
                "trading_days": self.trading_days,
                "daily_profits": list(self.daily_profits),
                "opened_today": self.opened_today}

    @staticmethod
    def from_json(d: dict) -> "RunnerState":
        return RunnerState(
            ftmo_day=str(d["ftmo_day"]),
            day_start_balance=float(d["day_start_balance"]),
            highest_eod_balance=float(d["highest_eod_balance"]),
            trading_days=int(d.get("trading_days", 0)),
            daily_profits=list(d.get("daily_profits", [])),
            opened_today=bool(d.get("opened_today", False)))


def load_state(path: Path = STATE_FILE) -> RunnerState | None:
    try:
        return RunnerState.from_json(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_state(state: RunnerState, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state.to_json(), indent=2) + "\n")


def advance_state(stored: RunnerState | None, balance: float, now: datetime,
                  config: fr.FTMOConfig) -> tuple[RunnerState, list[str]]:
    """Bring persisted state up to the current FTMO day. Pure.

    First run seeds from the live balance rather than from `initial_capital`:
    the account may already have traded, and seeding a $25,000 day-start onto a
    $24,300 balance would invent a $700 loss that never happened and could
    block trading on the spot.

    A crossed boundary rolls through `ftmo_rules.roll_day()` so the trailing
    high-water mark can only ever move in that one function. Several boundaries
    at once (a weekend, or the script simply not running for days) collapse to
    ONE roll, because balance is the only realised quantity available and it did
    not change on the days nothing happened — inventing a zero-profit entry per
    calendar day would pad `daily_profits`, which feeds the Best Day Rule.
    """
    today = fr.ftmo_day(now).isoformat()
    notes: list[str] = []

    if stored is None:
        notes.append(f"no prior state — seeding day {today} from the live "
                     f"balance {balance:,.2f} (not from initial_capital)")
        return RunnerState(ftmo_day=today, day_start_balance=balance,
                           highest_eod_balance=balance), notes

    if stored.ftmo_day == today:
        return stored, notes

    day_profit = balance - stored.day_start_balance
    prior = fr.AccountState(
        equity=balance, balance=balance,
        day_start_balance=stored.day_start_balance,
        highest_eod_balance=stored.highest_eod_balance,
        trading_days=stored.trading_days,
        daily_profits=tuple(stored.daily_profits))
    rolled = fr.roll_day(config, prior, closing_balance=balance,
                         day_profit=day_profit,
                         opened_a_position=stored.opened_today)
    notes.append(
        f"day rolled {stored.ftmo_day} -> {today}: closing balance "
        f"{balance:,.2f}, day P&L {day_profit:+,.2f}, trading days "
        f"{rolled.trading_days}, trailing high {rolled.highest_eod_balance:,.2f}")
    return RunnerState(
        ftmo_day=today, day_start_balance=rolled.day_start_balance,
        highest_eod_balance=rolled.highest_eod_balance,
        trading_days=rolled.trading_days,
        daily_profits=list(rolled.daily_profits),
        opened_today=False), notes


# ------------------------------------------------------------ account state

def floating_pnl(session, positions) -> tuple[float, int]:
    """Floating P&L marked at the EXIT side of the spread, and the unpriced count.

    Marking at the mid flatters equity by half a spread per position, which is
    the unsafe direction on a limit measured in equity. A position with no
    quote contributes nothing and is COUNTED, so the caller can refuse to trade
    on an equity it knows is incomplete rather than treating unknown as flat.
    """
    total, unpriced = 0.0, 0
    for p in positions:
        q = session.quote(p.symbol)
        mark = q.exit_side_price(p.side) if q else None
        if mark is None or not p.entry_price:
            unpriced += 1
            continue
        units = p.volume / 100.0
        delta = (mark - p.entry_price) if p.side == "BUY" else (p.entry_price - mark)
        total += delta * units
    return total, unpriced


def build_account_state(session, positions, state: RunnerState
                        ) -> tuple[fr.AccountState, int]:
    acct = session.account()
    balance = acct["balance"]
    floating, unpriced = floating_pnl(session, positions)
    return fr.AccountState(
        equity=balance + floating,
        balance=balance,
        day_start_balance=state.day_start_balance,
        highest_eod_balance=state.highest_eod_balance,
        open_position_count=len(positions),
        trading_days=state.trading_days,
        daily_profits=tuple(state.daily_profits)), unpriced


# ----------------------------------------------------------------- recording

def journal_ftmo(event: str, symbol: str = "", action: str = "",
                 quantity="", price="", stop="", target="", status: str = "",
                 detail: str = "", path: Path = JOURNAL) -> None:
    """One journal row for the FTMO venue (rule 6).

    `sec_type` carries the asset class rather than an IBKR contract type —
    there is no `Contract` object on this venue, and "cfd" alone would throw
    away the one distinction that matters when reading the book back.

    `target` is the take-profit. It reuses the column IBKR's bracket path
    already writes, so recording FTMO targets needed no schema change — worth
    noting because extending `JOURNAL_COLUMNS` is the operation that silently
    corrupts this file if the header is not migrated with it.
    """
    tj.append(path, {"event": event, "symbol": symbol, "sec_type": "cfd",
                     "action": action, "quantity": quantity, "price": price,
                     "stop": stop, "target": target, "status": status,
                     "detail": detail, "venue": VENUE})


# ------------------------------------------------------------------ the run

def flatten_all(session, positions, audit, now, dry_run: bool) -> list[dict]:
    """Close every open position. Never gated by any limit.

    Deliberately has nothing in front of it. This project has documented what
    happens when an exposure limit blocks an exit: the then-$5,000 IBKR
    notional cap made two winners un-exitable. A limit caps NEW exposure;
    blocking a close raises risk, which is the opposite of the job.

    Each close is attempted independently so one failure cannot strand the
    rest — a partial flatten is bad, a flatten that stops at the first error
    is worse.
    """
    results = []
    for p in positions:
        if dry_run:
            log(f"  DRY RUN would close {p.symbol} position {p.position_id} "
                f"volume {p.volume}")
            results.append({"symbol": p.symbol, "closed": False,
                            "dry_run": True})
            continue
        try:
            res = session.close_position(p.position_id, p.volume)
            journal_ftmo("FLATTEN", symbol=p.symbol, action="CLOSE",
                         quantity=p.volume, status="sent",
                         detail=f"rule engine FLATTEN; position {p.position_id}")
            audit.record_event(_evt("FLATTEN_EXECUTED", p.symbol), now,
                               position_id=p.position_id, volume=p.volume)
            results.append({"symbol": p.symbol, "closed": True, "raw": res})
            log(f"  closed {p.symbol} position {p.position_id}")
        except Exception as e:                                # noqa: BLE001
            journal_ftmo("ERROR", symbol=p.symbol, action="CLOSE",
                         quantity=p.volume, status="failed",
                         detail=f"FLATTEN failed: {e}")
            results.append({"symbol": p.symbol, "closed": False,
                            "error": str(e)})
            log(f"  FAILED to close {p.symbol}: {e}")
    return results


class _Evt:
    """Minimal MonitorEvent shape for `ftmo_audit.record_event`."""
    def __init__(self, kind, detail, posture=None, equity=None):
        self.kind = kind
        self.detail = detail
        self.posture = posture
        self.equity = equity
        self.verdict = None


def _evt(kind: str, detail: str, posture=None, equity=None) -> _Evt:
    return _Evt(kind, detail, posture, equity)


def execute_plan(session, plan: dict, positions, audit, now,
                 dry_run: bool) -> dict:
    """Exits first, then entries, then verify every stop against the venue.

    Exits run before entries so the position-count cap has headroom freed
    before anything tries to use it — the same ordering `paper_trader` uses on
    the IBKR side and for the same reason.
    """
    by_symbol = {p.symbol: p for p in positions}
    done = {"closed": [], "opened": [], "failed": [], "unprotected": []}

    for sym in plan["exits"]:
        p = by_symbol.get(sym)
        if p is None:
            continue
        if dry_run:
            log(f"  DRY RUN would exit {sym} (volume {p.volume})")
            continue
        try:
            session.close_position(p.position_id, p.volume)
            journal_ftmo("EXIT", symbol=sym, action="CLOSE", quantity=p.volume,
                         status="sent",
                         detail=f"rotated out; position {p.position_id}")
            done["closed"].append(sym)
            log(f"  exited {sym}")
        except Exception as e:                                # noqa: BLE001
            journal_ftmo("ERROR", symbol=sym, action="CLOSE",
                         quantity=p.volume, status="failed",
                         detail=f"exit failed: {e}")
            done["failed"].append({"symbol": sym, "stage": "exit",
                                   "error": str(e)})
            log(f"  FAILED to exit {sym}: {e}")

    for e in plan["entries"]:
        sym = e["symbol"]
        target = f"{e['take_profit_price']:.5f}"
        if dry_run:
            log(f"  DRY RUN would BUY {sym} volume {e['volume']} "
                f"stop {e['stop_price']:.5f} tp {target} "
                f"risk ${e['risk_at_stop']:,.2f}")
            continue
        journal_ftmo("SUBMIT", symbol=sym, action="BUY", quantity=e["volume"],
                     price=f"{e['entry_price']:.5f}",
                     stop=f"{e['stop_price']:.5f}", target=target,
                     status="sending",
                     detail=(f"{e['asset_class']}; kronos pred "
                             f"{e['predicted_return_pct']:+.2f}%; "
                             f"risk ${e['risk_at_stop']:,.2f}; "
                             f"reward ${e['reward_at_target']:,.2f}"))
        try:
            res = session.place_market(
                symbol=sym, side="BUY", volume=e["volume"],
                stop_price=e["stop_price"], reference_price=e["entry_price"],
                take_profit_price=e["take_profit_price"],
                label="kronos")
            journal_ftmo("RESULT", symbol=sym, action="BUY",
                         quantity=e["volume"], price=f"{e['entry_price']:.5f}",
                         stop=f"{e['stop_price']:.5f}", target=target,
                         status="accepted",
                         detail=f"venue response {res.get('response')}")
            done["opened"].append(sym)
            log(f"  BOUGHT {sym} volume {e['volume']} "
                f"stop {e['stop_price']:.5f} tp {target}")
        except Exception as ex:                               # noqa: BLE001
            journal_ftmo("REJECTED", symbol=sym, action="BUY",
                         quantity=e["volume"], price=f"{e['entry_price']:.5f}",
                         stop=f"{e['stop_price']:.5f}", target=target,
                         status="rejected",
                         detail=str(ex))
            done["failed"].append({"symbol": sym, "stage": "entry",
                                   "error": str(ex)})
            log(f"  REJECTED {sym}: {ex}")

    # Verify against the VENUE, not against our own belief that we sent a stop.
    # A rejected cTrader order arrives as an event rather than an error
    # response, so "we sent it" has already been shown to be worth nothing
    # here — the first live FTMO order was refused while the code reported
    # {'sent': True}. Only a read-back counts.
    if not dry_run and (done["opened"] or done["closed"]):
        time.sleep(2.0)
        try:
            naked = session.unprotected_positions()
            for p in naked:
                done["unprotected"].append(p.symbol)
                journal_ftmo("UNPROTECTED", symbol=p.symbol, action="",
                             quantity=p.volume, status="no stop",
                             detail=(f"position {p.position_id} has NO "
                                     f"server-side stop after entry"))
                audit.record_event(_evt("UNPROTECTED", p.symbol), now,
                                   position_id=p.position_id)
                log(f"  UNPROTECTED: {p.symbol} position {p.position_id}")
        except Exception as e:                                # noqa: BLE001
            log(f"  could not verify stop protection: {e}")
            journal_ftmo("ERROR", status="unknown",
                         detail=f"stop verification failed: {e}")

        # Targets are read back from the venue too, for the same reason and
        # with the same distrust of {'sent': True}. Deliberately a separate,
        # quieter finding than UNPROTECTED: a position without a target is
        # still fully stop-protected, so this records the gap without
        # implying the account is at risk.
        try:
            for p in session.untargeted_positions():
                done.setdefault("untargeted", []).append(p.symbol)
                journal_ftmo("NO_TARGET", symbol=p.symbol, action="",
                             quantity=p.volume, status="no take-profit",
                             detail=(f"position {p.position_id} has NO "
                                     f"server-side take-profit — stop is "
                                     f"unaffected"))
                log(f"  NO TARGET: {p.symbol} position {p.position_id} "
                    f"(stop still in place)")
        except Exception as e:                                # noqa: BLE001
            log(f"  could not verify take-profit attachment: {e}")
    return done


def run(force: bool = False, dry_run: bool = False,
        settings_path: Path = SETTINGS, state_path: Path = STATE_FILE) -> int:
    settings = load_settings(settings_path)
    cfg = autotrade_config(settings)

    if not cfg["enabled"] and not force:
        log("ftmo autotrade disabled — no-op")
        return 0

    config = config_from(cfg)
    now = datetime.now(PRAGUE)

    # Checked BEFORE the audit log is opened and long before torch is
    # imported, so an out-of-window firing costs a settings read and nothing
    # else. With ~20 in-window firings a day the out-of-window ones are the
    # majority of wakeups, and they must stay free.
    allowed, why = within_trading_window(now)
    if not allowed and not force:
        log(f"outside the trading window — no-op: {why}")
        return 0
    log(f"trading window: {why}")

    audit = fa.AuditLog()

    # Imported here, not at module scope: Kronos pulls in torch, and this
    # script is invoked on a schedule whether or not it is armed. The
    # disabled path above must stay a cheap settings read, never a 2 GB load.
    sys.path.insert(0, str(BASE_DIR / "KronosAI"))
    import kronos_agent as ka
    import pandas as pd

    specs = svc.load_symbol_specs()
    pairs = sig.build_universe(specs, sig.load_universe())
    symbols = [s for s, _ in pairs]
    classes = dict(pairs)
    log(f"universe: {len(symbols)} symbols across "
        f"{len(set(classes.values()))} classes")

    session = fs.FTMOSession(specs=specs)
    session.start()
    try:
        log(f"connected, account {session.account_id}")
        session.subscribe(symbols)
        time.sleep(2.0)                       # let the first spot ticks arrive

        positions = session.refresh_positions()
        acct = session.account()
        stored = load_state(state_path)
        state_obj, notes = advance_state(stored, acct["balance"], now, config)
        for n in notes:
            log(n)

        account_state, unpriced = build_account_state(session, positions,
                                                      state_obj)
        verdict = fr.evaluate(config, account_state)
        log(verdict.summary())
        audit.record_verdict("RUNNER_EVALUATION", verdict, now,
                             equity=account_state.equity,
                             open_positions=len(positions),
                             unpriced_positions=unpriced)

        # ---- FLATTEN takes precedence over everything, including a signal.
        if verdict.must_flatten:
            log("rule engine says FLATTEN — closing everything, no forecast run")
            results = flatten_all(session, positions, audit, now, dry_run)
            send_telegram(
                f"\U0001f6a8 FTMO FLATTEN\n{verdict.summary()}\n"
                f"closed {sum(1 for r in results if r.get('closed'))}"
                f"/{len(positions)} positions")
            if not dry_run:
                save_state(state_obj, state_path)
            return 0

        # ---- An equity we cannot fully price is not an equity to trade on.
        if unpriced and not force:
            msg = (f"{unpriced} open position(s) have no quote yet — equity is "
                   f"incomplete, so no new entries this cycle")
            log(msg)
            journal_ftmo("BLOCKED", status="unpriced", detail=msg)
            audit.record_event(_evt("BLOCKED", msg), now)
            return 0

        # ---- bars, with the scaling cross-check that a 1000x bug taught us
        bars_by_symbol, frames = {}, {}
        for sym in symbols:
            try:
                session.assert_bars_match_quote(sym)
            except fs.SessionError as e:
                log(f"  {sym}: {e}")
                journal_ftmo("BLOCKED", symbol=sym, status="bad data",
                             detail=str(e))
                continue
            bars = session.trendbars(sym, "D1", sig.BARS_NEEDED)
            if len(bars) < ka.LOOKBACK:
                log(f"  {sym}: only {len(bars)} daily bars, need "
                    f"{ka.LOOKBACK} — skipped")
                continue
            bars_by_symbol[sym] = bars
            idx = pd.to_datetime([b["ts"] for b in bars], unit="s")
            frames[sym] = pd.DataFrame(
                {"open": [b["open"] for b in bars],
                 "high": [b["high"] for b in bars],
                 "low": [b["low"] for b in bars],
                 "close": [b["close"] for b in bars],
                 "volume": [b["volume"] for b in bars]}, index=idx)
        log(f"bars usable for {len(frames)}/{len(symbols)} symbols")
        if not frames:
            log("no symbol had usable history — nothing to forecast")
            journal_ftmo("BLOCKED", status="no data",
                         detail="no symbol had usable daily history")
            return 1

        t0 = time.time()
        _, _, pred_dfs = ka.forecast_frames(frames,
                                            sample_count=cfg["sample_count"])
        log(f"Kronos forecast for {len(pred_dfs)} symbols in "
            f"{time.time() - t0:.0f}s")

        ranked = sig.rank_candidates(pred_dfs, bars_by_symbol, classes)
        held = [p.symbol for p in positions]
        plan = sig.plan_orders(session, config, account_state, ranked, held,
                               risk_pct=cfg["risk_pct"],
                               margin_pct=cfg["margin_pct"],
                               top_n=cfg["top_n"])
        print(sig.format_plan(plan))

        gap = plan.get("rank_gap")
        if gap is not None and gap < 1.0:
            log(f"NOTE: rank {cfg['top_n']}/{cfg['top_n'] + 1} gap is "
                f"{gap:.2f} pt — the selection at that boundary is close to a "
                f"coin flip, and the rotation margin is what is holding it "
                f"steady, not the signal")

        for s in plan["skipped"]:
            journal_ftmo("BLOCKED", status="skipped", detail=s)
        audit.record_verdict("RUNNER_PLAN", verdict, now,
                             target=plan.get("target", []),
                             exits=plan["exits"],
                             entries=[e["symbol"] for e in plan["entries"]],
                             rank_gap=gap, skipped=plan["skipped"])

        if not plan["entries"] and not plan["exits"]:
            log("no change — holding")
            return 0

        done = execute_plan(session, plan, positions, audit, now, dry_run)

        if not dry_run:
            if done["opened"]:
                state_obj.opened_today = True
            save_state(state_obj, state_path)
            lines = [f"\U0001f4c8 FTMO autotrade"]
            if done["opened"]:
                lines.append(f"opened: {', '.join(done['opened'])}")
            if done["closed"]:
                lines.append(f"closed: {', '.join(done['closed'])}")
            if done["failed"]:
                lines.append(f"FAILED: {done['failed']}")
            if done["unprotected"]:
                lines.append(f"⚠ UNPROTECTED: "
                             f"{', '.join(done['unprotected'])}")
            lines.append(verdict.summary())
            send_telegram("\n".join(lines))
        return 0
    finally:
        try:
            session.stop()
        except Exception:                                     # noqa: BLE001
            pass


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    import tempfile
    import shutil

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

    tmpdir = Path(tempfile.mkdtemp(prefix="ftmo-runner-selftest-"))
    cfg25 = fr.FTMOConfig(initial_capital=25_000.0)

    print("the toggle defaults to OFF and cannot be defaulted ON:")
    check("a settings file with no ftmo block is disabled",
          autotrade_config({})["enabled"] is False)
    check("an empty ftmo block is disabled",
          autotrade_config({"ftmo": {}})["enabled"] is False)
    check("an empty autotrade block is disabled",
          autotrade_config({"ftmo": {"autotrade": {}}})["enabled"] is False)
    check("an explicit true arms it",
          autotrade_config({"ftmo": {"autotrade": {"enabled": True}}})["enabled"]
          is True)
    check("IBKR's autotrade toggle does NOT arm FTMO",
          autotrade_config({"autotrade": {"enabled": True}})["enabled"] is False)

    print("trading window (16:30-11:30 Sofia, every day except Sunday):")

    def sofia(y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=TRADING_TZ)

    # 2026-08-06 is a Thursday; 08-09 is a Sunday.
    def allowed(dt):
        return within_trading_window(dt)[0]

    check("16:30 exactly is INSIDE (inclusive open)",
          allowed(sofia(2026, 8, 6, 16, 30)))
    check("16:29 is outside", not allowed(sofia(2026, 8, 6, 16, 29)))
    check("11:30 exactly is INSIDE (inclusive close)",
          allowed(sofia(2026, 8, 6, 11, 30)))
    check("11:31 is outside", not allowed(sofia(2026, 8, 6, 11, 31)))
    check("the 11:30-16:30 gap is closed (13:00)",
          not allowed(sofia(2026, 8, 6, 13, 0)))
    check("the window WRAPS midnight — 23:30 is inside",
          allowed(sofia(2026, 8, 6, 23, 30)))
    check("...and 00:30 is inside",
          allowed(sofia(2026, 8, 7, 0, 30)))
    check("...and 04:30 is inside",
          allowed(sofia(2026, 8, 7, 4, 30)))

    print("Sunday is excluded on the CALENDAR day, both legs:")
    check("Sunday 04:30 is refused", not allowed(sofia(2026, 8, 9, 4, 30)))
    check("Sunday 20:30 is refused", not allowed(sofia(2026, 8, 9, 20, 30)))
    check("Saturday evening still runs (16:30)",
          allowed(sofia(2026, 8, 8, 16, 30)))
    check("Saturday 23:30 still runs", allowed(sofia(2026, 8, 8, 23, 30)))
    check("Monday morning still runs (00:30)",
          allowed(sofia(2026, 8, 10, 0, 30)))
    check("the Sunday refusal says why",
          "Sunday" in within_trading_window(sofia(2026, 8, 9, 4, 30))[1])

    print("the window is evaluated in SOFIA time, not host time:")
    # 03:30 UTC on a Thursday is 06:30 Sofia (inside). Same instant, other tz.
    utc_moment = datetime(2026, 8, 6, 3, 30, tzinfo=ZoneInfo("UTC"))
    check("a UTC-expressed instant is converted before testing",
          allowed(utc_moment))
    # 09:00 UTC = 12:00 Sofia, which is in the closed gap.
    check("...and the conversion can move a moment OUT of the window",
          not allowed(datetime(2026, 8, 6, 9, 0, tzinfo=ZoneInfo("UTC"))))
    check("a naive datetime is refused rather than assumed local",
          raises(lambda: within_trading_window(datetime(2026, 8, 6, 17, 0)),
                 "timezone-aware"))

    print("DST: the rule is wall-clock Sofia time on both sides of the switch")
    # Sofia is EEST (+03) in August and EET (+02) in December.
    check("17:00 in August (EEST) is inside",
          allowed(sofia(2026, 8, 6, 17, 0)))
    check("17:00 in December (EET) is also inside",
          allowed(datetime(2026, 12, 3, 17, 0, tzinfo=TRADING_TZ)))
    check("13:00 in December is still in the closed gap",
          not allowed(datetime(2026, 12, 3, 13, 0, tzinfo=TRADING_TZ)))

    print("the firing count matches the agreed 20 per day:")
    fires = [h for h in range(24)
             if allowed(sofia(2026, 8, 6, h, 30))]
    check("20 hourly firings per non-Sunday day", len(fires) == 20)
    check("...covering 16:00-23:00 and 00:00-11:00",
          fires == list(range(0, 12)) + list(range(16, 24)))

    print("day-boundary state:")
    now = datetime(2026, 8, 6, 12, 0, tzinfo=PRAGUE)
    s0, notes0 = advance_state(None, 24_300.0, now, cfg25)
    check("a first run seeds day-start from the LIVE balance",
          s0.day_start_balance == 24_300.0)
    check("...and not from initial_capital, which would invent a $700 loss",
          s0.day_start_balance != 25_000.0)
    check("...and says so", any("live balance" in n for n in notes0))
    v0 = fr.evaluate(cfg25, fr.AccountState(equity=24_300.0, balance=24_300.0,
                                            day_start_balance=s0.day_start_balance))
    check("so a fresh install on a drawn-down account is not instantly blocked",
          v0.daily_loss_used == 0.0)

    same, notes1 = advance_state(s0, 24_100.0, now, cfg25)
    check("the same day does not roll", same.day_start_balance == 24_300.0)
    check("...and logs nothing", notes1 == [])

    tomorrow = datetime(2026, 8, 7, 9, 0, tzinfo=PRAGUE)
    s0.opened_today = True
    rolled, notes2 = advance_state(s0, 24_800.0, tomorrow, cfg25)
    check("crossing midnight rolls the day", rolled.ftmo_day == "2026-08-07")
    check("day-start becomes the closing balance",
          rolled.day_start_balance == 24_800.0)
    check("the completed day's P&L is recorded",
          rolled.daily_profits == [500.0])
    check("a day a position was opened counts as a trading day",
          rolled.trading_days == 1)
    check("opened_today resets", rolled.opened_today is False)
    check("the trailing high-water mark advanced",
          rolled.highest_eod_balance == 24_800.0)

    print("the trailing mark moves only on a closing balance:")
    spike = RunnerState(ftmo_day="2026-08-06", day_start_balance=25_000.0,
                        highest_eod_balance=25_000.0)
    after, _ = advance_state(spike, 25_400.0, tomorrow, cfg25)
    check("a day that closed at 25,400 marks 25,400",
          after.highest_eod_balance == 25_400.0)
    down = RunnerState(ftmo_day="2026-08-06", day_start_balance=25_400.0,
                       highest_eod_balance=25_400.0)
    after2, _ = advance_state(down, 25_100.0, tomorrow, cfg25)
    check("a losing day does NOT lower the mark",
          after2.highest_eod_balance == 25_400.0)

    print("a multi-day gap collapses to one roll:")
    friday = RunnerState(ftmo_day="2026-08-01", day_start_balance=25_000.0,
                         highest_eod_balance=25_000.0)
    monday = datetime(2026, 8, 4, 9, 0, tzinfo=PRAGUE)
    gapped, _ = advance_state(friday, 25_000.0, monday, cfg25)
    check("no zero-profit padding is invented for the days between",
          gapped.daily_profits == [0.0])
    check("...and the day is still brought up to date",
          gapped.ftmo_day == "2026-08-04")

    print("state survives a round trip through disk:")
    sp = tmpdir / "state.json"
    save_state(rolled, sp)
    back = load_state(sp)
    check("every field round-trips", back.to_json() == rolled.to_json())
    check("a corrupt state file reads as None, not as a crash",
          (sp.write_text("{ not json"), load_state(sp))[1] is None)
    check("a missing state file reads as None",
          load_state(tmpdir / "nope.json") is None)

    print("a corrupt state file cannot silently disable the daily limit:")
    seeded, seed_notes = advance_state(None, 24_000.0, now, cfg25)
    check("it re-seeds from the live balance rather than assuming a limit is met",
          seeded.day_start_balance == 24_000.0 and seed_notes)

    print("floating P&L marks at the exit side:")

    class _Q:
        def __init__(self, bid, ask):
            self.bid, self.ask = bid, ask

        def exit_side_price(self, side):
            return self.bid if side == "BUY" else self.ask

    class _Sess:
        def __init__(self, quotes):
            self._q = quotes

        def quote(self, sym):
            return self._q.get(sym)

    class _P:
        def __init__(self, symbol, side, volume, entry):
            self.symbol, self.side = symbol, side
            self.volume, self.entry_price = volume, entry

    sess = _Sess({"XAUUSD": _Q(2000.0, 2001.0)})
    pnl, unp = floating_pnl(sess, [_P("XAUUSD", "BUY", 100, 1990.0)])
    check("a long is marked at the BID, not the mid",
          abs(pnl - 10.0) < 1e-9)
    pnl2, _ = floating_pnl(sess, [_P("XAUUSD", "SELL", 100, 2010.0)])
    check("a short is marked at the ASK", abs(pnl2 - 9.0) < 1e-9)
    pnl3, unp3 = floating_pnl(sess, [_P("NOQUOTE", "BUY", 100, 1.0)])
    check("an unquoted position contributes 0 and is COUNTED, never assumed flat",
          pnl3 == 0.0 and unp3 == 1)

    print("journal rows are venue-labelled:")
    jp = tmpdir / "journal.csv"
    journal_ftmo("SUBMIT", symbol="BTCUSD", action="BUY", quantity=1,
                 path=jp)
    import csv
    rows = list(csv.DictReader(open(jp, newline="")))
    check("the row is labelled venue=ftmo", rows[0]["venue"] == "ftmo")
    check("the header carries all 12 columns",
          tj.read_header(jp) == tj.JOURNAL_COLUMNS)
    check("asset type is recorded as cfd", rows[0]["sec_type"] == "cfd")

    print("flatten is never gated (rule 3):")
    import inspect
    src = inspect.getsource(flatten_all)
    check("flatten_all consults no rule engine, sizer or limit",
          "evaluate(" not in src and "size_position" not in src
          and "can_open" not in src)
    check("...and keeps going after one close fails",
          "for p in positions" in src and "except Exception" in src)

    print("the runner refuses to trade on an equity it cannot price:")
    rsrc = inspect.getsource(run)
    check("unpriced positions block new entries",
          "unpriced and not force" in rsrc)
    check("FLATTEN is decided before any forecast is run",
          rsrc.index("must_flatten") < rsrc.index("forecast_frames"))
    check("torch is imported only after the enabled check",
          rsrc.index('enabled"] and not force') < rsrc.index("import kronos_agent"))
    check("the trading window is checked before the audit log is opened",
          rsrc.index("within_trading_window(now)") < rsrc.index("fa.AuditLog()"))
    check("...and before torch is imported, so an out-of-window wakeup is free",
          rsrc.index("within_trading_window(now)") < rsrc.index("import kronos_agent"))
    check("an out-of-window firing is a no-op, not a refusal to trade later",
          "outside the trading window — no-op" in rsrc)
    check("stops are verified by reading the venue back, not from our own send",
          "unprotected_positions" in inspect.getsource(execute_plan))
    check("exits are executed before entries",
          inspect.getsource(execute_plan).index('plan["exits"]')
          < inspect.getsource(execute_plan).index('plan["entries"]'))

    print("every entry carries a take-profit (owner decision 2026-08-08):")
    esrc = inspect.getsource(execute_plan)
    check("the target is sent with the order, not attached afterwards",
          "take_profit_price=e[" in esrc)
    check("targets are verified by reading the venue back, like stops",
          "untargeted_positions" in esrc)
    check("a missing target is reported SEPARATELY from a missing stop, so a "
          "naked stop cannot hide inside a routine warning",
          "NO_TARGET" in esrc and "UNPROTECTED" in esrc
          and esrc.index("UNPROTECTED") < esrc.index("NO_TARGET"))
    check("the dry run shows the target it would send",
          "tp {target}" in esrc or "tp {e[" in esrc)

    print("the take-profit is journalled, in the column that already exists:")
    jp2 = tmpdir / "journal_tp.csv"
    journal_ftmo("SUBMIT", symbol="ETHUSD", action="BUY", quantity=210,
                 price="1917.42000", stop="1799.10000", target="2196.98000",
                 path=jp2)
    trow = list(csv.DictReader(open(jp2, newline="")))[0]
    check("the target lands in the `target` column", trow["target"] == "2196.98000")
    check("...and the stop is still in its own column",
          trow["stop"] == "1799.10000")
    check("the header still has exactly 12 columns — no migration was needed",
          tj.read_header(jp2) == tj.JOURNAL_COLUMNS)
    check("a row written without a target is still valid (smoke/exit rows)",
          journal_ftmo("EXIT", symbol="ETHUSD", path=jp2) is None)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("\nFAILED" if failures else
          "\nAll ftmo_runner offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Unattended Kronos trading on the FTMO venue.")
    ap.add_argument("--selftest", action="store_true",
                    help="Offline checks; no network, no credentials.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Live data, full plan, places NOTHING.")
    ap.add_argument("--force", action="store_true",
                    help="Run even if the toggle is off.")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    try:
        return run(force=args.force, dry_run=args.dry_run)
    except Exception as e:                                    # noqa: BLE001
        log(f"ERROR: {type(e).__name__}: {e}")
        send_telegram(f"⚠ FTMO runner error\n{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
