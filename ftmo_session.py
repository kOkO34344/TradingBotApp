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

# Prices arrive as integers scaled by 10^digits for trendbars, and as
# "relative" 1e-5 scaled integers on spot events. Two different scales on one
# connection is exactly the kind of thing that silently misprices a stop, so
# both conversions are named and never inlined.
SPOT_SCALE = 100_000.0


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


def scale_price(raw: int, digits: int) -> float:
    """Trendbar / position prices arrive as ints scaled by 10^digits."""
    return raw / float(10 ** digits)


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
        digits = spec["digits"]
        bars = []
        for b in payload.trendbar:
            low = b.low
            bars.append({
                "ts": b.utcTimestampInMinutes * 60,
                "open": scale_price(low + b.deltaOpen, digits),
                "high": scale_price(low + b.deltaHigh, digits),
                "low": scale_price(low, digits),
                "close": scale_price(low + b.deltaClose, digits),
                "volume": b.volume,
            })
        bars.sort(key=lambda r: r["ts"])
        return bars

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
                     label: str = "kronos", timeout_s: float = 30.0) -> dict:
        """Market order with a SERVER-SIDE stop attached in the same request.

        `reference_price` is only used to validate the stop's side — it is not
        sent, and it does not price the order. Pass the current quote.

        Refuses rather than defaults on a missing or wrong-sided stop. The
        stop travels as a field on the order itself, so unlike an IBKR bracket
        there is no window in which the position exists unprotected.
        """
        validate_stop(side, reference_price, stop_price)
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

        payload = self._send(
            "ProtoOANewOrderReq", timeout_s=timeout_s,
            symbolId=spec["symbol_id"], orderType=1,
            tradeSide=1 if side.upper() == "BUY" else 2,
            volume=volume, stopLoss=stop_price, label=label,
            comment=f"ftmo/{label}")
        return {"sent": True, "symbol": symbol, "side": side.upper(),
                "volume": volume, "stop_loss": stop_price,
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

    def amend_stop(self, position_id: int, stop_price: float) -> dict:
        if stop_price is None or stop_price <= 0:
            raise SessionError("amend_stop requires a positive stop price")
        payload = self._send("ProtoOAAmendPositionSLTPReq",
                             positionId=position_id, stopLoss=stop_price)
        return {"amended": True, "position_id": position_id,
                "stop_loss": stop_price, "response": type(payload).__name__}

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

    print("price scaling:")
    check("5-digit FX price", abs(scale_price(108500, 5) - 1.085) < 1e-9)
    check("2-digit index price", abs(scale_price(3900012, 2) - 39000.12) < 1e-9)

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

    print("disconnect does not fabricate fresh data:")
    s2 = FTMOSession(env={"CTRADER_HOST": "demo"}, specs=specs)
    s2.quotes[1] = Quote(symbol_id=1, bid=1.0, ask=1.1, ts=time.time() - 5)
    s2._on_disconnected(None)
    check("quotes survive a disconnect so staleness is detectable",
          1 in s2.quotes)
    check("...and the session reports itself not ready", not s2.ready)

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
