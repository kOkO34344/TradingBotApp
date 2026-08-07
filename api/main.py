"""
main.py — FastAPI app for the TradingBotApp web UI.

Local only. It binds to 127.0.0.1 by default and is never deployed: it
holds a live IB Gateway connection on WEB_CLIENT_ID and can place orders,
so exposing it would put an unauthenticated order path on the network.
`web/README.md` says the same thing in the place someone is likely to
read it before running `--host 0.0.0.0`.

Route groups:
    /api/status        connection, account, paper gate, risk limits
    /api/symbols       symbol resolution + watchlist
    /api/bars          historical bars (cached, delayed-labelled)
    /api/indicators    catalog + computed series (via indicators.py)
    /api/journal       trade_journal.csv, corrections annotated
    /api/positions     live positions with stop-protection status
    /ws                WebSocket push (connection, orders, fills, account)

Every response that carries market data also carries where it came from and
how old it is. That is not decoration: this project has twice been misled by
a record that looked authoritative and wasn't, so the UI is built to make
staleness and disagreement visible rather than smoothing them over.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import ibkr_service as ib_svc  # noqa: E402
import signal_policy  # noqa: E402
import trader_app as ta  # noqa: E402

from . import backtests_api  # noqa: E402
from . import ftmo_api  # noqa: E402
from . import bars as bars_mod  # noqa: E402
from . import indicators_api  # noqa: E402
from . import jobs  # noqa: E402
from . import journal_api  # noqa: E402
from . import kronos_api  # noqa: E402
from . import rebalance  # noqa: E402
from . import trading  # noqa: E402
from .contracts import SymbolError, describe, resolve  # noqa: E402
from .ib_hub import IBHub, get_hub, set_hub  # noqa: E402
from .trader_worker import (TraderWorker, WorkerError, get_worker,  # noqa: E402
                            set_worker)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("api.main")

RISK_FILE = BASE_DIR / "risk_limits.json"

# Shorter than ibkr_service.OPEN_ORDERS_TIMEOUT (30s) on purpose. That value
# guards an order-placement path, where waiting is cheaper than guessing. This
# is a dashboard that polls: a 30s stall would freeze every screen behind one
# wedged request, and Koko would be looking at a spinner instead of at the
# reason. The SEMANTICS are identical either way — an unanswered request is
# reported as UNKNOWN, never as "no stop found" — only the patience differs.
WEB_OPEN_ORDERS_TIMEOUT = 8.0


def _settings() -> dict:
    """Read trader_settings.json through trader_app, so defaults match."""
    return ta.load_settings()


def ibkr_web_enabled(settings: dict | None = None) -> bool:
    """Whether the web backend should dial IB Gateway at all. OFF by default.

    Rule 9 retired IBKR in place. The hub's reconnect supervisor, however, runs
    for the life of the process, so a retired venue produced an endless stream
    of

        ConnectionRefusedError: [Errno 61] Connect call failed ('127.0.0.1', 4002)

    which `/api/status` reported to every screen on every poll — turning a
    deliberate, expected state into what looked like a broken application, on
    a dashboard whose actual trading venue was connected and healthy the whole
    time.

    Default OFF rather than "on if it works", because trying and failing IS the
    failure mode: each attempt burns a clientId that Gateway then holds, which
    is the documented way to make the NEXT genuine connection fail.

    **This does NOT stop IBKR's positions being managed.** `reflect_on_trades.py`
    runs as its own process on its own launchd schedule with its own clientId,
    and the three open positions and their GTC stops are its job. This flag
    governs one thing: whether the *browser* keeps a read connection open.

    Set `ibkr.web_enabled` true in trader_settings.json to turn the IBKR
    screens back on when Gateway is running.
    """
    block = (settings if settings is not None else _settings()).get("ibkr") or {}
    return bool(block.get("web_enabled", False))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _settings()
    port = int(settings.get("ibkr_port", ib_svc.PAPER_PORT_GATEWAY))
    hub = IBHub(port=port)
    set_hub(hub)
    if ibkr_web_enabled(settings):
        await hub.start()      # never raises; degraded mode if Gateway is down
    else:
        hub.state.disabled = True
        hub.state.error = (
            "IBKR is retired in place (rule 9) and the web backend is not "
            "connecting to it. Its open positions are still managed by "
            "reflect_on_trades.py. Set ibkr.web_enabled true in "
            "trader_settings.json to re-enable these screens.")
        log.info("IBKR web connection disabled — FTMO is the active venue")

    # The write worker connects lazily on its first order, not here: most
    # sessions never place one, and an idle second connection is a second
    # thing that can wedge.
    worker = TraderWorker(port=port)
    set_worker(worker)
    try:
        yield
    finally:
        jobs.registry.shutdown()
        worker.stop()
        set_worker(None)
        await hub.stop()
        set_hub(None)


app = FastAPI(title="TradingBotApp API", version="0.1.0", lifespan=lifespan)

# The Next.js dev server runs on 3000; only localhost origins are allowed,
# matching the local-only deployment decision.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _fail(exc: Exception) -> HTTPException:
    """Turn an internal error into something the UI can show verbatim."""
    if isinstance(exc, HTTPException):
        return exc                      # already shaped; don't relabel as 500
    if isinstance(exc, ConnectionError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (SymbolError, indicators_api.IndicatorError,
                        bars_mod.BarFetchError, trading.TradingError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, WorkerError):
        # The outcome may be genuinely unknown (see TraderWorker.call), so
        # this is 504 "no answer", never 500 "it failed".
        return HTTPException(status_code=504, detail=str(exc))
    log.exception("Unhandled error")
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ status

@app.get("/api/status")
async def status():
    """Everything the app shell needs: connection, paper gate, limits, policy."""
    hub = get_hub()
    settings = _settings()
    try:
        limits = json.loads(RISK_FILE.read_text())
    except Exception:                                  # noqa: BLE001
        limits = dict(ib_svc.DEFAULT_LIMITS)

    autotrade = settings.get("autotrade", {}) or {}
    signal = settings.get("signal", signal_policy.DEFAULT_SIGNAL)

    return {
        "connection": hub.state.as_dict(),
        "riskLimits": limits,
        "signal": {
            "active": signal,
            "default": signal_policy.DEFAULT_SIGNAL,
            "disabled": sorted(signal_policy.DISABLED_SIGNALS),
        },
        "autotrade": {
            "enabled": bool(autotrade.get("enabled", False)),
            "signal": autotrade.get("signal", signal_policy.DEFAULT_SIGNAL),
            "allowMomentum": bool(autotrade.get("allow_momentum", False)),
        },
        "journal": journal_api.summary(),
        "marketOpen": ib_svc.market_is_open(),
        "settings": {
            "riskPctPerTrade": settings.get("risk_pct_per_trade"),
            "momentumTopN": settings.get("momentum_top_n"),
            "benchmark": settings.get("benchmark"),
            "ibkrPort": settings.get("ibkr_port"),
        },
    }


@app.get("/api/account")
async def account():
    """Account values, with NetLiquidation converted to USD the safe way.

    Conversion goes through `paper_trader.get_net_liquidation_usd`, which
    uses IBKR's own ExchangeRate account value and verifies its direction
    against an independent quote. Never off a live FX quote — that path
    needs a market-data line this account doesn't have (error 10197) and
    an inverted rate misstates equity by ~29%.
    """
    hub = get_hub()
    try:
        ib = hub.require()
        # accountValues() reads the cache the account-update subscription
        # fills. It is populated on connect and kept current by the
        # accountValueEvent the hub is already listening to.
        values: dict[str, dict] = {}
        for v in ib.accountValues():
            values.setdefault(v.tag, {})[v.currency] = v.value

        import paper_trader  # imported lazily: pulls in yfinance
        net_liq_usd, conversion_error = None, None
        try:
            net_liq_usd = await hub.run(paper_trader.get_net_liquidation_usd, ib)
        except Exception as exc:                        # noqa: BLE001
            conversion_error = f"{type(exc).__name__}: {exc}"

        def num(tag, ccy=None):
            entry = values.get(tag, {})
            raw = entry.get(ccy) if ccy else next(iter(entry.values()), None)
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        return {
            "account": hub.state.account,
            "paper": hub.state.paper,
            "netLiquidationUsd": net_liq_usd,
            "conversionError": conversion_error,
            "baseCurrency": next((c for c in values.get("NetLiquidation", {})
                                  if c != "BASE"), None),
            "netLiquidation": values.get("NetLiquidation", {}),
            "totalCash": values.get("TotalCashValue", {}),
            "unrealizedPnl": num("UnrealizedPnL", "BASE") or num("UnrealizedPnL"),
            "realizedPnl": num("RealizedPnL", "BASE") or num("RealizedPnL"),
            "availableFunds": num("AvailableFunds", "BASE") or num("AvailableFunds"),
            "buyingPower": num("BuyingPower", "BASE") or num("BuyingPower"),
            "exchangeRates": values.get("ExchangeRate", {}),
        }
    except Exception as exc:                            # noqa: BLE001
        raise _fail(exc)


# ----------------------------------------------------------------- symbols

@app.get("/api/symbols/resolve")
async def resolve_symbol(q: str = Query(..., description="Typed symbol")):
    try:
        return describe(resolve(q))
    except Exception as exc:                            # noqa: BLE001
        raise _fail(exc)


@app.get("/api/symbols/search")
async def search_symbols(
    q: str = Query(..., min_length=1),
    limit: int = Query(12, ge=1, le=30),
):
    """Typeahead for the chart's symbol box.

    Two tiers, in this order:

      1. The watchlist, matched locally. Instant, needs no round trip, and
         these are the symbols actually being traded — they should never be
         buried under a broker search result.
      2. IBKR's own `reqMatchingSymbols`. Deliberately the source rather than
         a static ticker list: it returns what this account can actually
         trade, with the exchange and currency IBKR will use. A local list
         would happily suggest symbols the order path would then reject.

    Tier 2 failing is not an error. If IBKR doesn't answer, the watchlist
    matches still come back with `brokerSearch: false` so the UI can say the
    list is incomplete rather than implying nothing else exists.
    """
    query = q.strip().upper()
    settings = _settings()
    watchlist = [t.upper() for t in settings.get("tickers", [])]
    results: list[dict] = []
    seen: set[str] = set()

    # Tier 1: the watchlist. Prefix matches rank above substring ones so
    # typing "NV" puts NVDA first rather than something that merely contains
    # those letters.
    for ticker in sorted(
        (t for t in watchlist if query in t),
        key=lambda t: (not t.startswith(query), t),
    ):
        seen.add(f"STK:{ticker}:USD")
        results.append({
            "query": ticker,            # what goes in the box if picked
            "symbol": ticker,
            "label": ticker,
            "description": "On your watchlist",
            "secType": "STK",
            "exchange": "SMART",
            "currency": "USD",
            "source": "watchlist",
        })

    broker_ok = False
    broker_error = None
    hub = get_hub()
    if hub.state.connected:
        try:
            matches = await asyncio.wait_for(
                hub.run(hub.ib.reqMatchingSymbolsAsync, query), timeout=6)
            broker_ok = True
            for desc in matches or []:
                c = desc.contract
                symbol = (c.symbol or "").upper()
                sec_type = c.secType or ""
                currency = (c.currency or "").upper()
                if not symbol:
                    continue

                # Every row carries the EXACT query string the chart will
                # receive, so what the row promises is what gets charted.
                # Anything this box cannot express unambiguously is dropped
                # rather than guessed at: futures need an expiry, and an
                # index (MNQ/MES arrive as IND) is not the tradeable
                # contract, so both would resolve to something else or fail.
                if sec_type == "STK":
                    # `STK:SYM:CCY` keeps a foreign listing distinct from the
                    # US one — a bare "NVDA" is the ISLAND/USD line, and
                    # offering the Mexican listing under the same string
                    # would chart the wrong instrument.
                    chart_query = symbol if currency == "USD" else f"STK:{symbol}:{currency}"
                elif sec_type == "CASH":
                    # reqMatchingSymbols returns the base currency only, with
                    # no quote. USD is assumed and SAID so on the row; other
                    # pairs are typed directly (EUR.GBP).
                    if symbol == "USD":
                        continue
                    currency = "USD"
                    chart_query = f"FX:{symbol}USD"
                elif sec_type == "CRYPTO":
                    currency = currency or "USD"
                    chart_query = f"CRYPTO:{symbol}:{currency}"
                else:
                    continue

                key = f"{sec_type}:{symbol}:{currency}"
                if key in seen:
                    continue
                seen.add(key)

                name = (getattr(c, "description", "") or "").strip()
                venue = c.primaryExchange or c.exchange or ""
                detail = " · ".join(p for p in (venue, currency) if p)
                results.append({
                    "query": chart_query,
                    "symbol": symbol,
                    "label": f"{symbol}/USD" if sec_type == "CASH" else symbol,
                    "name": name,
                    "description": f"{name} — {detail}" if name else detail,
                    "secType": sec_type,
                    "exchange": venue or "SMART",
                    "currency": currency,
                    "source": "ibkr",
                })
            # Rank what this account can actually trade first: USD listings,
            # then symbols starting with what was typed. IBKR's own order
            # puts a Frankfurt line for NVDA above the US one, which is never
            # what's wanted here.
            #
            # Beyond those two rules the ORIGINAL order is kept, because it is
            # IBKR's relevance ranking and it is better than anything derivable
            # locally: for "MICRO" it knows MSFT is the answer and a symbol-
            # length tiebreak does not.
            head = [r for r in results if r["source"] == "watchlist"]
            tail = [(i, r) for i, r in enumerate(results)
                    if r["source"] != "watchlist"]
            tail.sort(key=lambda pair: (
                pair[1]["currency"] != "USD",
                not pair[1]["symbol"].startswith(query),
                pair[0],
            ))
            results = head + [r for _, r in tail]
        except asyncio.TimeoutError:
            broker_error = ("IBKR symbol search timed out — watchlist matches "
                            "only, so this list is incomplete.")
        except Exception as exc:                        # noqa: BLE001
            broker_error = (f"IBKR symbol search unavailable "
                            f"({type(exc).__name__}) — watchlist matches only.")
    else:
        broker_error = ("Not connected to IB Gateway — watchlist matches only, "
                        "so this list is incomplete.")

    return {
        "query": query,
        "results": results[:limit],
        "brokerSearch": broker_ok,
        "note": broker_error,
    }


@app.get("/api/symbols/watchlist")
async def watchlist():
    """Watchlist groups + the derived ticker union.

    Groups are the source of truth (see watchlist.py); `tickers` is derived
    and regenerated on save, so the UI shows both rather than implying the
    flat list can be edited directly.
    """
    settings = _settings()
    groups = settings.get("watchlist_groups") or {}
    tickers = settings.get("tickers", [])
    return {
        "groups": [{"name": name, "tickers": list(syms)}
                   for name, syms in groups.items()],
        "tickers": tickers,
        "resolved": [describe(resolve(t)) for t in tickers],
    }


# -------------------------------------------------------------------- bars

@app.get("/api/bars")
async def get_bars(
    symbol: str = Query(...),
    timeframe: str = Query(bars_mod.DEFAULT_TIMEFRAME),
    duration: str | None = Query(None),
    rth: bool = Query(False, description="Regular trading hours only"),
    indicators: str | None = Query(None, description="Comma list, e.g. sma:20,rsi:14"),
    levels: bool = Query(False, description="Include swing support/resistance"),
    markers: bool = Query(True, description="Include this symbol's journal markers"),
):
    """Bars + optional indicators, levels and trade markers, in one round trip.

    Bundled deliberately: separate calls would make the chart render the
    price series before its overlays and flicker, and would double the
    number of requests counted against IBKR's historical-data pacing.
    """
    hub = get_hub()
    try:
        sym = resolve(symbol)
        payload = await bars_mod.fetch_bars(hub, sym, timeframe, duration, rth)

        if indicators:
            specs = [s.strip() for s in indicators.split(",") if s.strip()]
            payload["indicators"] = indicators_api.compute(payload["bars"], specs)
        else:
            payload["indicators"] = []

        payload["levels"] = (indicators_api.levels(payload["bars"])
                             if levels else None)
        payload["markers"] = (journal_api.markers_for(sym.symbol)
                              if markers and sym.kind == "stock" else [])
        return payload
    except Exception as exc:                            # noqa: BLE001
        raise _fail(exc)


@app.get("/api/timeframes")
async def timeframes():
    return {"timeframes": bars_mod.timeframe_list(),
            "default": bars_mod.DEFAULT_TIMEFRAME}


@app.get("/api/indicators/catalog")
async def indicator_catalog():
    return {"indicators": indicators_api.catalog()}


# ----------------------------------------------------------------- journal

@app.get("/api/journal")
async def journal(
    symbol: str | None = Query(None),
    event: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    rows = journal_api.load_rows()
    if symbol:
        rows = [r for r in rows if r.symbol.upper() == symbol.upper()]
    if event:
        wanted = {e.strip().upper() for e in event.split(",")}
        rows = [r for r in rows if r.event.upper() in wanted]
    rows = rows[-limit:]
    return {"rows": [r.as_dict() for r in reversed(rows)],
            "summary": journal_api.summary()}


# --------------------------------------------------------------- positions

# Remembers a recent open-orders failure so a wedged Gateway is only probed
# once every few seconds instead of once per endpoint per poll. Without this,
# a single dashboard refresh queues three 8-second timeouts back to back
# behind the hub lock and the screen sits blank for half a minute.
_open_orders_failure: dict[str, float | str | None] = {"at": 0.0, "error": None}
OPEN_ORDERS_RETRY_AFTER = 20.0


async def _refresh_open_orders(hub) -> str | None:
    """Ask IBKR for open orders. Returns None on success, or why it failed.

    A timeout here is NOT an answer. An unanswered open-orders request comes
    back as an empty list, which is indistinguishable from "this position has
    no stop" — and on 2026-07-29 a wedged Gateway on this very machine
    answered position requests normally while `reqAllOpenOrders` timed out at
    30s and 45s. Treating that as "no stop found" would paint four naked
    positions in red on the dashboard and send Koko to re-protect positions
    that were already protected.

    So the caller gets a reason string and must render protection as UNKNOWN,
    not as unprotected. Same rule as `ibkr_service.OpenOrderStateUnknown`.
    """
    ib = hub.require()

    recent = _open_orders_failure
    age = time.time() - float(recent["at"] or 0)
    if recent["error"] and age < OPEN_ORDERS_RETRY_AFTER:
        return f"{recent['error']} (last attempt {age:.0f}s ago)"

    try:
        await asyncio.wait_for(
            hub.run(ib.reqAllOpenOrdersAsync),
            timeout=WEB_OPEN_ORDERS_TIMEOUT,
        )
        _open_orders_failure.update({"at": 0.0, "error": None})
        return None
    except asyncio.TimeoutError:
        message = (f"IBKR did not answer reqAllOpenOrders within "
                   f"{WEB_OPEN_ORDERS_TIMEOUT:g}s — open-order state is UNKNOWN. "
                   "This is NOT evidence that a stop is missing. Retry, or "
                   "restart IB Gateway if it keeps refusing (its open-orders "
                   "channel can wedge while positions and market data keep "
                   "working).")
    except Exception as exc:                            # noqa: BLE001
        message = (f"Open-orders request failed ({type(exc).__name__}: {exc}) — "
                   "open-order state is UNKNOWN, not empty.")

    _open_orders_failure.update({"at": time.time(), "error": message})
    return message


def _with_stop_prices(stops: list[dict], trades, symbol: str,
                      exit_action: str) -> list[dict]:
    """Attach each stop's trigger price, for drawing it on the chart.

    `ibkr_service._open_stops_for` deliberately returns only what the
    protection verdict needs (qty/tif/status) so that the verdict stays a
    pure, offline-testable function. The chart needs the price too, so it is
    added here — for DISPLAY only. The pass/fail decision above is still the
    shared function's, unchanged, so the two can't drift apart.
    """
    prices = [
        float(t.order.auxPrice or 0)
        for t in trades
        if getattr(t.contract, "symbol", None) == symbol
        and t.order.orderType in ib_svc.STOP_ORDER_TYPES
        and t.order.action == exit_action
    ]
    return [{**stop, "price": prices[i] if i < len(prices) else None}
            for i, stop in enumerate(stops)]


@app.get("/api/positions")
async def positions():
    """Open positions, each with an explicit stop-protection verdict.

    Three things this does that a naive implementation wouldn't, all because
    the naive version has already failed in production here:

      * Positions are re-requested and a timeout is allowed to RAISE. An
        empty `ib.positions()` cache is ambiguous — genuinely flat, or a
        swallowed startup timeout — and reading it as "flat" fabricated a
        full liquidation in reflect_on_trades.py on 2026-07-25.
      * Stops are read from live trades (via reqAllOpenOrders) and checked
        for `tif == "GTC"` and full quantity coverage, not merely for
        existing. A DAY stop looks fine for hours and then silently expires
        at the close, which is what left three positions unprotected
        overnight on 2026-07-21.
      * If the open-orders request goes unanswered, protection is reported as
        `null` (unknown) with the reason — never as false.
    """
    hub = get_hub()
    try:
        ib = hub.require()
        raw_positions = await asyncio.wait_for(
            hub.run(ib.reqPositionsAsync), timeout=20)
        orders_error = await _refresh_open_orders(hub)
        trades = ib.trades() if orders_error is None else []

        # Market value / unrealised P&L come from IBKR's own portfolio feed
        # rather than being recomputed from a separately-fetched price. Two
        # sources would disagree at the edges and the UI would have no way to
        # say which was right.
        portfolio = {
            getattr(item.contract, "symbol", ""): item
            for item in ib.portfolio()
        }

        out = []
        for pos in raw_positions:
            qty = float(pos.position or 0)
            if qty == 0:          # a closed position still reports at 0
                continue
            symbol = getattr(pos.contract, "symbol", "")
            exit_action = "SELL" if qty > 0 else "BUY"
            if orders_error is None:
                # The verdict comes from ibkr_service's shared functions, so
                # the UI can never disagree with what paper_trader and
                # reflect_on_trades consider "protected".
                stops = ib_svc._open_stops_for(trades, symbol, exit_action)
                protected, reason = ib_svc.stop_protection_status(stops, abs(qty))
                stops = _with_stop_prices(stops, trades, symbol, exit_action)
            else:
                stops, protected, reason = [], None, orders_error
            item = portfolio.get(symbol)
            avg_cost = float(pos.avgCost or 0)
            market_price = float(item.marketPrice) if item else None
            unrealized = float(item.unrealizedPNL) if item else None
            pct = None
            if market_price and avg_cost:
                pct = (market_price / avg_cost - 1) * 100 * (1 if qty > 0 else -1)

            out.append({
                "symbol": symbol,
                "secType": getattr(pos.contract, "secType", ""),
                "currency": getattr(pos.contract, "currency", ""),
                "position": qty,
                "avgCost": avg_cost,
                "marketPrice": market_price,
                "marketValue": float(item.marketValue) if item else None,
                "unrealizedPnl": unrealized,
                "unrealizedPct": pct,
                "account": pos.account,
                "protected": protected,          # None == unknown, never false-by-default
                "protectionReason": reason,
                "stops": stops,
            })
        out.sort(key=lambda p: p["symbol"])
        return {"positions": out, "count": len(out),
                "openOrdersError": orders_error}
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="IBKR did not answer the positions request. Position state "
                   "is unknown — not flat. Check that IB Gateway is responsive.")
    except Exception as exc:                            # noqa: BLE001
        raise _fail(exc)


@app.get("/api/orders")
async def orders():
    """Live open orders straight from IBKR.

    Uses reqAllOpenOrders, not the local order objects: on 2026-07-27
    ib_async logged a `Cancelled` status locally for orders IBKR still held
    as PreSubmitted, and the local view was the one that was wrong.
    """
    hub = get_hub()
    try:
        ib = hub.require()
        orders_error = await _refresh_open_orders(hub)
        if orders_error:
            # Returning an empty list here would read as "no open orders",
            # which is a claim this request did not establish.
            raise HTTPException(status_code=504, detail=orders_error)
        out = []
        for trade in ib.openTrades():
            out.append({
                "orderId": trade.order.orderId,
                "permId": trade.order.permId,
                "parentId": trade.order.parentId,
                "symbol": getattr(trade.contract, "symbol", ""),
                "secType": getattr(trade.contract, "secType", ""),
                "action": trade.order.action,
                "orderType": trade.order.orderType,
                "tif": trade.order.tif,
                "quantity": float(trade.order.totalQuantity or 0),
                "limitPrice": float(trade.order.lmtPrice or 0) or None,
                "stopPrice": float(trade.order.auxPrice or 0) or None,
                "status": trade.orderStatus.status,
                "filled": float(trade.orderStatus.filled or 0),
                "remaining": float(trade.orderStatus.remaining or 0),
            })
        out.sort(key=lambda o: (o["symbol"], o["orderId"]))
        return {"orders": out, "count": len(out)}
    except Exception as exc:                            # noqa: BLE001
        raise _fail(exc)


# --------------------------------------------------------------- websocket

# ---------------------------------------------------------------- FTMO venue
#
# Every one of these hops to a threadpool. ftmo_session runs Twisted on its own
# thread and blocks the caller, so awaiting it inline would stall the whole
# event loop for a protobuf round trip — including the requests the dashboard
# uses to report that something is wrong.
#
# IBKR keeps its endpoints above and stays READ-ONLY: rule 9 retired it for new
# orders while three positions are still open and monitored. FTMO is the
# venue that can trade.

@app.get("/api/ftmo/snapshot")
async def ftmo_snapshot():
    """Account, rule-engine verdict and positions in ONE consistent read."""
    return await run_in_threadpool(ftmo_api.snapshot)


@app.get("/api/ftmo/connection")
async def ftmo_connection():
    return ftmo_api.connection_state()


@app.get("/api/ftmo/quotes")
async def ftmo_quotes():
    return {"quotes": await run_in_threadpool(ftmo_api.quotes)}


@app.get("/api/ftmo/universe")
async def ftmo_universe():
    return {"universe": await run_in_threadpool(ftmo_api.universe)}


@app.get("/api/ftmo/bars")
async def ftmo_bars(
    symbol: str,
    period: str = Query(ftmo_api.DEFAULT_TIMEFRAME,
                        description="Chart key (1m..1d) or cTrader period (M1..D1)"),
    count: int | None = Query(None, description="Bars to pull; per-timeframe default"),
    indicators: str | None = Query(None, description="Comma list, e.g. sma:20,rsi:14"),
    levels: bool = Query(False, description="Include swing support/resistance"),
    markers: bool = Query(True, description="Include this symbol's FTMO journal fills"),
):
    """Bars + overlays for one FTMO symbol. The venue's own prints, not a proxy.

    `symbol` is an FTMO instrument name (`EURUSD`, `US30.cash`), which is why
    this does not go through `contracts.resolve()` — that resolver speaks IBKR
    contracts and knows nothing about CFDs.
    """
    specs = [s.strip() for s in (indicators or "").split(",") if s.strip()]
    try:
        return await run_in_threadpool(
            ftmo_api.bars, symbol, period, count, specs, levels, markers)
    except ValueError as e:              # an unknown timeframe is the caller's
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/ftmo/symbols")
async def ftmo_symbols():
    """Every chartable FTMO instrument, not just the traded universe."""
    return {"symbols": await run_in_threadpool(ftmo_api.all_symbols)}


@app.get("/api/ftmo/timeframes")
async def ftmo_timeframes():
    return {"timeframes": ftmo_api.timeframe_list(),
            "default": ftmo_api.DEFAULT_TIMEFRAME}


class FtmoAutotradeToggle(BaseModel):
    enabled: bool


@app.get("/api/ftmo/autotrade")
async def ftmo_autotrade_state():
    """Whether the FTMO runner is armed, and what it would run with."""
    return await run_in_threadpool(ftmo_api.autotrade_state)


@app.post("/api/ftmo/autotrade")
async def ftmo_set_autotrade(payload: FtmoAutotradeToggle):
    """Arm or disarm unattended Kronos trading on FTMO.

    Independent of the IBKR kill switch and of IB Gateway's health. Rule 9
    retired IBKR for new orders, so gating this venue's switch on a dead
    Gateway would make it impossible to disarm FTMO for a reason that has
    nothing to do with FTMO — and a switch you cannot reach when things are
    going wrong is not a switch.
    """
    try:
        return await run_in_threadpool(ftmo_api.set_autotrade, payload.enabled)
    except Exception as exc:                                  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"Could not change FTMO autotrade: {exc}")


@app.post("/api/ftmo/plan")
async def ftmo_plan(sampleCount: int | None = None):
    """Start a job computing what Kronos would trade on FTMO right now.

    Read-only: it runs the identical pipeline the unattended runner uses and
    stops before placing anything. One at a time, for the same reason the
    Kronos route refuses concurrent runs — two batch inferences on this
    machine produce results that look comparable but were computed under
    different load.
    """
    existing = jobs.registry.running("ftmo-plan")
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An FTMO plan is already in progress (job {existing[0].id}).")
    job = jobs.registry.submit(
        "ftmo-plan",
        lambda ctx: ftmo_api.plan(ctx, sample_count=sampleCount),
        params={"sampleCount": sampleCount},
    )
    return job.as_dict()


@app.websocket("/ws/ftmo")
async def ftmo_stream(ws: WebSocket):
    """Push the FTMO snapshot on a timer.

    Polled rather than event-driven, deliberately. The equity that matters is
    balance + floating P&L across every open position, so it changes on every
    tick of every held symbol — forwarding raw ticks would push far more
    messages than the UI can use and still leave the browser to recompute
    equity, which is exactly the recomputation that must not live in the
    presentation layer. One consistent server-computed snapshot per second is
    both cheaper and harder to get wrong.

    The first frame is sent immediately so a client connecting during an
    outage learns about it at once instead of looking healthy for a second.
    """
    await ws.accept()
    try:
        while True:
            snap = await run_in_threadpool(ftmo_api.snapshot)
            await ws.send_json({"topic": "ftmo", "data": snap})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:                                         # noqa: BLE001
        log.debug("FTMO WebSocket closed", exc_info=True)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Push channel: connection state, order status, fills, account values.

    The first message is always the current connection state, so a client
    that connects during a Gateway outage learns about it immediately
    rather than sitting silent and looking healthy.
    """
    await ws.accept()
    hub = get_hub()
    queue = hub.bus.subscribe()
    try:
        await ws.send_json({"topic": "connection", "data": hub.state.as_dict()})
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                # Keepalive doubles as a liveness signal for the UI's
                # "last update" indicator.
                await ws.send_json({"topic": "ping", "data": None})
                continue
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:                                   # noqa: BLE001
        log.debug("WebSocket closed", exc_info=True)
    finally:
        hub.bus.unsubscribe(queue)


# ------------------------------------------------------------ write actions
#
# Every one of these is two calls: preview, then execute with the token the
# preview returned. The execute step reads its parameters from the stored
# preview, never from the request body, so what was shown is necessarily what
# gets sent. See api/trading.py for the full reasoning.
#
# All of them run on the trader worker thread, which owns a separate IB
# connection — ibkr_service's order functions are synchronous and cannot run
# inside this event loop.


def _require_trading(hub) -> None:
    """Gate shared by every write route: verified paper account or nothing.

    Translates the refusal into a proper status code. Letting `require_paper`
    raise bare gave a plain 500 "Internal Server Error" when IB Gateway
    stopped answering — which tells the user nothing about what to fix, when
    the actual answer ("Gateway isn't responding, restart it") is right
    there in the connection state.
    """
    try:
        hub.require_paper()
    except (ConnectionError, PermissionError) as exc:
        raise _fail(exc) from exc


class FlattenRequest(BaseModel):
    symbol: str


class ReprotectRequest(BaseModel):
    symbol: str
    stopPrice: float


class BracketRequest(BaseModel):
    symbol: str
    action: str = "BUY"
    quantity: float | None = None
    stopPrice: float | None = None


class CancelRequest(BaseModel):
    orderId: int


class ExecuteRequest(BaseModel):
    token: str


def _preview_route(kind: str):
    """Shared error handling for the four preview endpoints."""
    async def wrapper(build, symbol: str, *args):
        hub = get_hub()
        _require_trading(hub)
        worker = get_worker()
        try:
            payload = await worker.call(build, *args)
        except trading.TradingError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except WorkerError as exc:
            raise HTTPException(status_code=504, detail=str(exc))
        except Exception as exc:                        # noqa: BLE001
            raise _fail(exc)
        return trading.make_preview(kind, symbol, payload).as_dict()
    return wrapper


@app.post("/api/trade/flatten/preview")
async def preview_flatten(req: FlattenRequest):
    return await _preview_route("flatten")(
        trading.build_flatten_preview, req.symbol.upper(), req.symbol)


@app.post("/api/trade/flatten/execute")
async def execute_flatten(req: ExecuteRequest):
    hub = get_hub()
    _require_trading(hub)
    preview = trading.take_preview(req.token, "flatten")
    worker = get_worker()
    result = await worker.call(
        trading.do_flatten, preview.symbol,
        preview.payload["quantity"], preview.payload["action"],
        timeout=120,
    )
    hub.bus.publish("tradeExecuted", result)
    return result


@app.post("/api/trade/reprotect/preview")
async def preview_reprotect(req: ReprotectRequest):
    return await _preview_route("reprotect")(
        trading.build_reprotect_preview, req.symbol.upper(),
        req.symbol, req.stopPrice)


@app.post("/api/trade/reprotect/execute")
async def execute_reprotect(req: ExecuteRequest):
    hub = get_hub()
    _require_trading(hub)
    preview = trading.take_preview(req.token, "reprotect")
    worker = get_worker()
    result = await worker.call(
        trading.do_reprotect, preview.symbol,
        preview.payload["quantity"], preview.payload["action"],
        preview.payload["stopPrice"], timeout=90,
    )
    hub.bus.publish("tradeExecuted", result)
    return result


@app.post("/api/trade/bracket/preview")
async def preview_bracket(req: BracketRequest):
    return await _preview_route("bracket")(
        trading.build_bracket_preview, req.symbol.upper(),
        req.symbol, req.action, req.quantity, req.stopPrice)


@app.post("/api/trade/bracket/execute")
async def execute_bracket(req: ExecuteRequest):
    hub = get_hub()
    _require_trading(hub)
    preview = trading.take_preview(req.token, "bracket")
    p = preview.payload
    worker = get_worker()
    result = await worker.call(
        trading.do_bracket, preview.symbol, p["action"], p["quantity"],
        p["entryLimit"], p["stopPrice"], timeout=180,
    )
    hub.bus.publish("tradeExecuted", result)
    return result


@app.post("/api/trade/cancel/preview")
async def preview_cancel(req: CancelRequest):
    hub = get_hub()
    _require_trading(hub)
    worker = get_worker()
    try:
        payload = await worker.call(trading.build_cancel_preview, req.orderId)
    except trading.TradingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        raise _fail(exc)
    return trading.make_preview("cancel", payload["symbol"], payload).as_dict()


@app.post("/api/trade/cancel/execute")
async def execute_cancel(req: ExecuteRequest):
    hub = get_hub()
    _require_trading(hub)
    preview = trading.take_preview(req.token, "cancel")
    worker = get_worker()
    result = await worker.call(
        trading.do_cancel, preview.payload["orderId"], timeout=90)
    hub.bus.publish("tradeExecuted", result)
    return result


# ----------------------------------------------------------------- kronos


class KronosRunRequest(BaseModel):
    tickers: list[str] | None = None
    draws: int = kronos_api.DEFAULT_DRAWS
    sampleCount: int = kronos_api.DEFAULT_SAMPLE_COUNT


class MonteCarloRequest(BaseModel):
    ticker: str
    paths: int = 12


@app.post("/api/kronos/run")
async def kronos_run(req: KronosRunRequest):
    """Start a forecast job. Returns immediately with a job id.

    Refuses to start a second run while one is in flight: two concurrent
    batch inferences on this machine would slow each other down and produce
    two results that look comparable but were computed under different load.
    """
    existing = jobs.registry.running("kronos")
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A Kronos run is already in progress (job {existing[0].id}). "
                   "Wait for it to finish or cancel it.")

    settings = _settings()
    tickers = [t.upper() for t in (req.tickers or settings.get("tickers", []))]
    if not tickers:
        raise HTTPException(status_code=400, detail="No tickers to forecast.")

    job = jobs.registry.submit(
        "kronos",
        lambda ctx: kronos_api.run_forecast(
            ctx, tickers, draws=req.draws, sample_count=req.sampleCount),
        params={"tickers": tickers, "draws": req.draws,
                "sampleCount": req.sampleCount},
    )
    return job.as_dict()


@app.post("/api/kronos/montecarlo")
async def kronos_montecarlo(req: MonteCarloRequest):
    existing = jobs.registry.running("kronos-mc")
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A Monte Carlo run is already in progress "
                   f"(job {existing[0].id}).")
    job = jobs.registry.submit(
        "kronos-mc",
        lambda ctx: kronos_api.monte_carlo(ctx, req.ticker, paths=req.paths),
        params={"ticker": req.ticker.upper(), "paths": req.paths},
    )
    return job.as_dict()


# -------------------------------------------------------------- rebalance


class RebalanceStart(BaseModel):
    dryRun: bool = False


class RebalanceDecision(BaseModel):
    jobId: str
    approved: bool


@app.post("/api/rebalance/start")
async def rebalance_start(req: RebalanceStart):
    """Run the signal and build a proposal, then wait for a decision.

    One long-lived call that pauses at the approval point, so the proposal
    shown and the orders placed come from the same `buy_plan` — see
    api/rebalance.py. A live rebalance blocks the trading worker while it
    waits, so only one may be in flight.
    """
    hub = get_hub()
    if not req.dryRun:
        _require_trading(hub)

    if jobs.registry.running("rebalance"):
        raise HTTPException(
            status_code=409,
            detail="A rebalance is already in progress. Approve, decline or "
                   "let it time out before starting another.")

    worker = get_worker()

    def job_fn(ctx):
        # The work must happen on the worker thread — execute_rebalance is
        # synchronous ibkr_service code and needs that thread's event loop
        # and connection. The job thread just holds the log and blocks here.
        future = worker.submit_sync(
            lambda ib: rebalance.run_rebalance(ctx, ib, dry_run=req.dryRun))
        return future.result(timeout=rebalance.APPROVAL_TIMEOUT + 600)

    job = jobs.registry.submit("rebalance", job_fn,
                               params={"dryRun": req.dryRun})
    return job.as_dict()


@app.get("/api/rebalance/pending")
async def rebalance_pending(jobId: str | None = None):
    """The proposal currently awaiting a decision, if any."""
    pending = rebalance.get_pending(jobId)
    running = jobs.registry.running("rebalance")
    return {
        "pending": pending.as_dict() if pending else None,
        "job": running[0].as_dict(include_result=False) if running else None,
    }


@app.post("/api/rebalance/decide")
async def rebalance_decide(req: RebalanceDecision):
    try:
        pending = rebalance.decide(req.jobId, req.approved, who="web UI")
    except rebalance.RebalanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"jobId": req.jobId, "approved": pending.approved}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, log: bool = True):
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"No job {job_id}. Finished jobs are kept for an hour.")
    return job.as_dict(include_log=log)


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    if not jobs.registry.cancel(job_id):
        raise HTTPException(
            status_code=400, detail="That job is not running.")
    return {"cancelled": job_id}


@app.get("/api/jobs")
async def job_list(kind: str | None = None, limit: int = 20):
    return {
        "jobs": [j.as_dict(include_log=False, include_result=False)
                 for j in jobs.registry.list(kind, limit)]
    }


@app.get("/api/kronos/latest")
async def kronos_latest(kind: str = "kronos"):
    """The most recent completed run, so reopening the page is free."""
    job = jobs.registry.latest(kind)
    running = jobs.registry.running(kind)
    return {
        "job": job.as_dict() if job else None,
        "running": running[0].as_dict(include_result=False) if running else None,
    }


class AutotradeToggle(BaseModel):
    enabled: bool


@app.post("/api/autotrade")
async def set_autotrade(payload: AutotradeToggle):
    """Turn unattended hourly rebalancing on or off.

    This is the kill switch in the header, so it is deliberately the one
    write action that works even when IB Gateway is unreachable — the whole
    point of a kill switch is that it works when things are going wrong.
    It edits `trader_settings.json`, which `autotrade_runner.py` re-reads on
    every firing, so the change takes effect from the next hour without
    touching launchd.

    The settings file is read and written as raw JSON rather than through
    `trader_app.load_settings()`, which merges in DEFAULT_SETTINGS and would
    silently write a pile of unrelated defaults into the owner's config as a
    side effect of flipping one boolean.

    Both directions are journalled. Rule 6 is about orders, but "who turned
    the robot on, and when" belongs in the same audit trail — especially
    for a feature CLAUDE.md flags as a deliberate experiment against the
    project's own evidence (rule 7).
    """
    settings_path = BASE_DIR / "trader_settings.json"
    try:
        raw = json.loads(settings_path.read_text())
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Could not read trader_settings.json: {exc}")

    autotrade = dict(raw.get("autotrade") or {})
    was = bool(autotrade.get("enabled", False))
    autotrade["enabled"] = payload.enabled
    autotrade.setdefault("signal", signal_policy.DEFAULT_SIGNAL)
    autotrade.setdefault("allow_momentum", False)
    raw["autotrade"] = autotrade

    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2))
    tmp.replace(settings_path)      # atomic: never leave a half-written config

    if was != payload.enabled:
        ib_svc.journal(
            "NOTE", None, status="autotrade " + ("enabled" if payload.enabled else "DISABLED"),
            detail=(f"Autotrade turned {'ON' if payload.enabled else 'OFF'} from the web UI. "
                    f"Signal: {autotrade.get('signal')}. "
                    f"RiskGuard remains enforced either way."),
        )
        log.info("Autotrade %s via web UI", "enabled" if payload.enabled else "disabled")

    return {
        "autotrade": {
            "enabled": autotrade["enabled"],
            "signal": autotrade.get("signal"),
            "allowMomentum": bool(autotrade.get("allow_momentum", False)),
        },
        "changed": was != payload.enabled,
    }


@app.get("/api/backtests")
async def backtests():
    """Recorded backtest results.

    Computed results and quoted findings are returned as separate keys, and
    every quoted finding carries its source. Nothing here re-runs anything.
    """
    return {
        "results": backtests_api.load_results(),
        "findings": backtests_api.RECORDED_FINDINGS,
    }


@app.get("/api/backtests/report")
async def backtests_report():
    md = backtests_api.report_markdown()
    if md is None:
        raise HTTPException(status_code=404, detail="No backtest_report.md.")
    return {"markdown": md}


@app.get("/api/health")
async def health():
    """Liveness for the launcher script — never touches IBKR."""
    return {"ok": True}
