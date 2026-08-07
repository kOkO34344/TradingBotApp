"""
ib_hub.py — the web UI's single, long-lived connection to IB Gateway.

Why a hub instead of connecting per request:

  * IB Gateway hands out one session per clientId, and this project already
    burns four (trader_app 7, paper_trader 9, reflect_on_trades 11,
    autotrade_runner 13). The web API takes WEB_CLIENT_ID (15) and holds it,
    so a browser refresh never races the hourly autotrade job for a slot.
  * Connecting on every request is how you get "Gateway stops answering new
    API connections while the port stays open" — already seen on this
    machine 2026-07-27 after a run of connects with distinct client_ids.
  * IBKR paces historical-data requests (roughly 60 per 10 minutes). One
    process with one cache can respect that; N independent request handlers
    cannot.

Degraded mode is deliberate. If the Gateway is down the API still starts and
answers, with `connected: false` and a reason — the UI is then honest about
having no data rather than failing to load. It never invents numbers to fill
the gap, and it never reports a stale cached bar as live.

Everything that touches orders goes through `ibkr_service.py`; this module
owns the socket and the read paths only.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ibkr_service as ib_svc  # noqa: E402
from ib_async import IB  # noqa: E402

log = logging.getLogger("api.ib_hub")

WEB_CLIENT_ID = 15          # see docstring — must not collide with 7/9/11/13
DELAYED_MARKET_DATA = 3     # reqMarketDataType: this account has no live sub
RECONNECT_BACKOFF = [2, 5, 10, 20, 30, 60]   # seconds, then holds at 60

# Fallback ids for the read hub, tried in order when the preferred one is
# refused. IB Gateway holds a clientId for a while after a client dies
# uncleanly — kill the API mid-request and the next start is met with
# "Peer closed connection. clientId 15 already in use?" and then timeouts
# forever, because retrying the SAME id can never succeed until Gateway
# lets go. Observed on this machine; the port stays open throughout, so it
# looks like a dead Gateway when it is actually a busy id.
#
# 16 is deliberately absent — that belongs to the write worker.
CLIENT_ID_CANDIDATES = [WEB_CLIENT_ID, 17, 18, 19, 20]
ROTATE_AFTER_FAILURES = 2


@dataclass
class ConnectionState:
    """What the UI is told about the socket, in one place.

    `paper` is the gate every trading control keys off. It is only ever True
    after `verify_paper_account` has actually returned an account id starting
    with 'D' — never inferred from the port number, because a port is a
    setting and an account id is a fact.
    """
    connected: bool = False
    account: str | None = None
    paper: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    client_id: int = WEB_CLIENT_ID
    since: float | None = None
    error: str | None = None
    attempts: int = 0
    market_data_type: str = "delayed"
    # True when the backend was told not to dial Gateway at all (rule 9).
    # "Deliberately not connected" and "should be connected and isn't" look
    # identical in every other field, and the UI must not render the first as
    # a fault — that is what made a healthy FTMO dashboard look broken.
    disabled: bool = False

    def as_dict(self) -> dict:
        return {
            "connected": self.connected,
            "account": self.account,
            "paper": self.paper,
            "host": self.host,
            "port": self.port,
            "clientId": self.client_id,
            "connectedSince": self.since,
            "error": self.error,
            "attempts": self.attempts,
            "marketDataType": self.market_data_type,
            "disabled": self.disabled,
        }


class EventBus:
    """Fan-out for WebSocket clients.

    Each subscriber gets its own bounded queue. A slow browser tab drops its
    own oldest messages instead of blocking the IBKR event loop — losing a
    price tick to a stalled tab is fine; stalling the connection that manages
    stops is not.
    """

    def __init__(self, maxsize: int = 256):
        self._subs: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, topic: str, payload: Any) -> None:
        msg = {"topic": topic, "ts": time.time(), "data": payload}
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()      # drop oldest, keep the newest state
                    q.put_nowait(msg)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


class IBHub:
    """Owns the IB connection, its lifecycle, and read access to it."""

    def __init__(self, port: int, host: str = "127.0.0.1",
                 client_id: int = WEB_CLIENT_ID):
        self.ib = IB()
        self.bus = EventBus()
        self.state = ConnectionState(host=host, port=port, client_id=client_id)
        self._lock = asyncio.Lock()          # serialises IBKR requests
        self._reconnect_task: asyncio.Task | None = None
        self._stop = False
        self._preferred_client_id = client_id
        self._id_index = 0
        self._consecutive_failures = 0

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Connect, then keep trying in the background if it fails.

        Never raises: a Gateway that is down must not stop the API from
        serving the journal, settings and backtest screens, which need no
        connection at all.
        """
        self._stop = False
        await self._try_connect()
        self._reconnect_task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self._stop = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        if self.ib.isConnected():
            self.ib.disconnect()
        self.state.connected = False

    async def _try_connect(self) -> bool:
        if self.ib.isConnected():
            return True
        self.state.attempts += 1
        try:
            port = self.state.port
            # Rule 1 lives in ibkr_service.connect(): live ports raise unless
            # allow_live=True. The web API never passes it — a UI is exactly
            # the kind of place a live port could get selected by accident.
            if port in (ib_svc.LIVE_PORT_TWS, ib_svc.LIVE_PORT_GATEWAY):
                raise RuntimeError(
                    f"Port {port} is a LIVE trading port. The web API refuses it."
                )
            await self.ib.connectAsync(
                self.state.host, port, clientId=self.state.client_id,
                readonly=False, timeout=8,
            )
            account = ib_svc.verify_paper_account(self.ib)   # raises on non-paper
            self.ib.reqMarketDataType(DELAYED_MARKET_DATA)

            self.state.connected = True
            self.state.account = account
            self.state.paper = account.startswith("D")
            self.state.since = time.time()
            self.state.error = None
            self._consecutive_failures = 0
            self._wire_events()
            log.info("Connected to IB Gateway %s:%s as %s (clientId %s)",
                     self.state.host, port, account, self.state.client_id)
            self.bus.publish("connection", self.state.as_dict())
            return True
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            self.state.connected = False
            self.state.paper = False
            self.state.account = None
            self._consecutive_failures += 1
            detail = f"{type(exc).__name__}: {exc}".rstrip(": ")
            self.state.error = (
                f"{detail} (clientId {self.state.client_id})"
            )
            log.warning("IB connect failed on clientId %s: %s",
                        self.state.client_id, detail)

            # Retrying a clientId Gateway still considers taken can never
            # succeed, so move to the next candidate rather than looping on
            # the same refusal until someone notices.
            if self._consecutive_failures >= ROTATE_AFTER_FAILURES:
                self._consecutive_failures = 0
                self._id_index = (self._id_index + 1) % len(CLIENT_ID_CANDIDATES)
                next_id = CLIENT_ID_CANDIDATES[self._id_index]
                if next_id != self.state.client_id:
                    log.warning(
                        "clientId %s is not connecting; trying %s next. "
                        "(Gateway holds an id for a while after a client dies "
                        "uncleanly.)", self.state.client_id, next_id)
                    self.state.client_id = next_id
                    self.state.error = (
                        f"{detail}. Retrying on clientId {next_id} — the "
                        "previous id may still be held by an earlier run."
                    )
                    # ib_async keeps per-connection state; a fresh object
                    # avoids inheriting anything from the failed attempt.
                    self.ib = IB()

            self.bus.publish("connection", self.state.as_dict())
            if self.ib.isConnected():
                self.ib.disconnect()
            return False

    async def _supervise(self) -> None:
        """Reconnect loop with backoff. Runs for the life of the process."""
        idx = 0
        while not self._stop:
            if self.ib.isConnected():
                idx = 0
                await asyncio.sleep(5)
                continue
            if self.state.connected:     # we thought we were up, and aren't
                self.state.connected = False
                self.state.error = "Connection to IB Gateway dropped."
                self.bus.publish("connection", self.state.as_dict())
            delay = RECONNECT_BACKOFF[min(idx, len(RECONNECT_BACKOFF) - 1)]
            await asyncio.sleep(delay)
            if self._stop:
                return
            if not await self._try_connect():
                idx += 1

    def _wire_events(self) -> None:
        """Push IBKR's own events straight to the browser.

        These are the events the UI is watching for: a stop firing, an order
        going terminal, the account revaluing. Handlers are attached with
        `+=` on every (re)connect, so they are cleared first to avoid a
        duplicate handler after a reconnect publishing everything twice.
        """
        self.ib.orderStatusEvent.clear()
        self.ib.execDetailsEvent.clear()
        self.ib.positionEvent.clear()
        self.ib.accountValueEvent.clear()
        self.ib.errorEvent.clear()
        self.ib.disconnectedEvent.clear()

        self.ib.orderStatusEvent += self._on_order_status
        self.ib.execDetailsEvent += self._on_exec
        self.ib.positionEvent += self._on_position
        self.ib.accountValueEvent += self._on_account_value
        self.ib.errorEvent += self._on_error
        self.ib.disconnectedEvent += self._on_disconnected

    # --------------------------------------------------------------- events

    def _on_order_status(self, trade) -> None:
        self.bus.publish("orderStatus", {
            "orderId": trade.order.orderId,
            "permId": trade.order.permId,
            "symbol": getattr(trade.contract, "symbol", ""),
            "action": trade.order.action,
            "orderType": trade.order.orderType,
            "tif": trade.order.tif,
            "quantity": float(trade.order.totalQuantity or 0),
            "filled": float(trade.orderStatus.filled or 0),
            "remaining": float(trade.orderStatus.remaining or 0),
            "avgFillPrice": float(trade.orderStatus.avgFillPrice or 0) or None,
            "status": trade.orderStatus.status,
        })

    def _on_exec(self, trade, fill) -> None:
        self.bus.publish("execution", {
            "symbol": getattr(fill.contract, "symbol", ""),
            "side": fill.execution.side,
            "shares": float(fill.execution.shares or 0),
            "price": float(fill.execution.price or 0),
            "time": fill.execution.time.isoformat() if fill.execution.time else None,
            "execId": fill.execution.execId,
        })

    def _on_position(self, position) -> None:
        self.bus.publish("position", {
            "account": position.account,
            "symbol": getattr(position.contract, "symbol", ""),
            "secType": getattr(position.contract, "secType", ""),
            "position": float(position.position or 0),
            "avgCost": float(position.avgCost or 0),
        })

    def _on_account_value(self, value) -> None:
        # The account channel is chatty; only forward what the UI displays.
        if value.tag in ("NetLiquidation", "TotalCashValue", "UnrealizedPnL",
                         "RealizedPnL", "AvailableFunds", "BuyingPower",
                         "ExchangeRate", "MaintMarginReq"):
            self.bus.publish("accountValue", {
                "tag": value.tag, "value": value.value,
                "currency": value.currency, "account": value.account,
            })

    def _on_error(self, reqId, code, message, contract=None) -> None:
        # 2104/2106/2158 are "market data farm is fine" chatter, not errors.
        if code in (2104, 2106, 2107, 2158, 2119):
            return
        self.bus.publish("ibError", {
            "reqId": reqId, "code": code, "message": message,
            "symbol": getattr(contract, "symbol", None) if contract else None,
        })

    def _on_disconnected(self) -> None:
        self.state.connected = False
        self.state.error = "Disconnected from IB Gateway."
        self.bus.publish("connection", self.state.as_dict())

    # ------------------------------------------------------------ accessors

    def require(self) -> IB:
        """Return the live IB, or raise a message the UI can show verbatim."""
        if not self.ib.isConnected():
            raise ConnectionError(
                self.state.error
                or "Not connected to IB Gateway. Start it and enable the API "
                   f"(expected {self.state.host}:{self.state.port})."
            )
        return self.ib

    def require_paper(self) -> IB:
        """Same, but also enforces the paper gate for anything that trades."""
        ib = self.require()
        if not self.state.paper:
            raise PermissionError(
                f"Account {self.state.account} is not a verified paper account. "
                "Trading controls are disabled."
            )
        return ib

    async def run(self, fn: Callable, *args, **kwargs):
        """Run one IBKR request at a time.

        The lock is not paranoia: ib_async multiplexes on a single socket and
        this project has already been bitten by concurrent request pileups
        making the Gateway stop answering. Serialising costs nothing on a
        UI-scale request rate.
        """
        async with self._lock:
            result = fn(*args, **kwargs)
            # ib_async returns bare Futures from several request methods, not
            # coroutines — `iscoroutine` alone silently hands the Future back
            # to the caller unawaited.
            if inspect.isawaitable(result):
                return await result
            return result


# A module-level singleton, set by main.py's lifespan. Route modules import
# `get_hub()` rather than the object, so import order can't capture a None.
_hub: IBHub | None = None


def set_hub(hub: IBHub | None) -> None:
    global _hub
    _hub = hub


def get_hub() -> IBHub:
    if _hub is None:
        raise RuntimeError("IB hub not initialised — API still starting up.")
    return _hub
