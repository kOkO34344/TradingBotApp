#!/usr/bin/env python3
"""
ftmo_session.py — a long-lived, connected cTrader session for the FTMO venue.

`ftmo_service.py` is one-shot: connect, ask, disconnect. That is right for a
probe and useless for trading, which needs a connection that stays up, streams
quotes into `ftmo_monitor`, and can place an order in the middle of all that.
This module is that connection.

THREADING, AND WHY NOT THE ASYNCIO REACTOR. The cTrader SDK is Twisted; the
FastAPI backend and `ib_async` are asyncio. The obvious marriage is Twisted's
asyncio reactor, and `ftmo_service.install_asyncio_reactor()` exists for it.
This module deliberately does NOT use it: it runs the DEFAULT reactor on its
own daemon thread with `installSignalHandlers=False`, and callers hand work in
via `reactor.callFromThread` and wait on a `concurrent.futures.Future`.

That choice buys isolation. A shared asyncio reactor means a slow protobuf
round trip and a slow HTTP request are competing for one event loop, and a
bug in either stalls the other — including stalling the equity monitor, which
is the one thing on this venue that must never stall. It also mirrors what
`api/trader_worker.py` already does for IBKR's synchronous order calls, so the
project has one threading story rather than two.

Consequence to respect: **every public method here blocks the calling thread.**
From FastAPI, call them in a threadpool (`run_in_threadpool`), never directly
in an async handler.

THE STOP IS PART OF THE ORDER, NOT A SECOND LEG. `ProtoOANewOrderReq` carries
`stopLoss` as a field, so the position cannot exist without its stop the way an
IBKR bracket's parent can briefly exist without its child. `place_market()`
REFUSES a missing or wrong-sided stop rather than defaulting one — the same
posture as rule 2, and the reason the GOOGL/AAPL class of incident is
structurally harder here. It still verifies after the fill, because "the field
was set" and "the venue accepted it" are different claims.

Offline selftest:  python3 ftmo_session.py --selftest
"""

from __future__ import annotations

import argparse
import inspect
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path

import ftmo_service as svc

BASE_DIR = Path(__file__).resolve().parent

# cTrader trendbar periods we actually use, by the project's own naming.
PERIODS = {"M1": 1, "M5": 5, "M15": 7, "M30": 8, "H1": 9, "H4": 10, "D1": 12}

# Spot events and trendbars BOTH arrive as integers in a fixed 1/100000 unit.
# The symbol's `digits` describes how the venue DISPLAYS a price; it is not the
# wire scale, and treating it as one is a 1000x error on every symbol whose
# digits is not 5. Named rather than inlined because a mis-scaled price
# silently misprices a stop.
SPOT_SCALE = 100_000.0

# Trendbars use the SAME fixed 1e5 scale as spot events, NOT the symbol's
# `digits`. See scale_price() — assuming digits here produced a 1000x error on
# every symbol whose digits is not 5.
TRENDBAR_SCALE = 100_000.0

# The number of decimals the 1e5 wire scale itself carries. `relativeStopLoss`
# is expressed on that scale, but the venue only accepts a value that also
# lands on the SYMBOL's precision grid — a multiple of 10**(5 - digits).
SPOT_DIGITS = 5


def stop_grid_step(digits: int) -> int:
    """The smallest `relativeStopLoss` increment the venue accepts for `digits`.

    A price of 2 digits moves in steps of 0.01, which is 1,000 units on the
    1e5 wire scale; 3 digits is 100, and 5 digits is 1. Anything finer is
    refused with `INVALID_REQUEST: Relative stop loss has invalid precision`.

    Clamped at 1 because the wire scale cannot express anything smaller, and a
    venue reporting more than 5 digits must not produce a step of zero — that
    would make the quantiser divide by zero on the one path that must not fail.
    """
    return max(1, 10 ** (SPOT_DIGITS - int(digits)))


def quantize_relative_stop(distance: float, digits: int) -> int:
    """Round a stop DISTANCE (in price) down onto the venue's precision grid.

    Returns the integer `relativeStopLoss` in 1/100000 price units.

    Rounds **down**, never to nearest, and the direction is the point. A
    shorter distance means a tighter stop, which can only make the realised
    loss SMALLER than the size the sizer budgeted for. Rounding up would widen
    real risk past the per-trade cap that `ftmo_sizing` just proved the order
    fits inside — a limit quietly exceeded by the transport layer is exactly
    the failure this project keeps writing rules against.

    The adjustment is at most one tick, so it never materially moves the stop.

    Learned live on 2026-08-07: the first four unattended FTMO orders were
    sized correctly and three were refused outright for this, while the fourth
    was accepted only because its ATR happened to land on two decimals. An
    unaligned stop is not rejected *approximately* — the whole order dies.
    """
    raw = int(round(abs(distance) * SPOT_SCALE))
    step = stop_grid_step(digits)
    return (raw // step) * step


def quantize_relative_take_profit(distance: float, digits: int) -> int:
    """Round a take-profit DISTANCE down onto the venue's precision grid.

    `relativeTakeProfit` rides the SAME 1e5 wire scale and the SAME symbol
    precision grid as `relativeStopLoss`, and is refused with the same
    `INVALID_REQUEST: ... invalid precision` error. Delegates rather than
    reimplements so the two can never drift apart — the 2026-08-07 rejections
    happened because a distance that looked correct was not expressible.

    Rounds DOWN, i.e. very slightly CLOSER to entry. The direction matters
    less here than it does for the stop: a target one tick nearer can only
    make the position close sooner and cannot cost money, whereas rounding a
    STOP the wrong way widens real risk past the sizer's cap. Kept the same
    direction anyway so there is one rule to remember, not two.
    """
    return quantize_relative_stop(distance, digits)


class SessionError(RuntimeError):
    """The session is not usable, or the venue refused something."""


@dataclass
class Quote:
    """Last seen top-of-book for one symbol."""
    symbol_id: int
    bid: float | None = None
    ask: float | None = None
    ts: float = 0.0

    def age_s(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.ts

    def exit_side_price(self, side: str) -> float | None:
        """Mark a position at the side it would CLOSE on — long at bid, short
        at ask. Marking at the mid flatters equity by half a spread per
        position, which is the unsafe direction on a limit measured in equity.
        Same rule ftmo_monitor applies; stated here too because this is where
        the numbers originate."""
        return self.bid if side.upper() == "BUY" else self.ask


@dataclass
class Position:
    position_id: int
    symbol_id: int
    symbol: str
    side: str
    volume: int
    entry_price: float
    stop_loss: float | None
    take_profit: float | None

    @property
    def protected(self) -> bool:
        return self.stop_loss is not None and self.stop_loss > 0


def validate_stop(side: str, entry_price: float, stop_price: float) -> None:
    """Refuse a stop that is missing, non-positive, or on the wrong side.

    A wrong-sided stop is not a rounding problem: a long whose stop sits ABOVE
    entry is an order that fills and immediately closes, converting a position
    into a guaranteed loss plus two spreads. Cheap to check, and the check has
    to live somewhere both the runner and any manual path go through.
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise SessionError(f"side must be BUY or SELL, got {side!r}")
    if stop_price is None or stop_price <= 0:
        raise SessionError(
            "refusing an order with no stop — every FTMO entry carries a "
            "server-side stop (rule 2)")
    if entry_price <= 0:
        raise SessionError(f"entry_price must be positive, got {entry_price!r}")
    if side == "BUY" and stop_price >= entry_price:
        raise SessionError(
            f"BUY stop {stop_price} is at or above entry {entry_price} — it "
            f"would close the position on fill")
    if side == "SELL" and stop_price <= entry_price:
        raise SessionError(
            f"SELL stop {stop_price} is at or below entry {entry_price} — it "
            f"would close the position on fill")


def validate_take_profit(side: str, entry_price: float,
                         take_profit_price: float) -> None:
    """Refuse a take-profit that is missing, non-positive, or on the wrong side.

    The mirror of `validate_stop`, and it exists for a sharper reason than
    symmetry. A take-profit is derived from Kronos's predicted return, and a
    top-N candidate can carry a NEGATIVE prediction — the 2026-08-07 21:32
    rebalance entered EURUSD at a predicted -0.15%. Applied naively that
    produces a "target" BELOW entry on a long, which the venue would treat as
    an immediate profitable exit and close at a loss on fill. A wrong-sided
    take-profit is therefore the same class of error as a wrong-sided stop,
    not a cosmetic one.
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise SessionError(f"side must be BUY or SELL, got {side!r}")
    if take_profit_price is None or take_profit_price <= 0:
        raise SessionError("take_profit_price must be a positive price")
    if entry_price <= 0:
        raise SessionError(f"entry_price must be positive, got {entry_price!r}")
    if side == "BUY" and take_profit_price <= entry_price:
        raise SessionError(
            f"BUY take-profit {take_profit_price} is at or below entry "
            f"{entry_price} — it would close the position on fill, at a loss")
    if side == "SELL" and take_profit_price >= entry_price:
        raise SessionError(
            f"SELL take-profit {take_profit_price} is at or above entry "
            f"{entry_price} — it would close the position on fill, at a loss")


def market_open_now(spec: dict, now_utc=None) -> bool | None:
    """Is this symbol tradeable right now, per the venue's own schedule?

    Returns None when the capture has no schedule — UNKNOWN, never False. A
    missing calendar is missing information, and refusing to trade on it would
    be a different bug from refusing to trade because the market is shut.

    WHY THIS IS NEEDED AT ALL: a streaming quote does not mean a tradeable
    market. On 2026-08-05 both US30.cash and BTCUSD were quoting live and both
    rejected an order with MARKET_CLOSED — it was 23:55 Moscow, inside FTMO's
    daily ten-minute maintenance window.

    The schedule's zone is the SYMBOL's (`scheduleTimeZone`, Europe/Moscow on
    this broker) and is NOT the Europe/Prague boundary `ftmo_rules` uses for
    the FTMO day. Two different timezones live in this system; conflating them
    would put the maintenance window an hour or two off.

    cTrader expresses each interval in seconds from the start of the trading
    WEEK, where second 0 is Sunday 00:00 in the schedule's own timezone.
    """
    from datetime import datetime, timezone as _tz
    from zoneinfo import ZoneInfo

    intervals = spec.get("schedule")
    zone = spec.get("schedule_timezone")
    if not intervals or not zone:
        return None
    try:
        tz = ZoneInfo(zone)
    except Exception:
        return None
    now = (now_utc or datetime.now(_tz.utc)).astimezone(tz)
    # isoweekday(): Mon=1..Sun=7. cTrader's week starts Sunday at second 0.
    week_second = (now.isoweekday() % 7) * 86400 + \
        now.hour * 3600 + now.minute * 60 + now.second
    return any(iv["start"] <= week_second < iv["end"] for iv in intervals)


def scale_price(raw: int) -> float:
    """Trendbar prices are ints scaled by 10^5 — ALWAYS, whatever `digits` says.

    This cost a real bug on 2026-08-05. The obvious reading is that a price is
    scaled by the symbol's own `digits`, and it is wrong: cTrader sends
    trendbars in a fixed 1/100000 unit for every instrument. EURUSD hides it
    perfectly, because EURUSD's digits IS 5 — and EURUSD is the symbol anyone
    smoke-tests first.

    Everything else breaks loudly once you look: XAUUSD (digits 2) priced at
    4,076,760 against a live bid of 4,248, BTCUSD at 64,285,760. Downstream
    that produced an ATR of 1.49 MILLION on BTC and a NEGATIVE stop price on
    NATGAS.cash, and the sizer accepted it and proposed an order.

    The lesson generalises past this function: a per-symbol scale that happens
    to be right for your test symbol is indistinguishable from a global one
    until you try a second symbol with different digits.
    """
    return raw / TRENDBAR_SCALE


class FTMOSession:
    """A connected cTrader session. Start it once, use it from any thread."""

    def __init__(self, env: dict | None = None, specs: dict | None = None):
        self.env = env or svc.load_env()
        self.specs = specs if specs is not None else _load_specs_quietly()
        self.account_id: int | None = None
        self.quotes: dict[int, Quote] = {}
        self.positions_cache: dict[int, Position] = {}
        self._client = None
        self._reactor = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: Exception | None = None
        self._start_traceback: str | None = None
        self._by_id = {v["symbol_id"]: k for k, v in self.specs.items()}
        self._lock = threading.Lock()
        self._exec_listeners: list = []

    # ------------------------------------------------------------- lifecycle

    def start(self, timeout_s: float = 45.0) -> None:
        """Connect, authenticate application and account. Blocks until ready."""
        if self._thread is not None:
            raise SessionError("session already started")
        self._thread = threading.Thread(target=self._run_reactor, daemon=True,
                                        name="ftmo-session")
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise SessionError(
                f"FTMO session did not become ready within {timeout_s}s")
        if self._start_error:
            raise SessionError(
                f"FTMO session failed to start: "
                f"{type(self._start_error).__name__}: {self._start_error}\n"
                f"{self._start_traceback or ''}")

    def _run_reactor(self) -> None:
        from twisted.internet import reactor
        from ctrader_open_api import Client, TcpProtocol
        self._reactor = reactor
        host = svc.host_for(self.env)
        self._client = Client(host, 5035, TcpProtocol)
        self._client.setConnectedCallback(lambda c: self._on_connected(c))
        self._client.setDisconnectedCallback(lambda c, r: self._on_disconnected(r))
        self._client.setMessageReceivedCallback(lambda c, m: self._on_message(m))
        self._client.startService()
        # installSignalHandlers=False is REQUIRED off the main thread; Twisted
        # otherwise tries to install SIGINT handlers and raises.
        reactor.run(installSignalHandlers=False)

    def _on_connected(self, client):
        """Authenticate, retrying a few times before giving up.

        The retry is NOT belt-and-braces. The very first live start of this
        module failed with a bare `(5, 'Deferred')` out of the SDK and then
        succeeded three times in a row on identical code, i.e. the handshake
        is occasionally flaky right after connect. On an unattended venue a
        transient failure that kills the session is a day of not trading and
        not monitoring, so it is retried and only then reported.

        Note it retries the AUTH SEQUENCE, not the reactor: Twisted reactors
        are not restartable, so `reactor.run()` after a `stop()` raises
        `ReactorNotRestartable`. Anything that wants a fresh reactor needs a
        fresh process.
        """
        from twisted.internet import defer, task, reactor
        from ctrader_open_api import Protobuf

        @defer.inlineCallbacks
        def auth(attempt=1, max_attempts=3):
            try:
                cid, secret, token = svc.require(
                    self.env, "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET",
                    "CTRADER_ACCESS_TOKEN")
                res = yield client.send("ProtoOAApplicationAuthReq",
                                        clientId=cid, clientSecret=secret)
                svc._raise_if_error(Protobuf.extract(res))
                res = yield client.send("ProtoOAGetAccountListByAccessTokenReq",
                                        accessToken=token)
                payload = Protobuf.extract(res)
                svc._raise_if_error(payload)
                target, _ = svc.select_account(
                    list(payload.ctidTraderAccount),
                    (self.env.get("CTRADER_ACCOUNT_ID") or "").strip(),
                    svc.host_choice(self.env))
                res = yield client.send("ProtoOAAccountAuthReq",
                                        ctidTraderAccountId=target,
                                        accessToken=token)
                svc._raise_if_error(Protobuf.extract(res))
                self.account_id = target
                self._start_error = None
                self._start_traceback = None
                self._ready.set()
            except Exception as e:
                # A routing or credential error will fail identically every
                # time, so retrying it just delays a clear message by 4s.
                fatal = isinstance(e, svc.FTMOError) and (
                    "CTRADER_HOST" in str(e) or "not among the accounts" in str(e))
                if attempt < max_attempts and not fatal:
                    yield task.deferLater(reactor, 2.0, lambda: None)
                    yield auth(attempt + 1, max_attempts)
                    return
                # Keep the traceback. A bare repr here produced
                # "(5, 'Deferred')", which names neither the failing call nor
                # the reason, and a connection failure at 03:00 is exactly
                # when the traceback matters.
                import traceback
                self._start_error = e
                self._start_traceback = traceback.format_exc()
                self._attempts_made = attempt
                self._ready.set()

        auth()

    def _on_disconnected(self, reason):
        # Deliberately does NOT clear self.quotes. A dropped connection means
        # the quotes are STALE, and staleness is what ftmo_monitor is built to
        # detect via age. Zeroing them here would look like fresh zeros.
        self._ready.clear()

    def _on_message(self, message):
        from ctrader_open_api import Protobuf
        try:
            payload = Protobuf.extract(message)
        except Exception:
            return
        name = type(payload).__name__
        if name == "ProtoOASpotEvent":
            self._on_spot(payload)
        elif name == "ProtoOAExecutionEvent":
            for fn in list(self._exec_listeners):
                try:
                    fn(payload)
                except Exception:
                    pass

    def _on_spot(self, ev):
        sid = ev.symbolId
        with self._lock:
            q = self.quotes.get(sid) or Quote(symbol_id=sid)
            if ev.HasField("bid"):
                q.bid = ev.bid / SPOT_SCALE
            if ev.HasField("ask"):
                q.ask = ev.ask / SPOT_SCALE
            q.ts = time.time()
            self.quotes[sid] = q

    def stop(self) -> None:
        if self._reactor is not None:
            try:
                self._reactor.callFromThread(self._reactor.stop)
            except Exception:
                pass
        self._ready.clear()

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._start_error is None

    # --------------------------------------------------------- request plumbing

    def _send(self, name: str, timeout_s: float = 20.0, **kwargs):
        """Send one request from any thread and block for its response."""
        if not self.ready:
            raise SessionError("session is not connected")
        from ctrader_open_api import Protobuf
        fut: Future = Future()

        def go():
            try:
                d = self._client.send(name, ctidTraderAccountId=self.account_id,
                                      **kwargs)
                d.addCallbacks(
                    lambda res: fut.set_result(Protobuf.extract(res)),
                    lambda err: fut.set_exception(
                        SessionError(f"{name} failed: {err.getErrorMessage()}")))
            except Exception as e:
                fut.set_exception(e)

        self._reactor.callFromThread(go)
        payload = fut.result(timeout=timeout_s)
        svc._raise_if_error(payload)
        return payload

    # ------------------------------------------------------------------ reads

    def account(self) -> dict:
        t = self._send("ProtoOATraderReq").trader
        digits = getattr(t, "moneyDigits", 2) or 2
        scale = float(10 ** digits)
        return {"balance": t.balance / scale,
                "leverage": getattr(t, "leverageInCents", 0) / 100,
                "account_id": self.account_id}

    def refresh_positions(self) -> list[Position]:
        payload = self._send("ProtoOAReconcileReq")
        out: dict[int, Position] = {}
        for p in payload.position:
            td = p.tradeData
            name = self._by_id.get(td.symbolId, str(td.symbolId))
            digits = self.specs.get(name, {}).get("digits", 5)
            side = "BUY" if td.tradeSide == 1 else "SELL"
            sl = getattr(p, "stopLoss", 0) or None
            tp = getattr(p, "takeProfit", 0) or None
            out[p.positionId] = Position(
                position_id=p.positionId, symbol_id=td.symbolId, symbol=name,
                side=side, volume=td.volume,
                entry_price=getattr(p, "price", 0.0),
                stop_loss=sl, take_profit=tp)
        with self._lock:
            self.positions_cache = out
        return list(out.values())

    def unprotected_positions(self) -> list[Position]:
        """Open positions with no server-side stop. Should always be empty.

        Exists because "we always attach a stop" is a claim about our code, and
        this is the check against the venue's own view of reality. The IBKR side
        of this project learned that distinction expensively.
        """
        return [p for p in self.refresh_positions() if not p.protected]

    def untargeted_positions(self) -> list[Position]:
        """Open positions with no server-side take-profit.

        Separate from `unprotected_positions` on purpose, and the two must not
        be merged into one "incomplete position" check. A missing STOP is a
        rule-2 breach and an open-ended loss; a missing TARGET costs upside on
        a position that is still fully protected. Reporting them through one
        channel would either cry wolf about targets or, far worse, let a naked
        stop hide inside a routine-looking warning.
        """
        return [p for p in self.refresh_positions()
                if not (p.take_profit is not None and p.take_profit > 0)]

    def trendbars(self, symbol: str, period: str = "D1",
                  count: int = 500) -> list[dict]:
        """OHLCV history for one symbol, oldest first.

        This is the bar source for Kronos on this venue. yfinance cannot serve
        it: FTMO's instruments are CFDs with names like `US30.cash` that have
        no yfinance ticker, and the whole point is to forecast the series we
        actually trade rather than a proxy of it.
        """
        if period not in PERIODS:
            raise SessionError(f"unknown period {period!r}; "
                               f"known: {', '.join(PERIODS)}")
        spec = self.specs.get(symbol)
        if spec is None:
            raise SessionError(f"{symbol!r} is not in the symbol capture")
        now_ms = int(time.time() * 1000)
        span = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
                "H1": 3600, "H4": 14400, "D1": 86400}[period]
        # Ask for generous slack: weekends and holidays mean elapsed wall time
        # is always more than bar count x period.
        from_ms = now_ms - int(count * span * 1000 * 2.2)
        payload = self._send("ProtoOAGetTrendbarsReq", symbolId=spec["symbol_id"],
                             period=PERIODS[period], fromTimestamp=from_ms,
                             toTimestamp=now_ms, count=count)
        bars = []
        for b in payload.trendbar:
            low = b.low
            bars.append({
                "ts": b.utcTimestampInMinutes * 60,
                "open": scale_price(low + b.deltaOpen),
                "high": scale_price(low + b.deltaHigh),
                "low": scale_price(low),
                "close": scale_price(low + b.deltaClose),
                "volume": b.volume,
            })
        bars.sort(key=lambda r: r["ts"])
        return bars

    def assert_bars_match_quote(self, symbol: str, tolerance: float = 0.25) -> None:
        """Cross-check the bar series against the live quote, and RAISE on a
        mismatch rather than trading on a mis-scaled price.

        Directly modelled on `paper_trader.get_net_liquidation_usd`, which
        verifies IBKR's FX direction against an independent quote and raises,
        because an inverted rate misstated equity by ~29% and would have
        mis-sized every order. The failure here is the same shape and worse:
        the 10^digits trendbar bug priced gold at 4,076,760 and produced a
        negative stop, and NOTHING downstream noticed — the sizer accepted it
        and proposed a live order.

        A daily bar legitimately differs from the current tick, so the
        tolerance is deliberately loose. It is not checking accuracy; it is
        checking for an order-of-magnitude scaling error, which is the failure
        that actually happens.
        """
        q = self.quote(symbol)
        if q is None or not (q.bid or q.ask):
            return  # no tick yet; nothing to compare against, so assert nothing
        bars = self.trendbars(symbol, "D1", 3)
        if not bars:
            return
        close = bars[-1]["close"]
        live = q.bid or q.ask
        if close <= 0 or live <= 0:
            raise SessionError(
                f"{symbol}: non-positive price (bar {close}, live {live})")
        ratio = close / live
        if not (1 - tolerance) <= ratio <= (1 + tolerance):
            raise SessionError(
                f"{symbol}: bar close {close:,.5f} and live quote {live:,.5f} "
                f"differ by {ratio:.1f}x — this is a PRICE SCALING error, not "
                f"a market move. Do not trade on it.")

    def subscribe(self, symbols: list[str]) -> None:
        ids = []
        for s in symbols:
            spec = self.specs.get(s)
            if spec is None:
                raise SessionError(f"{s!r} is not in the symbol capture")
            ids.append(spec["symbol_id"])
        if ids:
            self._send("ProtoOASubscribeSpotsReq", symbolId=ids)

    def quote(self, symbol: str) -> Quote | None:
        spec = self.specs.get(symbol)
        if spec is None:
            return None
        with self._lock:
            return self.quotes.get(spec["symbol_id"])

    # ----------------------------------------------------------------- writes

    def place_market(self, symbol: str, side: str, volume: int,
                     stop_price: float, reference_price: float,
                     take_profit_price: float | None = None,
                     label: str = "kronos", timeout_s: float = 30.0) -> dict:
        """Market order with a SERVER-SIDE stop attached in the same request.

        `reference_price` is only used to validate the stop's and target's
        side — it is not sent, and it does not price the order. Pass the
        current quote.

        Refuses rather than defaults on a missing or wrong-sided stop. The
        stop travels as a field on the order itself, so unlike an IBKR bracket
        there is no window in which the position exists unprotected.

        `take_profit_price` is optional HERE and required by the path that
        decides positions (`ftmo_signal.plan_orders` will not emit an entry
        without one). The asymmetry is deliberate: a stop is a rule-2 invariant
        that no caller may skip, whereas a target is a strategy choice, and
        `ftmo_smoke_order.py` deliberately places the smallest possible probe
        trade with nothing else attached to it.
        """
        validate_stop(side, reference_price, stop_price)
        if take_profit_price is not None:
            validate_take_profit(side, reference_price, take_profit_price)
        spec = self.specs.get(symbol)
        if spec is None:
            raise SessionError(f"{symbol!r} is not in the symbol capture")
        if volume <= 0:
            raise SessionError(f"volume must be positive, got {volume}")
        if volume < spec["min_volume"]:
            raise SessionError(
                f"volume {volume} is below {symbol}'s minimum "
                f"{spec['min_volume']} — the venue would reject it")
        if (volume - spec["min_volume"]) % spec["step_volume"] != 0:
            raise SessionError(
                f"volume {volume} is off {symbol}'s step grid "
                f"({spec['step_volume']}) — size it with ftmo_sizing")
        if spec.get("trading_mode") not in (None, "ENABLED"):
            raise SessionError(f"{symbol} is not tradeable right now "
                               f"(tradingMode={spec.get('trading_mode')})")

        # A MARKET order must carry its stop as `relativeStopLoss`, not as an
        # absolute `stopLoss`. The venue is explicit about it:
        #   INVALID_REQUEST: SL/TP in absolute values are allowed only for
        #   order types: [LIMIT, STOP, STOP_LIMIT]
        # Found on the first real order, 2026-08-05. The stop is still a field
        # on the same request, so it remains atomic with the entry — only the
        # encoding differs. relativeStopLoss is a positive DISTANCE in
        # 1/100000 price units; the venue applies it on the correct side from
        # tradeSide, which is why validate_stop() above still has to check the
        # side we were given rather than trusting the number alone.
        # ...and it must land on the symbol's own precision grid, not merely on
        # the 1e5 wire scale. See quantize_relative_stop().
        distance = abs(reference_price - stop_price)
        relative = quantize_relative_stop(distance, spec["digits"])
        if relative <= 0:
            step = stop_grid_step(spec["digits"])
            raise SessionError(
                f"stop distance {distance!r} is smaller than one "
                f"{symbol} tick ({step / SPOT_SCALE:g}) — refusing an order "
                f"whose stop would collapse onto its entry")

        # The target rides the same request, on the same grid, for the same
        # reason: a MARKET order cannot carry an absolute takeProfit either.
        extra = {}
        relative_tp = None
        if take_profit_price is not None:
            tp_distance = abs(take_profit_price - reference_price)
            relative_tp = quantize_relative_take_profit(tp_distance,
                                                        spec["digits"])
            if relative_tp <= 0:
                step = stop_grid_step(spec["digits"])
                raise SessionError(
                    f"take-profit distance {tp_distance!r} is smaller than one "
                    f"{symbol} tick ({step / SPOT_SCALE:g}) — refusing rather "
                    f"than sending an order whose target sits on its entry")
            extra["relativeTakeProfit"] = relative_tp

        payload = self._send(
            "ProtoOANewOrderReq", timeout_s=timeout_s,
            symbolId=spec["symbol_id"], orderType=1,
            tradeSide=1 if side.upper() == "BUY" else 2,
            volume=volume, relativeStopLoss=relative, label=label,
            comment=f"ftmo/{label}", **extra)
        return {"sent": True, "symbol": symbol, "side": side.upper(),
                "volume": volume, "stop_loss": stop_price,
                "relative_stop_loss": relative,
                "take_profit": take_profit_price,
                "relative_take_profit": relative_tp,
                "response": type(payload).__name__}

    def close_position(self, position_id: int, volume: int) -> dict:
        """Close (or part-close) a position. Never gated by an exposure limit.

        Deliberately has no risk check in front of it. This project has already
        documented what happens when a limit blocks an exit: the then-$5,000
        IBKR notional cap made two winners un-exitable. A limit caps NEW
        exposure; blocking a close raises risk.
        """
        payload = self._send("ProtoOAClosePositionReq",
                             positionId=position_id, volume=volume)
        return {"closed": True, "position_id": position_id, "volume": volume,
                "response": type(payload).__name__}

    def amend_stop(self, position_id: int, stop_price: float,
                   take_profit_price: float | None = None) -> dict:
        """Move a position's stop, and optionally its target.

        **`ProtoOAAmendPositionSLTPReq` sets the whole SL/TP pair.** Sending it
        with only `stopLoss` populated is how you would silently CLEAR a target
        that is already attached — which matters as of 2026-08-08, when every
        entry started carrying one. `take_profit_price` therefore defaults to
        None meaning "leave it alone", and the current value is read back from
        the venue and re-sent rather than omitted.

        Nothing calls this yet. It is written this way now because the failure
        would be invisible: the amend would succeed, the stop would be correct,
        and the target would just be gone.
        """
        if stop_price is None or stop_price <= 0:
            raise SessionError("amend_stop requires a positive stop price")
        keep_tp = take_profit_price
        if keep_tp is None:
            existing = next((p for p in self.refresh_positions()
                             if p.position_id == position_id), None)
            if existing is not None and existing.take_profit:
                keep_tp = existing.take_profit
        fields = {"positionId": position_id, "stopLoss": stop_price}
        if keep_tp:
            fields["takeProfit"] = keep_tp
        payload = self._send("ProtoOAAmendPositionSLTPReq", **fields)
        return {"amended": True, "position_id": position_id,
                "stop_loss": stop_price, "take_profit": keep_tp,
                "response": type(payload).__name__}

    def on_execution(self, fn) -> None:
        """Register a callback for ProtoOAExecutionEvent. Fires on the reactor
        thread, so the callback must be quick and must not block."""
        self._exec_listeners.append(fn)


def _load_specs_quietly() -> dict:
    try:
        return svc.load_symbol_specs()
    except FileNotFoundError:
        return {}


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline. No network, no credentials, no account touched."""
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def raises(fn, needle=""):
        try:
            fn()
        except Exception as e:
            return needle.lower() in str(e).lower()
        return False

    print("stop validation (the rule 2 analogue):")
    check("a missing stop is refused",
          raises(lambda: validate_stop("BUY", 100.0, 0), "no stop"))
    check("a None stop is refused",
          raises(lambda: validate_stop("BUY", 100.0, None), "no stop"))
    check("a negative stop is refused",
          raises(lambda: validate_stop("BUY", 100.0, -5), "no stop"))
    check("BUY stop above entry refused (would close on fill)",
          raises(lambda: validate_stop("BUY", 100.0, 101.0), "would close"))
    check("BUY stop exactly at entry refused",
          raises(lambda: validate_stop("BUY", 100.0, 100.0), "would close"))
    check("SELL stop below entry refused",
          raises(lambda: validate_stop("SELL", 100.0, 99.0), "would close"))
    check("SELL stop exactly at entry refused",
          raises(lambda: validate_stop("SELL", 100.0, 100.0), "would close"))
    check("a bad side is refused, not guessed",
          raises(lambda: validate_stop("HOLD", 100.0, 90.0), "BUY or SELL"))
    check("a valid long stop passes",
          validate_stop("BUY", 100.0, 98.0) is None)
    check("a valid short stop passes",
          validate_stop("SELL", 100.0, 102.0) is None)

    print("take-profit validation (owner decision 2026-08-08):")
    check("a missing take-profit is refused",
          raises(lambda: validate_take_profit("BUY", 100.0, 0), "positive"))
    check("a None take-profit is refused",
          raises(lambda: validate_take_profit("BUY", 100.0, None), "positive"))
    check("BUY target below entry refused (closes at a LOSS on fill)",
          raises(lambda: validate_take_profit("BUY", 100.0, 99.0), "at a loss"))
    check("BUY target exactly at entry refused",
          raises(lambda: validate_take_profit("BUY", 100.0, 100.0), "at a loss"))
    check("SELL target above entry refused",
          raises(lambda: validate_take_profit("SELL", 100.0, 101.0), "at a loss"))
    check("a bad side is refused, not guessed",
          raises(lambda: validate_take_profit("HOLD", 100.0, 110.0),
                 "BUY or SELL"))
    check("a valid long target passes",
          validate_take_profit("BUY", 100.0, 110.0) is None)
    check("a valid short target passes",
          validate_take_profit("SELL", 100.0, 90.0) is None)
    # The live case this guard exists for: EURUSD was entered at a predicted
    # -0.15% on 2026-08-07, which as a target is BELOW entry on a long.
    check("the EURUSD -0.15% shape is caught (the reason this check exists)",
          raises(lambda: validate_take_profit("BUY", 1.16000,
                                              1.16000 * (1 - 0.0015)),
                 "at a loss"))

    print("relativeTakeProfit rides the SAME grid as the stop:")
    check("a target distance quantises exactly as a stop distance does",
          all(quantize_relative_take_profit(d, dg)
              == quantize_relative_stop(d, dg)
              for d, dg in [(4.50286, 2), (0.18629, 3), (118.32, 2),
                            (0.00552, 5)]))
    check("every quantised target lands on its symbol's grid",
          all(quantize_relative_take_profit(d, dg) % stop_grid_step(dg) == 0
              for d, dg in [(279.56, 2), (0.18629, 3), (1.0 / 7, 5)]))
    check("a sub-tick target collapses to 0 so the caller must refuse it",
          quantize_relative_take_profit(0.004, 2) == 0)
    check("place_market refuses a sub-tick target rather than sending it bare",
          "take-profit distance" in inspect.getsource(FTMOSession.place_market))
    check("place_market validates the target's side before sending",
          "validate_take_profit" in inspect.getsource(FTMOSession.place_market))
    check("stops and targets are reported through SEPARATE read-backs",
          FTMOSession.unprotected_positions is not
          FTMOSession.untargeted_positions)

    print("price scaling is FIXED at 1e5, not per-symbol digits:")
    check("a 5-digit FX price", abs(scale_price(115_512) - 1.15512) < 1e-9)
    check("a 2-digit metal price scales the same way",
          abs(scale_price(424_588_000) - 4245.88) < 1e-6)
    check("a 2-digit crypto price scales the same way",
          abs(scale_price(6_495_040_000) - 64950.40) < 1e-4)
    check("scale_price takes no digits argument at all — the whole bug was "
          "believing it should",
          scale_price.__code__.co_argcount == 1)

    print("relativeStopLoss lands on the SYMBOL's grid, not just the 1e5 wire:")
    check("5 digits steps by 1 (the wire scale itself)", stop_grid_step(5) == 1)
    check("3 digits steps by 100", stop_grid_step(3) == 100)
    check("2 digits steps by 1000", stop_grid_step(2) == 1000)
    check("digits beyond the wire scale clamp to 1, never 0",
          stop_grid_step(7) == 1)
    check("a 5-digit FX distance is already aligned and unchanged",
          quantize_relative_stop(0.00552, 5) == 552)
    # The three orders the venue actually refused on 2026-08-07, and the one
    # it accepted. Regression, not illustration.
    check("SOLUSD 4.50286 @2dp quantises onto the grid (was rejected raw)",
          quantize_relative_stop(4.50286, 2) == 450_000)
    check("NATGAS 0.18629 @3dp quantises onto the grid (was rejected raw)",
          quantize_relative_stop(0.18629, 3) == 18_600)
    check("LTCUSD 2.53571 @2dp quantises onto the grid (was rejected raw)",
          quantize_relative_stop(2.53571, 2) == 253_000)
    check("ETHUSD 118.32 @2dp was already aligned — accepted, and still is",
          quantize_relative_stop(118.32, 2) == 11_832_000)
    check("every quantised distance is a multiple of its own step",
          all(quantize_relative_stop(d, dg) % stop_grid_step(dg) == 0
              for d, dg in [(4.50286, 2), (0.18629, 3), (2.53571, 2),
                            (118.32, 2), (0.00552, 5), (1.0 / 3, 4)]))
    check("quantising never widens the distance, so real risk cannot exceed "
          "the sized budget",
          all(quantize_relative_stop(d, dg) <= round(d * SPOT_SCALE)
              for d, dg in [(4.50286, 2), (0.18629, 3), (2.53571, 2),
                            (0.00552, 5), (1.0 / 3, 4), (9.99999, 2)]))
    check("a distance below one tick floors to zero rather than rounding up "
          "to a stop the caller never asked for",
          quantize_relative_stop(0.004, 2) == 0)
    check("a sub-tick stop is refused by place_market_order, not sent naked",
          "collapse onto its entry" in inspect.getsource(
              FTMOSession.place_market))
    check("the order path quantises rather than calling round() itself",
          "quantize_relative_stop" in inspect.getsource(
              FTMOSession.place_market))

    print("quote marks at the exit side of the spread:")
    q = Quote(symbol_id=1, bid=1.0840, ask=1.0842, ts=time.time())
    check("a long marks at the bid", q.exit_side_price("BUY") == 1.0840)
    check("a short marks at the ask", q.exit_side_price("SELL") == 1.0842)
    old = Quote(symbol_id=1, bid=1.0, ask=1.1, ts=time.time() - 30)
    check("age is reported in seconds", 29 < old.age_s() < 31)

    print("position protection:")
    p = Position(1, 1, "EURUSD", "BUY", 100, 1.08, None, None)
    check("no stop -> unprotected", not p.protected)
    check("zero stop -> unprotected",
          not Position(1, 1, "EURUSD", "BUY", 100, 1.08, 0, None).protected)
    check("a real stop -> protected",
          Position(1, 1, "EURUSD", "BUY", 100, 1.08, 1.07, None).protected)

    print("order guards (no connection needed — they fire before send):")
    specs = {"EURUSD": {"symbol_id": 1, "digits": 5, "min_volume": 100_000,
                        "step_volume": 100_000, "max_volume": 10 ** 10,
                        "trading_mode": "ENABLED"},
             "HALTED": {"symbol_id": 2, "digits": 2, "min_volume": 100,
                        "step_volume": 100, "max_volume": 10 ** 6,
                        "trading_mode": "CLOSE_ONLY_MODE"}}
    s = FTMOSession(env={"CTRADER_HOST": "demo"}, specs=specs)
    check("an unknown symbol is refused",
          raises(lambda: s.place_market("NOPE", "BUY", 100_000, 1.07, 1.08),
                 "not in the symbol capture"))
    check("a stopless order is refused before any send",
          raises(lambda: s.place_market("EURUSD", "BUY", 100_000, 0, 1.08),
                 "no stop"))
    check("a wrong-sided stop is refused before any send",
          raises(lambda: s.place_market("EURUSD", "BUY", 100_000, 1.09, 1.08),
                 "would close"))
    check("sub-minimum volume is refused",
          raises(lambda: s.place_market("EURUSD", "BUY", 50_000, 1.07, 1.08),
                 "below"))
    check("off-grid volume is refused",
          raises(lambda: s.place_market("EURUSD", "BUY", 150_000, 1.07, 1.08),
                 "step grid"))
    check("zero volume is refused",
          raises(lambda: s.place_market("EURUSD", "BUY", 0, 1.07, 1.08),
                 "positive"))
    check("a non-tradeable symbol is refused",
          raises(lambda: s.place_market("HALTED", "BUY", 100, 90.0, 100.0),
                 "not tradeable"))
    check("a valid order gets past every guard and fails only on connection",
          raises(lambda: s.place_market("EURUSD", "BUY", 100_000, 1.07, 1.08),
                 "not connected"))
    check("amend_stop refuses a non-positive stop",
          raises(lambda: s.amend_stop(1, 0), "positive"))
    check("amend_stop preserves an existing target instead of clearing it",
          "takeProfit" in inspect.getsource(FTMOSession.amend_stop)
          and "existing.take_profit" in inspect.getsource(
              FTMOSession.amend_stop))

    print("disconnect does not fabricate fresh data:")
    s2 = FTMOSession(env={"CTRADER_HOST": "demo"}, specs=specs)
    s2.quotes[1] = Quote(symbol_id=1, bid=1.0, ask=1.1, ts=time.time() - 5)
    s2._on_disconnected(None)
    check("quotes survive a disconnect so staleness is detectable",
          1 in s2.quotes)
    check("...and the session reports itself not ready", not s2.ready)

    print("market_open_now (a live quote does NOT mean a tradeable market):")
    from datetime import datetime, timezone as _tz
    # BTCUSD on this broker: Mon-Sat 00:05 -> 23:55 Europe/Moscow. cTrader
    # counts seconds from Sunday 00:00, so Wednesday is day index 3.
    wed = {"schedule_timezone": "Europe/Moscow",
           "schedule": [{"start": 3 * 86400 + 5 * 60,
                         "end": 3 * 86400 + 23 * 3600 + 55 * 60}]}
    def moscow(y, mo, d, h, mi):
        from zoneinfo import ZoneInfo
        return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(_tz.utc)
    check("open in the middle of the session",
          market_open_now(wed, moscow(2026, 8, 5, 12, 0)) is True)
    check("CLOSED inside the 23:55-00:05 maintenance window — the exact case "
          "that rejected a live order on 2026-08-05",
          market_open_now(wed, moscow(2026, 8, 5, 23, 57)) is False)
    check("closed just before the open", 
          market_open_now(wed, moscow(2026, 8, 5, 0, 1)) is False)
    check("open right at the start boundary",
          market_open_now(wed, moscow(2026, 8, 5, 0, 5)) is True)
    check("closed on a day with no interval (Thursday)",
          market_open_now(wed, moscow(2026, 8, 6, 12, 0)) is False)
    check("a missing schedule is UNKNOWN, never False",
          market_open_now({"schedule": [], "schedule_timezone": ""}) is None)
    check("an unparseable timezone is UNKNOWN, never False",
          market_open_now({"schedule": [{"start": 0, "end": 10}],
                           "schedule_timezone": "Mars/Olympus"}) is None)

    print("period names:")
    check("D1 maps to cTrader's 12", PERIODS["D1"] == 12)
    check("an unknown period is refused",
          raises(lambda: s.trendbars("EURUSD", "D7"), "unknown period"))

    print("\nFAILED" if failures else
          "\nAll ftmo_session offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Long-lived FTMO cTrader session.")
    ap.add_argument("--selftest", action="store_true",
                    help="Offline checks; no network, no credentials.")
    ap.add_argument("--smoke", action="store_true",
                    help="Connect read-only, subscribe to a few symbols, "
                         "print quotes and positions. Places NOTHING.")
    args = ap.parse_args()
    if args.smoke:
        return smoke()
    return selftest()


def smoke() -> int:
    """Read-only live check that the session actually stays up. Places nothing."""
    s = FTMOSession()
    print("connecting...")
    s.start()
    print(f"ready, account {s.account_id}")
    print("account:", s.account())
    positions = s.refresh_positions()
    print(f"positions: {len(positions)}")
    for p in positions:
        print(f"  {p.symbol} {p.side} vol={p.volume} "
              f"stop={p.stop_loss or 'NONE'} protected={p.protected}")
    picks = [n for n in ("EURUSD", "XAUUSD", "US30.cash") if n in s.specs]
    if picks:
        s.subscribe(picks)
        print(f"subscribed {picks}; waiting 6s for ticks")
        time.sleep(6)
        for n in picks:
            q = s.quote(n)
            print(f"  {n:12} bid={q.bid if q else None} ask={q.ask if q else None}"
                  f" age={q.age_s():.1f}s" if q else f"  {n:12} no tick yet")
        bars = s.trendbars(picks[0], "D1", 10)
        print(f"trendbars {picks[0]}: {len(bars)} bars, last close "
              f"{bars[-1]['close'] if bars else 'n/a'}")
    s.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
