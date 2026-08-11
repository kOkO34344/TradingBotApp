#!/usr/bin/env python3
"""
ftmo_monitor.py — continuous equity monitor for the FTMO venue. Pure state
machine, no network.

This is what turns `ftmo_rules.py` from a calculator into actual protection.
The rule engine can answer "given this equity, what am I allowed to do"; this
module is what keeps equity current, tick by tick, and decides WHEN to ask.

WHY A MONITOR AND NOT A PRE-TRADE CHECK. Every FTMO limit is measured on equity
including floating P&L, so the account can fail with no order placed and
nothing realised. `RiskGuard.check()` on the IBKR side is consulted only while
an order is being placed and provably cannot see this — the 2026-07-23 GOOGL
stop-out moved the account $422 with nothing running and was invisible for two
days. On a $25,000 FTMO account with a $1,250 hard daily limit, the equivalent
blind spot is not a reporting gap, it is a failed account.

FOUR THINGS THIS GETS RIGHT, each because getting it wrong is a known failure:

1. EDGE-TRIGGERED, NOT LEVEL-TRIGGERED. Actions fire on posture CHANGE, not on
   every tick. A level-triggered flatten at 50 ticks/second would emit 50
   flatten instructions for one event.

2. STALE DATA IS NOT SAFE DATA. If quotes stop arriving for a symbol we hold,
   equity is UNKNOWN — not unchanged, and certainly not fine. This project has
   made the opposite mistake twice: an empty `ib.positions()` read as "flat"
   manufactured a phantom liquidation, and a wedged `reqAllOpenOrders` would
   have read as four naked positions. Unknown gets its own posture here, it
   blocks new entries immediately, and it escalates to flatten only after a
   longer interval — because a two-second network blip is not a reason to
   liquidate, and sixty seconds of blindness on a leveraged book is.

3. FLOATING P&L USES THE EXIT SIDE OF THE SPREAD. A long is marked at the bid
   and a short at the ask, because that is what closing it would actually get.
   Marking at the mid flatters the account by half a spread on every position,
   which is the unsafe direction on a limit measured in equity.

4. THE DAY ROLLS AT 00:00 CE(S)T. The daily loss baseline is the balance at the
   Prague midnight, not a rolling 24 hours and not the host's midnight. The
   monitor detects the boundary itself rather than trusting a caller to.

WHAT IT DOES NOT DO: place or cancel anything. It emits typed events and the
executor acts on them. Keeping the decision separate from the action is what
makes the whole thing testable against a synthetic tick stream, which is the
only way to exercise a breach without failing a real account.

Offline selftest:  python3 ftmo_monitor.py --selftest
"""

from __future__ import annotations

import argparse
import inspect
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import ftmo_rules as fr

# Postures, worst-last. The monitor emits an event whenever this changes.
OK = "OK"
BLOCKED = "BLOCKED"           # soft limit: no new entries, existing left alone
UNKNOWN = "UNKNOWN"           # quotes stale: cannot value the book
FLATTEN = "FLATTEN"           # flatten threshold: close everything now
BREACHED = "BREACHED"         # an FTMO limit is already gone

# How long a quote may go unrefreshed before the book is considered unvalued,
# and how long before blindness itself justifies closing out.
STALE_BLOCK_S = 10.0
STALE_FLATTEN_S = 60.0

# EARLY WARNINGS. Fractions of the daily SOFT budget at which to speak up
# while everything is still fine.
#
# Added 2026-08-11 with buffer_pct at 0.01, which leaves only $6.25 between
# the flatten threshold and FTMO's hard $1,250 cliff. At that spacing the
# first posture change the owner ever hears about is effectively the last
# one: BLOCKED, FLATTEN and BREACHED all land within $12.50 of each other.
# These are informational and edge-triggered ONCE PER DAY per level, so they
# add two messages on a bad day and none on a good one.
#
# WARNING is deliberately NOT a posture. Postures drive the executor — a
# FLATTEN closes the book — and an advisory message must never be able to
# move that state machine. It rides the same event stream so the alerting
# layer needs no new wiring, and it carries the posture unchanged.
EARLY_WARNING_FRACTIONS = (0.50, 0.75)
WARNING = "WARNING"


@dataclass(frozen=True)
class OpenPosition:
    """One live position, in the terms the monitor needs to value it."""
    position_id: int
    symbol_id: int
    symbol_name: str
    side: str                    # "BUY" or "SELL"
    units: float
    entry_price: float
    stop_price: float
    quote_to_account_rate: float = 1.0

    def __post_init__(self):
        if self.side.upper() not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {self.side!r}")
        if self.units <= 0:
            raise ValueError("units must be positive; direction lives in `side`")
        if self.quote_to_account_rate <= 0:
            raise ValueError("quote_to_account_rate must be positive")

    def mark_price(self, bid: float, ask: float) -> float:
        """The price this position would actually CLOSE at."""
        return bid if self.side.upper() == "BUY" else ask

    def floating_pnl(self, bid: float, ask: float) -> float:
        """Unrealised P&L in account currency, marked at the exit side."""
        mark = self.mark_price(bid, ask)
        move = (mark - self.entry_price) if self.side.upper() == "BUY" \
            else (self.entry_price - mark)
        return move * self.units * self.quote_to_account_rate

    def risk_at_stop(self) -> float:
        """What a stop-out costs from ENTRY, in account currency.

        Deliberately measured from entry rather than from the current mark:
        this feeds the portfolio budget in ftmo_rules.max_position_risk_usd(),
        which is about how much the book can lose in total, not about how much
        is left to lose from here.
        """
        return abs(self.entry_price - self.stop_price) * self.units \
            * self.quote_to_account_rate


def _age_s(age: float) -> str:
    """Format a staleness age, without rendering infinity as 'infs'."""
    return "never quoted" if age == float("inf") else f"{age:.0f}s"


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    at: datetime


@dataclass(frozen=True)
class MonitorEvent:
    """Something the executor and the alerting layer need to act on."""
    kind: str            # posture name, or POSITION_OPENED / POSITION_CLOSED / DAY_ROLLED
    detail: str
    equity: float
    posture: str
    verdict: fr.RuleVerdict | None = None

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


class EquityMonitor:
    """Keeps equity current and emits actions on posture changes.

    Every `on_*` method returns the list of events that transition produced —
    usually empty. The caller journals them, texts them, and acts on FLATTEN.
    """

    def __init__(self, config: fr.FTMOConfig, balance: float, now: datetime,
                 highest_eod_balance: float | None = None,
                 trading_days: int = 0,
                 daily_profits: tuple[float, ...] = (),
                 stale_block_s: float = STALE_BLOCK_S,
                 stale_flatten_s: float = STALE_FLATTEN_S):
        if now.tzinfo is None:
            raise ValueError("EquityMonitor needs a timezone-aware `now` — a naive "
                             "one would put the 00:00 CE(S)T rollover in the wrong place")
        self.config = config
        self.balance = balance
        self.day_start_balance = balance
        self.highest_eod_balance = (balance if highest_eod_balance is None
                                    else highest_eod_balance)
        self.trading_days = trading_days
        self.daily_profits = tuple(daily_profits)
        self.opened_today = False
        self.day = fr.ftmo_day(now)
        self.stale_block_s = stale_block_s
        self.stale_flatten_s = stale_flatten_s

        self.positions: dict[int, OpenPosition] = {}
        self.quotes: dict[int, Quote] = {}
        # When each position was registered, so a position whose symbol has not
        # been quoted YET ages from its open rather than reading as infinitely
        # stale. See stalest_age_s().
        self._opened_at: dict[int, datetime] = {}
        self.posture = OK
        self._breach_emitted = False
        # Which early-warning levels have already been announced today.
        # Reset on the day roll, so each level speaks at most once per day.
        self._warned: set[float] = set()

    # ---------------------------------------------------------------- state

    def floating_pnl(self) -> float | None:
        """Total unrealised P&L, or None if any held symbol has no quote yet.

        None means "cannot value the book", which is deliberately NOT zero.
        Returning zero here would report a healthy account while blind, which
        is the whole class of bug this module exists to avoid.
        """
        total = 0.0
        for p in self.positions.values():
            q = self.quotes.get(p.symbol_id)
            if q is None:
                return None
            total += p.floating_pnl(q.bid, q.ask)
        return total

    def equity(self) -> float | None:
        floating = self.floating_pnl()
        return None if floating is None else self.balance + floating

    def open_risk(self) -> float:
        return sum(p.risk_at_stop() for p in self.positions.values())

    def stalest_age_s(self, now: datetime) -> float:
        """Age of the oldest quote among symbols we actually hold. 0 if flat.

        A position whose symbol has NEVER been quoted is aged from the moment
        it was opened, not treated as infinitely stale. That distinction is not
        cosmetic: a position is registered from the execution event, which
        arrives before the first spot tick for that symbol, so treating
        "no quote yet" as infinite age made the monitor demand an immediate
        flatten of every position at the instant it opened. Found by replaying
        a synthetic breach day end-to-end; the unit tests missed it because
        they all happened to quote a symbol before asserting on posture.

        Aging from the open gives a brand-new position the same grace period
        as any other, and it still goes UNKNOWN and then FLATTEN if the quote
        genuinely never arrives.
        """
        if not self.positions:
            return 0.0
        ages = []
        for p in self.positions.values():
            q = self.quotes.get(p.symbol_id)
            if q is None:
                opened_at = self._opened_at.get(p.position_id)
                if opened_at is None:
                    return float("inf")  # unknown provenance: treat as fully stale
                ages.append((now - opened_at).total_seconds())
            else:
                ages.append((now - q.at).total_seconds())
        return max(ages)

    def account_state(self) -> fr.AccountState:
        """Snapshot in the shape the rule engine consumes.

        Falls back to balance when equity is unvaluable. That is safe ONLY
        because the caller checks staleness separately and forces UNKNOWN —
        never read this as an all-clear on its own.
        """
        eq = self.equity()
        return fr.AccountState(
            equity=self.balance if eq is None else eq,
            balance=self.balance,
            day_start_balance=self.day_start_balance,
            highest_eod_balance=self.highest_eod_balance,
            open_position_count=len(self.positions),
            trading_days=self.trading_days,
            daily_profits=self.daily_profits,
        )

    # ------------------------------------------------------------ transitions

    def _assess(self, now: datetime) -> list[MonitorEvent]:
        """Recompute posture and emit an event only if it changed."""
        events: list[MonitorEvent] = []
        events.extend(self._roll_if_new_day(now))

        state = self.account_state()
        verdict = fr.evaluate(self.config, state)
        eq = self.equity()
        age = self.stalest_age_s(now)

        if verdict.breached:
            posture = BREACHED
            detail = "; ".join(verdict.reasons)
        elif verdict.must_flatten:
            posture = FLATTEN
            detail = "; ".join(verdict.reasons)
        elif self.positions and age >= self.stale_flatten_s:
            posture = FLATTEN
            detail = (f"quotes stale {_age_s(age)} (>= {self.stale_flatten_s:.0f}s) with "
                      f"{len(self.positions)} position(s) open — closing out rather "
                      f"than staying blind on a leveraged book")
        elif self.positions and age >= self.stale_block_s:
            posture = UNKNOWN
            detail = (f"quotes stale {_age_s(age)} (>= {self.stale_block_s:.0f}s) — equity "
                      f"cannot be valued; this is UNKNOWN, not safe")
        elif not verdict.can_open:
            posture = BLOCKED
            detail = "; ".join(verdict.reasons)
        else:
            posture = OK
            detail = verdict.summary()

        events.extend(self._early_warnings(verdict, eq))

        if posture != self.posture:
            # A breach is terminal and must be announced exactly once even if
            # posture later churns.
            if posture == BREACHED and self._breach_emitted:
                pass
            else:
                events.append(MonitorEvent(
                    kind=posture, detail=detail,
                    equity=self.balance if eq is None else eq,
                    posture=posture, verdict=verdict))
            if posture == BREACHED:
                self._breach_emitted = True
            self.posture = posture
        return events

    def _early_warnings(self, verdict: fr.RuleVerdict,
                        eq: float | None) -> list[MonitorEvent]:
        """Speak up at 50% and 75% of the daily budget, once each per day.

        Edge-triggered on the LEVEL, not on the posture, because the whole
        point is to say something while the posture is still OK. Returns
        events the caller texts; it changes no state the executor reads.
        """
        soft = verdict.daily_soft
        if soft <= 0:
            return []
        used = verdict.daily_loss_used
        out = []

        # An early warning is only early if it arrives while everything is
        # still fine. Once the engine has already blocked or breached, the
        # posture event says so with more authority and this would contradict
        # it — the first live run of ftmo_watch started mid-breach and emitted
        # "has passed 50% ... nothing is blocked yet" alongside BREACHED, with
        # "-44.78 left". The levels are still marked spent so they cannot fire
        # later in the same day on the way back down.
        if verdict.breached or not verdict.can_open:
            self._warned.update(EARLY_WARNING_FRACTIONS)
            return []

        for frac in sorted(EARLY_WARNING_FRACTIONS):
            if frac in self._warned or used < soft * frac:
                continue
            self._warned.add(frac)
            out.append(MonitorEvent(
                kind=WARNING,
                detail=(f"daily loss {used:,.2f} has passed {frac:.0%} of the "
                        f"{soft:,.2f} budget (hard limit {verdict.daily_hard:,.2f}, "
                        f"{verdict.daily_hard - used:,.2f} left) — nothing is "
                        f"blocked yet"),
                equity=self.balance if eq is None else eq,
                posture=self.posture, verdict=verdict))
        return out

    def _roll_if_new_day(self, now: datetime) -> list[MonitorEvent]:
        """Cross the 00:00 CE(S)T boundary if it has passed."""
        today = fr.ftmo_day(now)
        if today == self.day:
            return []
        day_profit = self.balance - self.day_start_balance
        rolled = fr.roll_day(self.config,
                             fr.AccountState(equity=self.balance, balance=self.balance,
                                             day_start_balance=self.day_start_balance,
                                             highest_eod_balance=self.highest_eod_balance,
                                             trading_days=self.trading_days,
                                             daily_profits=self.daily_profits),
                             closing_balance=self.balance,
                             day_profit=day_profit,
                             opened_a_position=self.opened_today)
        self.day_start_balance = rolled.day_start_balance
        self.highest_eod_balance = rolled.highest_eod_balance
        self.trading_days = rolled.trading_days
        self.daily_profits = rolled.daily_profits
        self.opened_today = False
        self.day = today
        # A new daily budget means the old warnings no longer describe
        # anything. Without this reset they would fire once and never again.
        self._warned.clear()
        # A new day resets the daily limit, so a posture set by it is stale.
        self.posture = OK if self.posture in (BLOCKED, FLATTEN) else self.posture
        return [MonitorEvent(
            kind="DAY_ROLLED",
            detail=(f"00:00 CE(S)T — new baseline {self.day_start_balance:,.2f}, "
                    f"day P&L {day_profit:+,.2f}, {self.trading_days} trading day(s)"),
            equity=self.balance, posture=self.posture)]

    # ------------------------------------------------------------- callbacks

    def on_quote(self, symbol_id: int, bid: float, ask: float,
                 now: datetime) -> list[MonitorEvent]:
        if bid <= 0 or ask <= 0:
            raise ValueError(f"non-positive quote for symbol {symbol_id}")
        self.quotes[symbol_id] = Quote(bid=bid, ask=ask, at=now)
        return self._assess(now)

    def on_balance(self, balance: float, now: datetime) -> list[MonitorEvent]:
        self.balance = balance
        return self._assess(now)

    def on_position_opened(self, position: OpenPosition,
                           now: datetime) -> list[MonitorEvent]:
        self.positions[position.position_id] = position
        self._opened_at[position.position_id] = now
        self.opened_today = True
        events = [MonitorEvent(
            kind="POSITION_OPENED",
            detail=(f"{position.symbol_name} {position.side} {position.units:g} @ "
                    f"{position.entry_price:g}, stop {position.stop_price:g} "
                    f"(risks {position.risk_at_stop():,.2f})"),
            equity=self.balance, posture=self.posture)]
        return events + self._assess(now)

    def on_position_closed(self, position_id: int, realized_pnl: float,
                           new_balance: float, now: datetime) -> list[MonitorEvent]:
        position = self.positions.pop(position_id, None)
        self._opened_at.pop(position_id, None)
        self.balance = new_balance
        name = position.symbol_name if position else f"id={position_id}"
        events = [MonitorEvent(
            kind="POSITION_CLOSED",
            detail=f"{name} closed, realised {realized_pnl:+,.2f}, "
                   f"balance {new_balance:,.2f}",
            equity=self.balance, posture=self.posture)]
        return events + self._assess(now)

    def heartbeat(self, now: datetime) -> list[MonitorEvent]:
        """Call on a timer. This is what notices SILENCE.

        Every other entry point is driven by an incoming message, so none of
        them can detect the case where messages simply stop — which is exactly
        the case that matters.
        """
        return self._assess(now)


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline checks against synthetic tick streams. No network, no venue."""
    from zoneinfo import ZoneInfo
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def kinds(events):
        return [e.kind for e in events]

    def postures(events):
        """Posture transitions only. WARNING rides the same stream but is
        advisory and must never be mistaken for a state change — filtering it
        here keeps every posture assertion below saying exactly what it
        means."""
        return [e.kind for e in events if e.kind != WARNING]

    cfg = fr.FTMOConfig(product="2step", phase="challenge", initial_capital=25_000.0)
    T0 = datetime(2026, 8, 3, 12, 0, tzinfo=ZoneInfo("Europe/Prague"))

    def fresh(**kw):
        return EquityMonitor(cfg, balance=25_000.0, now=T0, **kw)

    # 1000 units of a symbol at 100.00, stop 99.75 -> risks 250.
    def pos(pid=1, side="BUY", entry=100.0, stop=99.75, units=1000.0, sym=1):
        return OpenPosition(position_id=pid, symbol_id=sym, symbol_name=f"SYM{sym}",
                            side=side, units=units, entry_price=entry, stop_price=stop)

    print("valuation basics:")
    m = fresh()
    check("flat account is valuable with no quotes", m.equity() == 25_000.0)
    check("flat account is never stale", m.stalest_age_s(T0) == 0.0)
    m.on_position_opened(pos(), T0)
    check("holding a symbol with no quote -> equity UNKNOWN, not balance",
          m.equity() is None)
    check("...and floating P&L is None, deliberately not 0.0", m.floating_pnl() is None)
    m.on_quote(1, 100.0, 100.02, T0)
    check("quote arrives -> equity valuable", m.equity() is not None)

    print("floating P&L marks at the EXIT side of the spread:")
    long_p = pos(side="BUY", entry=100.0)
    check("a long marks at the bid", long_p.mark_price(99.0, 99.02) == 99.0)
    check("a long 1.00 below entry loses 1000",
          abs(long_p.floating_pnl(99.0, 99.02) + 1000.0) < 1e-9)
    short_p = pos(side="SELL", entry=100.0, stop=100.25)
    check("a short marks at the ask", short_p.mark_price(99.0, 99.02) == 99.02)
    check("a short profits when price falls", short_p.floating_pnl(99.0, 99.02) > 0)
    check("marking a long at the mid would flatter it",
          long_p.floating_pnl(99.0, 99.02) < long_p.units * (99.01 - 100.0) + 1e-9)

    print("risk at stop feeds the portfolio budget:")
    check("1000 units with a 0.25 stop risks 250", abs(pos().risk_at_stop() - 250) < 1e-9)
    m = fresh()
    m.on_position_opened(pos(pid=1), T0)
    m.on_position_opened(pos(pid=2, sym=2), T0)
    check("open risk sums across the book", abs(m.open_risk() - 500.0) < 1e-9)

    print("edge-triggered, not level-triggered:")
    m = fresh()
    m.on_position_opened(pos(), T0)
    m.on_quote(1, 100.0, 100.02, T0)
    # Drop 1.05 -> floating -1050, past the 1000 soft limit.
    ev = m.on_quote(1, 98.95, 98.97, T0 + timedelta(seconds=1))
    check("crossing the soft limit emits exactly one POSTURE event",
          len(postures(ev)) == 1)
    check("...and it is BLOCKED", postures(ev) == [BLOCKED])
    # This tick fell straight past 50% and 75% on its way, but it landed
    # BLOCKED. A warning saying "nothing is blocked yet" next to a BLOCKED
    # event is a contradiction, so the warnings are suppressed once the engine
    # has already acted. Found by running ftmo_watch against a breached
    # account, not by these tests.
    check("...and NO early warning contradicts it",
          kinds(ev).count(WARNING) == 0)
    check("...but the levels are marked spent, so they cannot fire on the "
          "way back down", m._warned == {0.50, 0.75})
    quiet = m.on_quote(1, 98.94, 98.96, T0 + timedelta(seconds=2))
    check("staying blocked emits nothing further", quiet == [])
    quiet2 = m.on_quote(1, 98.93, 98.95, T0 + timedelta(seconds=3))
    check("...still nothing on the next tick", quiet2 == [])
    back = m.on_quote(1, 100.0, 100.02, T0 + timedelta(seconds=4))
    check("recovering emits one OK event", kinds(back) == [OK])

    print("early warnings speak while everything is still fine (2026-08-11):")
    m = fresh()
    m.on_position_opened(pos(), T0)
    m.on_quote(1, 100.0, 100.02, T0)
    # Budget is the 1000 soft limit here, so 50% is -500 and 75% is -750.
    ev = m.on_quote(1, 99.60, 99.62, T0 + timedelta(seconds=1))      # -400
    check("under 50% of the budget says nothing", ev == [])
    ev = m.on_quote(1, 99.45, 99.47, T0 + timedelta(seconds=2))      # -550
    check("crossing 50% emits a WARNING", kinds(ev) == [WARNING])
    check("...and the posture is untouched — nothing is blocked",
          m.posture == OK and ev[0].posture == OK)
    check("...and the message says nothing is blocked yet",
          "nothing is blocked yet" in ev[0].detail)
    check("...and it reports how much room is left to the HARD limit",
          "left" in ev[0].detail)
    quiet = m.on_quote(1, 99.40, 99.42, T0 + timedelta(seconds=3))   # -600
    check("the same level does not warn twice", quiet == [])
    ev = m.on_quote(1, 99.15, 99.17, T0 + timedelta(seconds=4))      # -850
    check("crossing 75% emits the second WARNING", kinds(ev) == [WARNING])
    ev = m.on_quote(1, 99.10, 99.12, T0 + timedelta(seconds=5))      # -900
    check("...and that level does not repeat either", ev == [])
    # Recover fully, then fall through both levels again. Within one FTMO day
    # that must stay silent: the levels are armed once per day, not once per
    # crossing, or a position oscillating around 50% would text on every swing.
    m.on_quote(1, 100.0, 100.02, T0 + timedelta(seconds=6))
    again = m.on_quote(1, 99.15, 99.17, T0 + timedelta(seconds=7))
    check("recovering and re-crossing does NOT warn again the same day",
          [e for e in again if e.kind == WARNING] == [])
    check("...because both levels are still armed-and-spent",
          m._warned == {0.50, 0.75})

    print("a new FTMO day re-arms the warnings:")
    m2 = fresh()
    m2.on_position_opened(pos(), T0)
    m2.on_quote(1, 100.0, 100.02, T0)
    m2.on_quote(1, 99.20, 99.22, T0 + timedelta(seconds=1))          # -800, both
    check("both levels fired on day one", m2._warned == {0.50, 0.75})
    tomorrow = T0 + timedelta(days=1)
    m2.on_quote(1, 100.0, 100.02, tomorrow)
    check("the day roll clears them", m2._warned == set())

    print("WARNING is advisory and never drives the executor:")
    check("WARNING is not one of the postures",
          WARNING not in (OK, BLOCKED, UNKNOWN, FLATTEN, BREACHED))
    src = inspect.getsource(EquityMonitor._early_warnings)
    check("_early_warnings assigns no posture",
          "self.posture =" not in src)
    check("...and emits nothing when the budget is zero or negative",
          "if soft <= 0" in src)

    print("the two tiers, in order:")
    m = fresh()
    m.on_position_opened(pos(), T0)
    m.on_quote(1, 100.0, 100.02, T0)
    ev = m.on_quote(1, 98.95, 98.97, T0 + timedelta(seconds=1))     # -1050
    check("past soft (1000) blocks entries", postures(ev) == [BLOCKED])
    ev = m.on_quote(1, 98.80, 98.82, T0 + timedelta(seconds=2))     # -1200
    check("past flatten (1125) escalates to FLATTEN", kinds(ev) == [FLATTEN])
    check("...and it is not yet a breach", m.posture == FLATTEN)
    ev = m.on_quote(1, 98.70, 98.72, T0 + timedelta(seconds=3))     # -1300
    check("past hard (1250) is BREACHED", kinds(ev) == [BREACHED])

    print("breach is announced once:")
    m = fresh()
    m.on_position_opened(pos(), T0)
    m.on_quote(1, 98.70, 98.72, T0)
    first = m.on_quote(1, 98.60, 98.62, T0 + timedelta(seconds=1))
    check("no repeat BREACHED on further ticks", BREACHED not in kinds(first))

    print("stale quotes are UNKNOWN, never safe:")
    m = fresh()
    m.on_position_opened(pos(), T0)
    m.on_quote(1, 100.0, 100.02, T0)
    check("fresh quotes -> OK", m.posture == OK)
    ev = m.heartbeat(T0 + timedelta(seconds=11))
    check("11s of silence blocks entries", kinds(ev) == [UNKNOWN])
    check("...and does NOT flatten yet", m.posture == UNKNOWN)
    ev = m.heartbeat(T0 + timedelta(seconds=61))
    check("61s of silence escalates to FLATTEN", kinds(ev) == [FLATTEN])
    ev = m.on_quote(1, 100.0, 100.02, T0 + timedelta(seconds=62))
    check("a fresh quote recovers to OK", kinds(ev) == [OK])

    print("a freshly-opened position is not instantly stale:")
    # Regression: the execution event that registers a position arrives BEFORE
    # the first spot tick for its symbol. Treating "no quote yet" as infinite
    # age made the monitor demand an immediate flatten of every new position.
    # Found by replaying a synthetic breach day, not by the unit tests above.
    m = fresh()
    ev = m.on_position_opened(pos(), T0)
    check("opening with no quote yet does not flatten",
          FLATTEN not in kinds(ev))
    check("...and does not go UNKNOWN either", m.posture == OK)
    check("...age is measured from the open, not infinity",
          m.stalest_age_s(T0) == 0.0)
    check("grace period still applies normally",
          m.stalest_age_s(T0 + timedelta(seconds=5)) == 5.0)
    ev = m.heartbeat(T0 + timedelta(seconds=11))
    check("a quote that never arrives still goes UNKNOWN", kinds(ev) == [UNKNOWN])
    ev = m.heartbeat(T0 + timedelta(seconds=61))
    check("...and still escalates to FLATTEN", kinds(ev) == [FLATTEN])
    check("infinity renders readably, not as 'infs'",
          _age_s(float("inf")) == "never quoted")

    print("staleness only matters while holding something:")
    m = fresh()
    check("flat and silent for an hour is still OK",
          m.heartbeat(T0 + timedelta(hours=1)) == [])
    check("...posture unchanged", m.posture == OK)

    print("heartbeat is what notices silence:")
    m = fresh()
    m.on_position_opened(pos(), T0)
    m.on_quote(1, 100.0, 100.02, T0)
    check("without a heartbeat nothing detects the gap", m.posture == OK)
    m.heartbeat(T0 + timedelta(seconds=30))
    check("the heartbeat does", m.posture == UNKNOWN)

    print("00:00 CE(S)T rollover:")
    late = datetime(2026, 8, 3, 23, 59, tzinfo=ZoneInfo("Europe/Prague"))
    m = EquityMonitor(cfg, balance=25_000.0, now=late)
    m.on_position_opened(pos(), late)
    m.on_quote(1, 100.0, 100.02, late)
    # 1,100 is past the 1,000 soft limit but inside the 1,125 flatten tier.
    m.on_position_closed(1, realized_pnl=-1_100.0, new_balance=23_900.0, now=late)
    check("an 1,100 loss blocks entries before midnight", m.posture == BLOCKED)
    ev = m.heartbeat(datetime(2026, 8, 4, 0, 1, tzinfo=ZoneInfo("Europe/Prague")))
    check("crossing midnight emits DAY_ROLLED", "DAY_ROLLED" in kinds(ev))
    check("baseline resets to the new balance", m.day_start_balance == 23_900.0)
    check("the daily block clears with the new day", m.posture == OK)
    check("the trading day was counted", m.trading_days == 1)
    check("the day's P&L was recorded", m.daily_profits == (-1_100.0,))
    check("high-water mark did NOT rise on a losing day",
          m.highest_eod_balance == 25_000.0)

    print("the daily limit resets each day; the total drawdown does NOT:")
    # Bleed across three days, each day's loss comfortably inside the daily
    # limit, so nothing but the cumulative drawdown can be what bites. Doing
    # this in a single day would trip the daily hard limit first and prove
    # nothing about the drawdown.
    day = datetime(2026, 8, 3, 23, 59, tzinfo=ZoneInfo("Europe/Prague"))
    m = EquityMonitor(cfg, balance=25_000.0, now=day)
    balance = 25_000.0
    for i, loss in enumerate((900.0, 900.0, 300.0)):
        balance -= loss
        m.on_position_closed(100 + i, realized_pnl=-loss, new_balance=balance, now=day)
        check(f"day {i + 1}: a {loss:,.0f} loss alone does not breach the day",
              m.posture != BREACHED)
        day = day + timedelta(days=1)
        m.heartbeat(day)
    check("three days later the balance is 22,900", abs(balance - 22_900.0) < 1e-9)
    check("each day's own loss stayed inside the 1,250 daily limit",
          m.daily_profits == (-900.0, -900.0, -300.0))
    check("but cumulative drawdown of 2,100 is past the 2,000 soft limit",
          m.posture == BLOCKED)
    check("...and a fresh day does not clear it",
          m.heartbeat(day + timedelta(days=1)).count(None) == 0 and m.posture == BLOCKED)

    print("position lifecycle events:")
    m = fresh()
    ev = m.on_position_opened(pos(), T0)
    check("opening emits POSITION_OPENED", "POSITION_OPENED" in kinds(ev))
    check("...naming the stop and the risk", "risks 250.00" in ev[0].detail)
    ev = m.on_position_closed(1, realized_pnl=125.0, new_balance=25_125.0, now=T0)
    check("closing emits POSITION_CLOSED", "POSITION_CLOSED" in kinds(ev))
    check("...with the realised number", "+125.00" in ev[0].detail)
    check("the book is empty again", len(m.positions) == 0)
    check("closing an unknown id does not raise",
          "POSITION_CLOSED" in kinds(m.on_position_closed(404, 0.0, 25_125.0, T0)))

    print("input validation:")
    check("naive `now` refused",
          _raises(lambda: EquityMonitor(cfg, 25_000.0, datetime(2026, 8, 3, 12, 0))))
    check("negative quote refused",
          _raises(lambda: fresh().on_quote(1, -1.0, 1.0, T0)))
    check("negative units refused", _raises(lambda: pos(units=-5)))
    check("bad side refused", _raises(lambda: pos(side="LONG")))
    check("non-positive FX rate refused",
          _raises(lambda: OpenPosition(1, 1, "X", "BUY", 1.0, 1.0, 0.9, 0.0)))

    print("\nFAILED" if failures else "\nAll FTMO monitor selftests passed.")
    return 1 if failures else 0


def _raises(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="FTMO equity monitor (pure, offline).")
    ap.add_argument("--selftest", action="store_true", help="Run offline checks and exit.")
    ap.parse_args()
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
