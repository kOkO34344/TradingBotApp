"""
trader_worker.py — the thread that is allowed to place orders.

Why a whole separate thread exists, when the API already has an IB
connection:

`ibkr_service.place_bracket_order`, `place_market_order`, `wait_for_status`
and `verify_stop_protection` are SYNCHRONOUS. They call `ib.sleep()`, which
calls `IB.run()`, which calls `loop.run_until_complete()` — and that raises
`RuntimeError: This event loop is already running` if invoked from inside
FastAPI's async handler. So those functions simply cannot be called from a
route.

There were three ways out, and only one of them is safe:

  1. Reimplement the order path in async. Rejected outright: it would fork
     the risk-handling code, and the entire reason `execute_rebalance` is
     shared between the human-approved and autotrade paths is that the two
     must never be able to diverge in how they enforce risk. A third
     implementation for the browser would be strictly worse.
  2. Call the sync functions from a thread while sharing the hub's IB
     object. Rejected: ib_async's socket and event dispatch belong to the
     loop that created them; driving one connection from two loops is a
     data race on the wire protocol.
  3. What this module does — a dedicated worker thread with its OWN event
     loop and its OWN IB connection, running the existing synchronous
     functions completely unmodified. `ib_async.util.getLoop()` is
     thread-aware, so inside this thread the sync code behaves exactly as it
     does in `paper_trader.py`. Nothing about risk enforcement is
     reimplemented, reordered or relaxed.

The worker holds ONE long-lived connection (clientId 16) rather than
connecting per order: CLAUDE.md records IB Gateway refusing new API
connections after a run of connects with distinct client_ids, and an order
path is the last thing that should be exposed to that.

Being a single thread, it also serialises every write. Two flatten requests
from two browser tabs queue instead of racing.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ibkr_service as ib_svc  # noqa: E402

log = logging.getLogger("api.trader_worker")

WRITE_CLIENT_ID = 16        # 15 is the read hub; 7/9/11/13 are the CLI tools
CONNECT_TIMEOUT = 10


class WorkerError(RuntimeError):
    """Something went wrong inside the worker; the message is user-facing."""


@dataclass
class _Job:
    fn: Callable
    args: tuple
    kwargs: dict
    future: concurrent.futures.Future


class TraderWorker:
    """Runs synchronous ibkr_service calls on a private connection."""

    def __init__(self, port: int, host: str = "127.0.0.1",
                 client_id: int = WRITE_CLIENT_ID):
        self.port = port
        self.host = host
        self.client_id = client_id
        self.ib = None
        self.account: str | None = None
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._stop = False

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._run, name="trader-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        # This thread gets its own event loop. ib_async's getLoop() is
        # thread-aware, so every sync helper below resolves to THIS loop and
        # `run_until_complete` is legal here.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._stop:
                job = self._queue.get()
                if job is None:
                    break
                try:
                    self._ensure_connected()
                    result = job.fn(self.ib, *job.args, **job.kwargs)
                    job.future.set_result(result)
                except Exception as exc:                # noqa: BLE001
                    job.future.set_exception(exc)
        finally:
            if self.ib is not None and self.ib.isConnected():
                self.ib.disconnect()
            loop.close()

    def _ensure_connected(self) -> None:
        from ib_async import IB

        if self.ib is not None and self.ib.isConnected():
            return
        # Rule 1 is enforced inside ibkr_service.connect(): a live port raises
        # unless allow_live=True, which this worker never passes. A browser is
        # exactly the kind of surface where a live port could be selected by
        # accident, so the refusal stays at the lowest level.
        self.ib = ib_svc.connect(
            port=self.port, host=self.host, client_id=self.client_id,
            allow_live=False, readonly=False,
        )
        # Verified by account id, never inferred from the port number.
        self.account = ib_svc.verify_paper_account(self.ib, allow_live=False)
        self.ib.reqMarketDataType(3)     # no live data subscription on this account
        log.info("Trader worker connected: %s (clientId %s)",
                 self.account, self.client_id)

    # -------------------------------------------------------------- submission

    async def call(self, fn: Callable, *args, timeout: float = 90, **kwargs) -> Any:
        """Run `fn(ib, *args, **kwargs)` on the worker thread and await it.

        `fn` receives the worker's own IB as its first argument, so callers
        pass the ibkr_service functions directly and unmodified.
        """
        self.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put(_Job(fn=fn, args=args, kwargs=kwargs, future=fut))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise WorkerError(
                f"The order operation did not complete within {timeout:g}s. "
                "Its outcome is UNKNOWN — check the journal and IBKR before "
                "retrying, so you don't place it twice."
            ) from exc


_worker: TraderWorker | None = None


def set_worker(worker: TraderWorker | None) -> None:
    global _worker
    _worker = worker


def get_worker() -> TraderWorker:
    if _worker is None:
        raise WorkerError("Trading worker not initialised — API still starting.")
    return _worker
