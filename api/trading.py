"""
trading.py — write actions, every one of them preview-first.

The contract this module enforces:

    You cannot execute anything the UI has not first shown you, exactly as
    it will be sent.

A preview returns the concrete order — symbol, side, quantity, prices, TIF —
plus RiskGuard's verdict on it, and a short-lived token. Execute takes the
token and nothing else that matters; the parameters are read back from the
stored preview, not from the request body. So the browser cannot show one
order and submit another, whether through a bug, a stale tab, or a race
against a price that moved.

Tokens expire (PREVIEW_TTL). A preview priced off a two-minute-old quote is
not a preview of the order you would get now, and silently repricing it
would defeat the point.

Nothing here reimplements risk logic. Sizing is `paper_trader.size_position`,
limits are `ibkr_service.RiskGuard`, placement is `place_bracket_order` /
`place_market_order`, and all of it runs on the worker thread against the
same functions the terminal paths call.
"""
from __future__ import annotations

import logging
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ibkr_service as ib_svc  # noqa: E402

log = logging.getLogger("api.trading")

PREVIEW_TTL = 120.0          # seconds a quoted preview stays executable
STOP_ATR_MULT = 2.0          # matches paper_trader.STOP_ATR_MULT
ENTRY_LIMIT_BUFFER = 0.005   # matches paper_trader.ENTRY_LIMIT_BUFFER


class TradingError(RuntimeError):
    """User-facing problem with a requested action."""


@dataclass
class Preview:
    token: str
    kind: str                       # flatten | reprotect | bracket
    symbol: str
    created_at: float
    payload: dict = field(default_factory=dict)
    allowed: bool = False
    reason: str = ""

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > PREVIEW_TTL

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "kind": self.kind,
            "symbol": self.symbol,
            "createdAt": self.created_at,
            "expiresInSeconds": max(
                0, round(PREVIEW_TTL - (time.time() - self.created_at))),
            "allowed": self.allowed,
            "reason": self.reason,
            **self.payload,
        }


_previews: dict[str, Preview] = {}


def _store(preview: Preview) -> Preview:
    # Opportunistically drop expired entries; this dict is tiny and the
    # alternative is a background task for no benefit.
    for token, item in list(_previews.items()):
        if item.expired:
            _previews.pop(token, None)
    _previews[preview.token] = preview
    return preview


def take_preview(token: str, kind: str) -> Preview:
    """Consume a preview token. Single use — an execute cannot be replayed."""
    preview = _previews.pop(token, None)
    if preview is None:
        raise TradingError(
            "That preview is no longer valid. It may have already been "
            "executed, or the API restarted. Preview the action again."
        )
    if preview.kind != kind:
        raise TradingError("Preview token does not match the requested action.")
    if preview.expired:
        raise TradingError(
            f"That preview expired ({PREVIEW_TTL:.0f}s old) and prices have "
            "moved. Preview the action again to see the current order."
        )
    if not preview.allowed:
        raise TradingError(f"That action was not permitted: {preview.reason}")
    return preview


# --------------------------------------------------------------- helpers

def _position_for(ib, symbol: str):
    for pos in ib.positions():
        if getattr(pos.contract, "symbol", None) == symbol and pos.position != 0:
            return pos
    return None


def _refresh_open_orders(ib, timeout: float = 8.0) -> str | None:
    """Refresh the open-order cache. Returns None on success, else the reason.

    `ib.openTrades()` reads a cache. If the request that fills it goes
    unanswered the cache stays empty, and empty reads as "no resting orders"
    — which is the same ambiguity that produced the phantom liquidation from
    an empty `ib.positions()`. Callers must treat a non-None return as
    "unknown", not as "none".
    """
    try:
        ib.run(ib.reqAllOpenOrdersAsync(), timeout=timeout)
        return None
    except Exception as exc:                            # noqa: BLE001
        return (f"IBKR did not answer reqAllOpenOrders within {timeout:g}s "
                f"({type(exc).__name__}) — resting orders for this symbol "
                "CANNOT be enumerated right now.")


# ------------------------------------------------------- flatten a position

def build_flatten_preview(ib, symbol: str) -> dict:
    """What flattening `symbol` would do, without doing any of it.

    Runs on the worker thread. Note the exit is checked with `opening=False`:
    every exposure limit is gated on opening precisely so a limit can never
    trap you in a position (CLAUDE.md rule 3 — the 2026-07-27 incident where
    the notional cap made two winners un-exitable).
    """
    symbol = symbol.upper()
    pos = _position_for(ib, symbol)
    if pos is None:
        raise TradingError(
            f"No open position in {symbol} — nothing to flatten. "
            "(If you expected one, check the positions screen first: an "
            "unanswered request is not the same as a flat account.)"
        )

    qty = float(pos.position)
    action = "SELL" if qty > 0 else "BUY"
    contract = ib_svc.stock(symbol)
    ib.qualifyContracts(contract)
    price = ib_svc.market_price(ib, contract)

    orders_unknown = _refresh_open_orders(ib)
    open_orders = [
        {
            "orderId": t.order.orderId,
            "type": t.order.orderType,
            "action": t.order.action,
            "qty": float(t.order.totalQuantity or 0),
            "tif": t.order.tif,
            "status": t.orderStatus.status,
            "stopPrice": float(t.order.auxPrice or 0) or None,
        }
        for t in ib.openTrades()
        if getattr(t.contract, "symbol", None) == symbol
    ]

    guard = ib_svc.RiskGuard()
    ok, reason = guard.check(
        ib, contract, abs(qty), price,
        has_stop=True,        # a close needs no stop of its own
        opening=False,        # never let an exposure limit block an exit
    )

    # A flatten with unreadable open orders is genuinely riskier than usual:
    # `cancel_open_orders_for` can only cancel what it can see, so the
    # position's stop may survive the exit and later fire against nothing —
    # opening a NEW short. The action is still allowed (being unable to
    # enumerate orders is a bad reason to be unable to exit), but the UI has
    # to say this out loud before the click, not after.
    warnings = []
    if orders_unknown:
        warnings.append(
            f"{orders_unknown} The cancel step can only cancel orders it can "
            "see, so this symbol's stop may SURVIVE the exit and later fire "
            "against a position that no longer exists — which would open a "
            "new position in the opposite direction. Check open orders at "
            "IBKR directly after flattening."
        )
    elif not open_orders:
        warnings.append(
            "IBKR reports no resting orders for this symbol, so this position "
            "currently has no stop protecting it."
        )

    return {
        "symbol": symbol,
        "position": qty,
        "action": action,
        "quantity": abs(qty),
        "estimatedPrice": price,
        "estimatedProceeds": abs(qty) * price,
        "ordersToCancel": open_orders,
        "ordersUnknown": bool(orders_unknown),
        "warnings": warnings,
        "orderType": "MKT",
        "allowed": ok,
        "reason": reason,
        "steps": [
            (f"Cancel {len(open_orders)} working order(s) for {symbol} and "
             "confirm the cancel before flattening — a stale stop must not "
             "fire against a position that is already gone."
             if not orders_unknown else
             "Attempt to cancel resting orders — WARNING: they cannot be "
             "enumerated right now, so this step may cancel nothing."),
            f"{action} {abs(qty):g} {symbol} at market (~{price:,.2f}).",
        ],
    }


def do_flatten(ib, symbol: str, quantity: float, action: str) -> dict:
    """Cancel resting orders, confirm, then flatten. Journalled by ibkr_service."""
    import paper_trader

    contract = ib_svc.stock(symbol)
    ib.qualifyContracts(contract)

    cancelled = paper_trader.cancel_open_orders_for(ib, symbol)
    trade = ib_svc.place_market_order(
        ib, contract, quantity, action=action,
        allow_no_stop=True,     # an exit needs no stop of its own
        opening=False,          # rule 3: exposure limits never block an exit
    )
    if trade is None:
        raise TradingError(
            "RiskGuard blocked the exit. This should not happen — exposure "
            "limits are gated on `opening` so they cannot block a close. "
            "Check trade_journal.csv for the BLOCKED row and why."
        )
    status = trade.orderStatus
    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "status": status.status,
        "filled": float(status.filled or 0),
        "avgFillPrice": float(status.avgFillPrice or 0) or None,
        "cancelledOrders": len(cancelled),
    }


# ------------------------------------------------- re-protect with a GTC stop

def build_reprotect_preview(ib, symbol: str, stop_price: float) -> dict:
    """A standalone GTC stop covering the full position.

    Exists because `verify_stop_protection` deliberately does NOT
    auto-remediate — silently replacing a missing stop would hide how often
    it goes missing. This makes fixing it one deliberate click instead.
    """
    symbol = symbol.upper()
    pos = _position_for(ib, symbol)
    if pos is None:
        raise TradingError(f"No open position in {symbol} to protect.")

    qty = float(pos.position)
    exit_action = "SELL" if qty > 0 else "BUY"
    contract = ib_svc.stock(symbol)
    ib.qualifyContracts(contract)
    price = ib_svc.market_price(ib, contract)

    if stop_price <= 0:
        raise TradingError("Stop price must be positive.")
    # A long's stop must sit below the market and a short's above it, or the
    # stop triggers instantly and becomes a market order.
    if qty > 0 and stop_price >= price:
        raise TradingError(
            f"Stop {stop_price:,.2f} is at or above the current price "
            f"{price:,.2f} for a long position — it would trigger immediately."
        )
    if qty < 0 and stop_price <= price:
        raise TradingError(
            f"Stop {stop_price:,.2f} is at or below the current price "
            f"{price:,.2f} for a short position — it would trigger immediately."
        )

    orders_unknown = _refresh_open_orders(ib)
    if orders_unknown:
        # Placing a second stop when one already exists would leave the
        # position double-covered: the first fill closes it, the second turns
        # into a new short. Since existing coverage can't be read, say so.
        existing, covered, cover_reason = [], None, orders_unknown
    else:
        existing = ib_svc._open_stops_for(ib.openTrades(), symbol, exit_action)
        covered, cover_reason = ib_svc.stop_protection_status(existing, abs(qty))
    risk = abs(qty) * abs(price - stop_price)

    warnings = []
    if orders_unknown:
        warnings.append(
            "Existing stops cannot be read right now, so this may add a "
            "SECOND stop on top of one already live. If both fill, the "
            "second one opens a position in the opposite direction."
        )
    elif covered:
        warnings.append(
            f"This position already appears protected ({cover_reason}). "
            "Adding another stop would over-cover it."
        )

    return {
        "ordersUnknown": bool(orders_unknown),
        "warnings": warnings,
        "symbol": symbol,
        "position": qty,
        "action": exit_action,
        "quantity": abs(qty),
        "stopPrice": stop_price,
        "currentPrice": price,
        "tif": "GTC",
        "riskIfHit": risk,
        "distancePct": abs(price - stop_price) / price * 100,
        "alreadyProtected": covered,
        "existingCoverage": cover_reason,
        "allowed": True,
        "reason": "ok",
        "steps": [
            f"Place {exit_action} STP {abs(qty):g} {symbol} @ {stop_price:,.2f}, "
            "TIF=GTC explicitly — a DAY stop expires at the close and stops "
            "protecting anything.",
            "Existing stops are left alone; cancel them separately if this "
            "would double up.",
        ],
    }


def do_reprotect(ib, symbol: str, quantity: float, action: str,
                 stop_price: float) -> dict:
    from ib_async import StopOrder

    contract = ib_svc.stock(symbol)
    ib.qualifyContracts(contract)

    order = StopOrder(action, quantity, stop_price, tif="GTC")
    order.outsideRth = True
    ib_svc.journal("SUBMIT", contract, action, quantity, "", stop_price,
                   status="submitted",
                   detail="re-protect: standalone GTC stop (from web UI)")
    trade = ib.placeOrder(contract, order)
    ib.sleep(1.5)
    ib_svc.journal("RESULT", contract, action, quantity, "", stop_price,
                   status=trade.orderStatus.status, detail="re-protect stop")

    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "stopPrice": stop_price,
        "tif": trade.order.tif,
        "status": trade.orderStatus.status,
        "orderId": trade.order.orderId,
    }


# ------------------------------------------------------ manual bracket entry

def build_bracket_preview(ib, symbol: str, action: str,
                          quantity: float | None,
                          stop_price: float | None) -> dict:
    """A new position, sized the same way paper_trader sizes one.

    When `quantity` is omitted the size comes from
    `paper_trader.size_position` — risk budget divided by the stop distance,
    clamped to the notional cap using the BUFFERED entry price rather than
    the raw market price (a real bug found and fixed during the first live
    run: a quantity that looks fine at market price still gets blocked).
    """
    import pandas as pd  # noqa: F401  (paper_trader needs it loaded)

    import indicators as ind
    import paper_trader
    import trader_app as ta

    symbol = symbol.upper()
    action = action.upper()
    if action not in ("BUY", "SELL"):
        raise TradingError("Action must be BUY or SELL.")

    contract = ib_svc.stock(symbol)
    ib.qualifyContracts(contract)
    price = ib_svc.market_price(ib, contract)

    settings = ta.load_settings()
    guard = ib_svc.RiskGuard()

    # ATR from the same daily history the rest of the project uses, so the
    # stop distance the UI proposes matches what paper_trader would propose.
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="6 M", barSizeSetting="1 day",
        whatToShow="TRADES", useRTH=True, formatDate=2,
    )
    from ib_async import util as ib_util
    df = ib_util.df(bars)
    if df is None or len(df) < 20:
        raise TradingError(
            f"Not enough daily history for {symbol} to compute ATR — "
            "cannot size a stop, so this entry is refused."
        )
    frame = df.rename(columns={"high": "High", "low": "Low", "close": "Close"})
    atr_val = float(ind.atr(frame).iloc[-1])
    if not atr_val or atr_val != atr_val:
        raise TradingError(f"ATR for {symbol} is unusable — entry refused.")

    auto_stop = (price - STOP_ATR_MULT * atr_val if action == "BUY"
                 else price + STOP_ATR_MULT * atr_val)
    stop = round(stop_price if stop_price else auto_stop, 2)
    entry = round(price * (1 + ENTRY_LIMIT_BUFFER if action == "BUY"
                           else 1 - ENTRY_LIMIT_BUFFER), 2)

    net_liq = paper_trader.get_net_liquidation_usd(ib)
    auto_qty = paper_trader.size_position(net_liq, price, atr_val, settings, guard)
    qty = int(quantity) if quantity else auto_qty

    if qty <= 0:
        raise TradingError(
            f"Computed size is 0 for {symbol}. Risk budget "
            f"{settings.get('risk_pct_per_trade', 2.0)}% of "
            f"${net_liq:,.0f} divided by a {STOP_ATR_MULT}xATR stop distance of "
            f"{STOP_ATR_MULT * atr_val:,.2f} leaves nothing to buy."
        )

    # Sanity-check the stop side before RiskGuard, so the message is about
    # the actual mistake rather than a generic rejection.
    try:
        ib_svc._validate_bracket(action, entry, stop)
    except Exception as exc:                            # noqa: BLE001
        raise TradingError(str(exc)) from exc

    ok, reason = guard.check(ib, contract, qty, entry, has_stop=True, opening=True)
    risk = qty * abs(entry - stop)

    return {
        "symbol": symbol,
        "action": action,
        "quantity": qty,
        "autoQuantity": auto_qty,
        "quantitySource": "manual" if quantity else "auto (risk budget / 2xATR)",
        "marketPrice": price,
        "entryLimit": entry,
        "stopPrice": stop,
        "stopSource": "manual" if stop_price else f"{STOP_ATR_MULT}x ATR({atr_val:.2f})",
        "atr": atr_val,
        "notional": qty * entry,
        "riskIfStopped": risk,
        "riskPctOfEquity": risk / net_liq * 100 if net_liq else None,
        "netLiquidationUsd": net_liq,
        "parentTif": "DAY",
        "stopTif": "GTC",
        "allowed": ok,
        "reason": reason,
        "steps": [
            f"{action} LMT {qty} {symbol} @ {entry:,.2f}, TIF=DAY "
            "(an entry priced off today's close should expire with the session).",
            f"Attached STP @ {stop:,.2f}, TIF=GTC explicitly — the stop must "
            "outlive the day.",
            "After a terminal status, the covering GTC stop is verified; if "
            "one isn't live, UNPROTECTED is journalled and texted.",
        ],
    }


def do_bracket(ib, symbol: str, action: str, quantity: float,
               entry_limit: float, stop_price: float) -> dict:
    contract = ib_svc.stock(symbol)
    trades = ib_svc.place_bracket_order(
        ib, contract, quantity, action, entry_limit, stop_price,
    )
    if trades is None:
        raise TradingError(
            "RiskGuard blocked the order. See the BLOCKED row in "
            "trade_journal.csv for which limit and why."
        )
    parent = trades[0]
    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "entryLimit": entry_limit,
        "stopPrice": stop_price,
        "status": parent.orderStatus.status,
        "filled": float(parent.orderStatus.filled or 0),
        "avgFillPrice": float(parent.orderStatus.avgFillPrice or 0) or None,
        "legs": [
            {
                "orderId": t.order.orderId,
                "type": t.order.orderType,
                "tif": t.order.tif,
                "status": t.orderStatus.status,
            }
            for t in trades
        ],
    }


# --------------------------------------------------------- cancel one order

def build_cancel_preview(ib, order_id: int) -> dict:
    for trade in ib.openTrades():
        if trade.order.orderId == order_id:
            is_stop = trade.order.orderType in ib_svc.STOP_ORDER_TYPES
            return {
                "orderId": order_id,
                "symbol": getattr(trade.contract, "symbol", ""),
                "action": trade.order.action,
                "orderType": trade.order.orderType,
                "quantity": float(trade.order.totalQuantity or 0),
                "tif": trade.order.tif,
                "stopPrice": float(trade.order.auxPrice or 0) or None,
                "status": trade.orderStatus.status,
                "isStop": is_stop,
                "allowed": True,
                "reason": "ok",
                "steps": [
                    f"Cancel order {order_id}.",
                    *(["This is a STOP order. Cancelling it leaves the "
                       "position unprotected until you place another one."]
                      if is_stop else []),
                ],
            }
    raise TradingError(
        f"No open order with id {order_id}. It may have filled or been "
        "cancelled already — refresh the orders list."
    )


def do_cancel(ib, order_id: int) -> dict:
    for trade in ib.openTrades():
        if trade.order.orderId == order_id:
            ib_svc.journal("SUBMIT", trade.contract, trade.order.action,
                           float(trade.order.totalQuantity or 0),
                           status="cancel requested",
                           detail=f"cancel order {order_id} from web UI")
            ib.cancelOrder(trade.order)
            ib.sleep(1.5)
            ib_svc.journal("RESULT", trade.contract, trade.order.action,
                           float(trade.order.totalQuantity or 0),
                           status=trade.orderStatus.status,
                           detail=f"cancel order {order_id}")
            return {
                "orderId": order_id,
                "status": trade.orderStatus.status,
                "symbol": getattr(trade.contract, "symbol", ""),
            }
    raise TradingError(f"Order {order_id} is no longer open.")


def make_preview(kind: str, symbol: str, payload: dict) -> Preview:
    preview = Preview(
        token=secrets.token_urlsafe(16),
        kind=kind,
        symbol=symbol,
        created_at=time.time(),
        payload=payload,
        allowed=bool(payload.get("allowed")),
        reason=str(payload.get("reason", "")),
    )
    return _store(preview)
