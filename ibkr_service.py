"""
ibkr_service.py

Connection + multi-asset data/execution layer for Interactive Brokers,
built for a 15-minute-interval trading loop across stocks, forex,
futures (incl. commodities), and crypto — one IBKR account/API.

Phase 2 hardening: this is no longer bare plumbing. Every order path
goes through, in order:
  1. Paper-account verification (refuses non-paper accounts by default)
  2. RiskGuard — limits loaded from risk_limits.json, enforced in code:
     max order notional, max open positions, daily-loss circuit breaker,
     and stop-required (bare orders without a stop are refused unless
     explicitly overridden)
  3. Trade journal — every attempt, block, submission and fill is
     appended to trade_journal.csv (the audit trail)

IMPORTANT
---------
- Requires TWS or IB Gateway RUNNING LOCALLY with the API enabled
  (Edit -> Global Configuration -> API -> Settings).
- Paper ports: 7497 (TWS) / 4002 (Gateway). connect() refuses live
  ports (7496/4001) unless allow_live=True is passed explicitly.
- Install: pip install ib_async pandas
- Offline self-test (no TWS needed): python3 ibkr_service.py --selftest
- Connected smoke test:              python3 ibkr_service.py
"""

import csv
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from ib_async import (IB, Stock, Forex, Future, Crypto,
                      MarketOrder, LimitOrder, StopOrder, util)

PAPER_PORT_TWS = 7497
PAPER_PORT_GATEWAY = 4002
LIVE_PORT_TWS = 7496
LIVE_PORT_GATEWAY = 4001

BASE_DIR = Path(__file__).parent
RISK_FILE = BASE_DIR / "risk_limits.json"
JOURNAL_FILE = BASE_DIR / "trade_journal.csv"

# Best-effort phone alert on RiskGuard blocks (see journal() below). Never
# lets a notification problem affect order logic — falls back to a no-op
# if TelegramBot isn't configured yet.
sys.path.insert(0, str(BASE_DIR / "TelegramBot"))
try:
    from notify import send_telegram
except Exception:
    def send_telegram(*args, **kwargs):  # pragma: no cover - best-effort only
        return False

DEFAULT_LIMITS = {
    "max_order_notional_usd": 5000,
    "max_open_positions": 5,
    "max_daily_loss_usd": 300,
    "require_stop_attached": True,
}


def connect(port: int = PAPER_PORT_TWS, host: str = "127.0.0.1", client_id: int = 1,
            allow_live: bool = False, readonly: bool = False) -> IB:
    """Connect to a running TWS / IB Gateway instance. Defaults to paper trading.

    Live ports are refused unless allow_live=True is passed explicitly —
    a deliberate speed bump so automated code can't silently touch real
    money because of a config typo.

    readonly=True asks TWS/Gateway itself to reject any order placement on
    this connection. Callers that only inspect state (position checks,
    --dry-run proposals) should pass it: without it, "this code doesn't
    place orders" is a property of the code, which a later edit can quietly
    undo. With it, the guarantee is enforced at the other end of the socket
    — the same "rules in code, not in convention" reasoning as the live-port
    refusal above and RiskGuard below. Default stays False so every existing
    trading path is unchanged."""
    if port in (LIVE_PORT_TWS, LIVE_PORT_GATEWAY) and not allow_live:
        raise RuntimeError(
            f"Port {port} is a LIVE trading port. Pass allow_live=True only "
            "after your strategy has months of paper-trading evidence behind it."
        )
    ib = IB()
    ib.connect(host, port, clientId=client_id, readonly=readonly)
    return ib


def verify_paper_account(ib: IB, allow_live: bool = False) -> str:
    """IBKR paper account ids start with 'D' (e.g. DU1234567). Refuse to
    proceed on anything else unless allow_live=True."""
    accounts = ib.managedAccounts()
    if not accounts:
        raise RuntimeError("No managed accounts visible — is the API logged in?")
    acct = accounts[0]
    if not acct.startswith("D") and not allow_live:
        raise RuntimeError(
            f"Account {acct} does not look like a paper account. Orders refused. "
            "Pass allow_live=True only when live trading has genuinely been earned."
        )
    return acct


# ---------------------------------------------------------------- contracts
# One unified Order type works across all of these once the contract
# is qualified — the main advantage of building a multi-asset bot on
# IBKR instead of stitching together separate broker APIs.

def stock(symbol: str, currency: str = "USD") -> Stock:
    return Stock(symbol, "SMART", currency)


def forex_pair(pair: str) -> Forex:
    return Forex(pair)  # e.g. "EURUSD"


def future(symbol: str, expiry: str, exchange: str) -> Future:
    """
    Any futures contract — index, commodity, whatever. expiry format
    "YYYYMM", e.g. "202612". Commodities are just futures with the
    right symbol/exchange, e.g.:
      future("MGC", "202612", "COMEX")  - Micro Gold, 10 troy oz (the
                                          retail-appropriate size)
      future("GC",  "202612", "COMEX")  - Gold, 100 troy oz
      future("SI",  "202612", "COMEX")  - Silver
      future("CL",  "202612", "NYMEX")  - Crude Oil
      future("ES",  "202612", "CME")    - S&P 500 E-mini
    For unleveraged commodity exposure, stock("GLD") / stock("IAU")
    work instead (gold ETFs, no expiry to manage).
    """
    return Future(symbol, expiry, exchange)


def crypto(symbol: str, currency: str = "USD") -> Crypto:
    return Crypto(symbol, "PAXOS", currency)  # IBKR crypto trades via Paxos


# ---------------------------------------------------------------- data

def _what_to_show(contract) -> str:
    """IBKR requires different historical-data types per asset class:
    forex has no trades tape (use MIDPOINT), crypto uses AGGTRADES."""
    if isinstance(contract, Forex):
        return "MIDPOINT"
    if isinstance(contract, Crypto):
        return "AGGTRADES"
    return "TRADES"


def get_15min_bars(ib: IB, contract, duration: str = "2 D"):
    """One-shot pull of recent 15-minute bars for a contract, as a DataFrame."""
    ib.qualifyContracts(contract)
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr=duration,
        barSizeSetting="15 mins", whatToShow=_what_to_show(contract), useRTH=False,
    )
    return util.df(bars)


def stream_15min_bars(ib: IB, contract, on_update):
    """
    Subscribe to a live-updating 15-minute bar series. `on_update` is
    called with the updated bar list every time a new bar closes (or
    the current bar updates). Keep the ib.run() event loop alive to
    receive updates.
    """
    ib.qualifyContracts(contract)
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="1 D",
        barSizeSetting="15 mins", whatToShow=_what_to_show(contract), useRTH=False,
        keepUpToDate=True,
    )
    bars.updateEvent += lambda b, has_new_bar: on_update(b, has_new_bar)
    return bars


def market_price(ib: IB, contract) -> float:
    """Best-effort current price (for notional risk checks)."""
    ib.qualifyContracts(contract)
    [ticker] = ib.reqTickers(contract)
    px = ticker.marketPrice()
    if px is None or px != px:  # NaN check
        px = ticker.close
    if px is None or px != px or px <= 0:
        raise RuntimeError(f"No usable price for {contract.symbol} — cannot risk-check notional.")
    return float(px)


# ---------------------------------------------------------------- journal

JOURNAL_COLUMNS = ["timestamp", "event", "symbol", "sec_type", "action", "quantity",
                   "price", "stop", "target", "status", "detail"]


def journal(event: str, contract=None, action: str = "", quantity: float = "",
            price="", stop="", target="", status: str = "", detail: str = "",
            path: Path = JOURNAL_FILE) -> None:
    """Append one row to the trade journal. Every attempt, block and fill
    goes here — if it isn't in the journal, it didn't happen."""
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(JOURNAL_COLUMNS)
        w.writerow([datetime.now().isoformat(timespec="seconds"), event,
                    getattr(contract, "symbol", ""), getattr(contract, "secType", ""),
                    action, quantity, price, stop, target, status, detail])

    # Real events (not the --selftest run against a temp journal path) that are
    # exactly the "something needs my attention" case worth a phone alert.
    if path != JOURNAL_FILE:
        return
    symbol = getattr(contract, "symbol", "?")
    if event == "BLOCKED":
        send_telegram(
            f"\U0001f6ab RiskGuard BLOCKED an order\n"
            f"{symbol} {action} {quantity}\n"
            f"Reason: {detail}"
        )
    elif event == "UNPROTECTED":
        # Naked exposure. Nothing auto-fixes this — it needs a human now.
        send_telegram(
            f"\U0001f6a8 UNPROTECTED POSITION — no live GTC stop\n"
            f"{symbol}: {quantity} shares filled\n"
            f"{detail}\n"
            f"Place a GTC stop manually, and check Gateway's Order Presets "
            f"(error 10349 forces DAY TIF and cancels the stop leg)."
        )


# ---------------------------------------------------------------- risk guard

class RiskGuard:
    """Code-enforced limits. The strategy proposes; this decides if the
    order is even allowed to reach the broker. Limits live in
    risk_limits.json so changing them is an explicit, visible act."""

    def __init__(self, limits_path: Path = RISK_FILE):
        if limits_path.exists():
            self.limits = {**DEFAULT_LIMITS, **json.loads(limits_path.read_text())}
        else:
            limits_path.write_text(json.dumps(DEFAULT_LIMITS, indent=2))
            self.limits = dict(DEFAULT_LIMITS)

    def check(self, ib: IB, contract, quantity: float, est_price: float,
              has_stop: bool, opening: bool = True) -> tuple[bool, str]:
        """Decide whether an order may reach the broker.

        Every limit here caps NEW EXPOSURE, so the exposure-sized ones are
        gated on `opening` and never apply to an order that flattens or
        reduces a position. A risk limit must not be able to trap you in a
        position — blocking an exit raises risk, which is the opposite of
        the job.

        That is not hypothetical: on 2026-07-27 the notional cap blocked the
        exits for both open positions. AAPL was bought at 15 x 328.04 =
        $4,921 and JNJ at 19 x 249.98 = $4,750, both under the then-$5,000
        cap; they appreciated to $5,007 and $5,005 and became un-exitable
        through paper_trader. The cap trapped winners specifically, and the
        rebalance silently held instead of rotating. `opening=False` was
        being passed correctly all along — only max_open_positions honoured
        it.

        The daily-loss breaker is gated for the same reason, and it matters
        more there: after a bad day you would otherwise be unable to close
        out of anything, which is exactly when getting out matters most.

        `require_stop_attached` is deliberately NOT gated — it is checked
        for openings only in the sense that closers pass allow_no_stop=True,
        and an exit needs no stop of its own. Do not weaken it (project
        rule 2).
        """
        L = self.limits
        if quantity <= 0:
            return False, "quantity must be positive"
        notional = quantity * est_price
        if opening and notional > L["max_order_notional_usd"]:
            return False, (f"notional ${notional:,.0f} exceeds limit "
                           f"${L['max_order_notional_usd']:,.0f}")
        if L["require_stop_attached"] and not has_stop:
            return False, "no stop attached (require_stop_attached=true)"
        if opening and len(ib.positions()) >= L["max_open_positions"]:
            return False, f"already at max_open_positions={L['max_open_positions']}"
        if opening:
            pnl = daily_realized_pnl(ib)
            if pnl is not None and pnl <= -abs(L["max_daily_loss_usd"]):
                return False, (f"daily loss circuit breaker: realized {pnl:,.0f} <= "
                               f"-{L['max_daily_loss_usd']:,.0f}. Done for the day.")
        return True, "ok"


def daily_realized_pnl(ib: IB):
    """Best-effort realized P&L today from IBKR account values. Returns None
    if the tag isn't available (check is then skipped, with a journal note)."""
    try:
        for v in ib.accountValues():
            if v.tag == "RealizedPnL" and v.currency == "USD":
                return float(v.value)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- execution

def market_is_open(now_ny=None) -> bool:
    """Weekday + 9:30-16:00 America/New_York.

    Authoritative in NY time via zoneinfo, never host time — this machine runs
    EEST/EET, ~7h ahead of US Eastern year-round. No market-holiday calendar:
    a holiday just means a caller harmlessly attempts against stale/empty data
    and fails gracefully, not a safety issue.

    Lives here, in the broker layer, so both the human-approved path
    (paper_trader) and the unattended one (autotrade_runner) ask the same
    question of the same code. autotrade_runner owned this first; paper_trader
    had no check at all and would place orders into a closed market.
    """
    now_ny = now_ny or datetime.now(ZoneInfo("America/New_York"))
    if now_ny.weekday() >= 5:  # Saturday/Sunday
        return False
    open_t = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now_ny <= close_t


def wait_for_status(ib: IB, trade, timeout_s: float = 30.0):
    """Block until the order reaches a terminal state or timeout. Returns the trade."""
    waited = 0.0
    while not trade.isDone() and waited < timeout_s:
        ib.sleep(0.5)
        waited += 0.5
    return trade


# Order types that actually protect a position. STP LMT is included because
# IBKR accepts it as a bracket exit leg, even though we never place one.
STOP_ORDER_TYPES = ("STP", "STP LMT", "TRAIL", "TRAIL LIMIT")

# An order in one of these states is still working at IBKR. Anything else
# (Cancelled, ApiCancelled, Inactive, Filled) protects nothing from here on.
LIVE_ORDER_STATUS = ("PendingSubmit", "PreSubmitted", "Submitted", "ApiPending")


def stop_protection_status(stops: list[dict], filled_qty: float) -> tuple[bool, str]:
    """Is `filled_qty` shares covered by live, GTC stop orders? Pure function.

    `stops` are plain dicts ({"qty", "tif", "status"}) so this stays testable
    offline — see `_open_stops_for()` for the live-Trade adapter.

    "Present" is not the bar; GTC is (rule 2 + the 2026-07-21 incident where a
    DAY stop looked fine for hours and then silently expired at the close,
    leaving three positions unprotected overnight). A DAY stop here counts as
    NO protection, deliberately.
    """
    live = [s for s in stops if s.get("status") in LIVE_ORDER_STATUS]
    if not live:
        return False, f"no live stop order found for {filled_qty:g} shares"

    gtc = [s for s in live if s.get("tif") == "GTC"]
    if not gtc:
        tifs = ", ".join(sorted({str(s.get("tif")) for s in live}))
        return False, (f"stop order(s) exist but NONE are GTC (tif={tifs}) — a DAY stop "
                       f"expires at the session close and stops protecting anything")

    covered = sum(float(s.get("qty", 0)) for s in gtc)
    # Tolerance for float noise on fractional quantities only.
    if covered + 1e-6 < filled_qty:
        return False, (f"GTC stop covers only {covered:g} of {filled_qty:g} shares — "
                       f"{filled_qty - covered:g} shares unprotected")
    return True, f"GTC stop covers {covered:g}/{filled_qty:g} shares"


def _open_stops_for(trades, symbol: str, exit_action: str) -> list[dict]:
    """Reduce live Trade objects to the plain dicts stop_protection_status wants."""
    out = []
    for t in trades:
        order = t.order
        if getattr(t.contract, "symbol", None) != symbol:
            continue
        if order.orderType not in STOP_ORDER_TYPES or order.action != exit_action:
            continue
        out.append({"qty": float(order.totalQuantity),
                    "tif": order.tif,
                    "status": t.orderStatus.status})
    return out


def verify_stop_protection(ib: IB, contract, exit_action: str, filled_qty: float,
                           settle_s: float = 1.5) -> tuple[bool, str]:
    """Confirm a filled position is covered by a live full-size GTC stop.

    Journals `UNPROTECTED` and fires a phone alert when it isn't. Deliberately
    does NOT place a replacement stop — auto-remediation is a separate decision
    and silently fixing this would hide how often it happens.

    This exists because a bracket can half-survive: IBKR error 10349 (an Order
    Preset forcing DAY TIF) cancels legs individually, so the parent can fill
    while its stop is rejected, leaving naked exposure that nothing detects.

    Returns (ok, reason). Raises `OpenOrderStateUnknown` if IBKR never answers
    — see below for why that is not the same as "unprotected".
    """
    # An UNANSWERED open-orders request returns an EMPTY list, which is
    # indistinguishable from "this position genuinely has no stop" unless we
    # separate the two. Getting this wrong is how the 2026-07-25 phantom
    # liquidation happened with ib.positions(); the fix there was to let a
    # timeout RAISE rather than degrade to []. Same rule here.
    #
    # Not hypothetical: on 2026-07-29 a wedged Gateway answered position
    # requests normally while reqAllOpenOrders timed out at 30s AND 45s. The
    # old code would have read that as four naked positions and fired four
    # false UNPROTECTED alerts.
    try:
        ib.run(ib.reqAllOpenOrdersAsync(), timeout=OPEN_ORDERS_TIMEOUT)
    except Exception as e:
        raise OpenOrderStateUnknown(
            f"IBKR did not answer reqAllOpenOrders within {OPEN_ORDERS_TIMEOUT}s "
            f"({type(e).__name__}) — open-order state for {contract.symbol} is "
            f"UNKNOWN. This is NOT evidence that the stop is missing. Retry, or "
            f"restart Gateway if it keeps refusing."
        ) from e
    ib.sleep(settle_s)
    stops = _open_stops_for(ib.openTrades(), contract.symbol, exit_action)
    ok, reason = stop_protection_status(stops, filled_qty)
    if not ok:
        journal("UNPROTECTED", contract, exit_action, filled_qty,
                status="NO GTC STOP", detail=reason)
        print(f"UNPROTECTED POSITION: {contract.symbol} — {reason}", file=sys.stderr)
    return ok, reason


def place_market_order(ib: IB, contract, quantity: float, action: str = "BUY",
                       guard: RiskGuard | None = None, allow_no_stop: bool = False,
                       allow_live: bool = False, opening: bool = True):
    """
    Bare market order — refused by default unless a stop will be managed
    elsewhere and you pass allow_no_stop=True. Prefer place_bracket_order.
    Pass opening=False when this order flattens/reduces an existing
    position (e.g. a rotation exit) so RiskGuard's max_open_positions check
    — which only makes sense for new exposure — isn't applied to a close.
    Returns the Trade object, or None if blocked by the risk guard.
    """
    verify_paper_account(ib, allow_live=allow_live)
    guard = guard or RiskGuard()
    ib.qualifyContracts(contract)
    px = market_price(ib, contract)
    ok, reason = guard.check(ib, contract, quantity, px, has_stop=allow_no_stop, opening=opening)
    if not ok:
        journal("BLOCKED", contract, action, quantity, px, status="blocked", detail=reason)
        print(f"ORDER BLOCKED: {reason}", file=sys.stderr)
        return None
    journal("SUBMIT", contract, action, quantity, px, status="submitted", detail="market order")
    trade = ib.placeOrder(contract, MarketOrder(action, quantity))
    wait_for_status(ib, trade)
    journal("RESULT", contract, action, quantity,
            trade.orderStatus.avgFillPrice or px, status=trade.orderStatus.status,
            detail=f"filled {trade.orderStatus.filled}/{quantity}")
    return trade


OPEN_ORDERS_TIMEOUT = 30.0


class OpenOrderStateUnknown(RuntimeError):
    """IBKR never answered an open-orders request.

    Deliberately NOT a subclass of anything that reads as "no stop found".
    An unanswered request is missing information, not a negative answer, and
    conflating the two is what manufactures false alarms.
    """


class OrderErrorCollector:
    """Captures IBKR's error/warning messages for a specific set of order IDs.

    IBKR explains order rejections out-of-band, on the error channel — not in
    orderStatus, which only ever says `Cancelled`. Without this, the single
    most useful sentence ("Order TIF was set to DAY based on order preset",
    error 10349) exists nowhere afterwards: not in the journal, not in the
    Gateway logs on disk (they're encrypted), not anywhere a later session can
    read it. That happened on 2026-07-27 and cost a day of guessing at which
    of several plausible causes had cancelled an entry.

    Usage:
        with OrderErrorCollector(ib, order_ids) as errs:
            ...place orders, wait...
        errs.summary()   # "" if IBKR said nothing
    """

    # IBKR sends plenty of routine chatter (2104 "market data farm connection
    # is OK", 2158, etc.). These are not order problems and would drown the
    # journal detail column.
    IGNORED_CODES = frozenset({1100, 1101, 1102, 2103, 2104, 2105, 2106, 2107,
                               2108, 2119, 2158, 2168, 2169})

    def __init__(self, ib: IB, order_ids):
        self.ib = ib
        self.order_ids = set(order_ids)
        self.errors = []

    def _on_error(self, reqId, errorCode, errorString, contract=None):
        if errorCode in self.IGNORED_CODES:
            return
        # reqId is the orderId for order-related messages; -1 is system-wide.
        if reqId in self.order_ids:
            self.errors.append((reqId, errorCode, str(errorString).strip()))

    def __enter__(self):
        self.ib.errorEvent += self._on_error
        return self

    def __exit__(self, *exc):
        self.ib.errorEvent -= self._on_error
        return False

    def summary(self) -> str:
        """One-line, journal-safe rendering of everything IBKR said."""
        seen, out = set(), []
        for _, code, msg in self.errors:
            if (code, msg) in seen:
                continue
            seen.add((code, msg))
            out.append(f"IBKR {code}: {msg}")
        return "; ".join(out)

    def codes(self) -> set:
        return {code for _, code, _ in self.errors}


def _validate_bracket(action: str, entry_limit: float, stop_price: float) -> None:
    if action not in ("BUY", "SELL"):
        raise ValueError("action must be BUY or SELL")
    if action == "BUY" and not stop_price < entry_limit:
        raise ValueError("For a BUY, stop_price must be below entry_limit.")
    if action == "SELL" and not stop_price > entry_limit:
        raise ValueError("For a SELL, stop_price must be above entry_limit.")


def place_bracket_order(ib: IB, contract, quantity: float, action: str,
                        entry_limit: float, stop_price: float,
                        target_price: float | None = None,
                        guard: RiskGuard | None = None, allow_live: bool = False):
    """
    The default way to enter a position: limit entry + stop-loss (+ optional
    take-profit) placed atomically, so no position can exist without its stop.
    Returns the list of Trade objects, or None if blocked.
    """
    _validate_bracket(action, entry_limit, stop_price)
    verify_paper_account(ib, allow_live=allow_live)
    guard = guard or RiskGuard()
    ib.qualifyContracts(contract)
    exit_action = "SELL" if action == "BUY" else "BUY"

    ok, reason = guard.check(ib, contract, quantity, entry_limit, has_stop=True)
    if not ok:
        journal("BLOCKED", contract, action, quantity, entry_limit,
                stop_price, target_price or "", status="blocked", detail=reason)
        print(f"ORDER BLOCKED: {reason}", file=sys.stderr)
        return None

    # The PARENT's tif must be explicit too, and this is not cosmetic. Leaving
    # it unset is what triggered IBKR error 10349 "Order TIF was set to DAY
    # based on order preset" — the preset was filling in the blank we left,
    # and announcing it. Proved by probe 2026-07-28: the error's reqId is
    # always the parent's, and the stop leg (which always carried an explicit
    # tif="GTC") kept GTC at IBKR throughout. Setting it ourselves leaves the
    # preset nothing to override.
    #
    # DAY is the right value HERE, unlike the stop below: an entry limit is
    # priced off today's close and should expire with the session rather than
    # fire days later at a price the signal never justified. It is the STOP
    # that must outlive the day (see the GTC comment below).
    parent = LimitOrder(action, quantity, entry_limit, tif="DAY")
    parent.orderId = ib.client.getReqId()
    parent.transmit = False
    children = []
    if target_price is not None:
        tp = LimitOrder(exit_action, quantity, target_price, tif="GTC")
        tp.orderId = ib.client.getReqId()
        tp.parentId = parent.orderId
        tp.transmit = False
        children.append(tp)
    # GTC (Good-Til-Cancelled): a DAY stop (IBKR's default) expires at the
    # end of the trading session, silently leaving a multi-day swing
    # position with no protective stop at all. The stop must outlive the
    # day it was placed on.
    sl = StopOrder(exit_action, quantity, stop_price, tif="GTC")
    sl.orderId = ib.client.getReqId()
    sl.parentId = parent.orderId
    sl.transmit = True  # last child transmits the whole bracket atomically
    children.append(sl)

    journal("SUBMIT", contract, action, quantity, entry_limit, stop_price,
            target_price or "", status="submitted", detail="bracket order")

    all_orders = [parent] + children
    with OrderErrorCollector(ib, [o.orderId for o in all_orders]) as errs:
        trades = [ib.placeOrder(contract, o) for o in all_orders]

        # Wait for the parent to actually resolve. The old code slept a flat 1s
        # and journalled whatever status the parent happened to hold at that
        # instant — a snapshot, not an outcome. On 2026-07-27 that recorded two
        # brackets that went on to fill (AMZN 21, DIS 52) as "Cancelled", and
        # the account ran two positions ahead of the journal for a full day.
        parent_trade = trades[0]
        wait_for_status(ib, parent_trade)

    st = parent_trade.orderStatus
    filled = float(st.filled or 0)
    detail = f"bracket parent: filled {filled:g}/{quantity:g}"
    if errs.summary():
        # Whatever IBKR said about WHY goes in the journal, verbatim. A bare
        # "Cancelled" row is what made 10349 take a day to identify.
        detail += f" | {errs.summary()}"
    journal("RESULT", contract, action, quantity, st.avgFillPrice or entry_limit,
            stop_price, target_price or "", status=st.status, detail=detail)

    if 10349 in errs.codes():
        print("\n*** IBKR error 10349: an Order Preset forced DAY TIF and overrode the "
              "explicit GTC on this bracket's stop leg. Fix in Gateway/TWS Global "
              "Configuration -> Presets. ***", file=sys.stderr)

    # A filled parent whose stop leg didn't survive is a rule-2 violation, and
    # nothing downstream would have caught it.
    if filled > 0:
        try:
            verify_stop_protection(ib, contract, exit_action, filled)
        except OpenOrderStateUnknown as e:
            # We hold a real position and cannot confirm it is protected. That
            # is not the same as knowing it's naked, and must not be journalled
            # as UNPROTECTED — but it absolutely still needs a human, because
            # the one thing we cannot do is assume it's fine.
            journal("PROTECTION_UNKNOWN", contract, exit_action, filled,
                    status="unverified", detail=str(e))
            send_telegram(
                f"⚠️ Cannot verify stop protection\n"
                f"{contract.symbol}: {filled:g} shares FILLED\n"
                f"IBKR did not answer the open-orders request, so we do not know "
                f"whether the stop is live. It may well be fine.\n"
                f"Check the position manually and restart Gateway if it keeps "
                f"refusing."
            )
            print(f"PROTECTION UNKNOWN: {contract.symbol} — {e}", file=sys.stderr)
    return trades


# ---------------------------------------------------------------- self-test

def _selftest() -> int:
    """Offline checks of everything that doesn't need a connection."""
    import tempfile
    failures = []

    def check(name, cond):
        print(("  PASS  " if cond else "  FAIL  ") + name)
        if not cond:
            failures.append(name)

    # contracts + data-type routing
    check("stock builder", stock("AAPL").secType == "STK")
    check("forex builder", forex_pair("EURUSD").secType == "CASH")
    check("future builder", future("MGC", "202612", "COMEX").secType == "FUT")
    check("crypto builder", crypto("BTC").secType == "CRYPTO")
    check("forex uses MIDPOINT", _what_to_show(forex_pair("EURUSD")) == "MIDPOINT")
    check("crypto uses AGGTRADES", _what_to_show(crypto("BTC")) == "AGGTRADES")
    check("stock uses TRADES", _what_to_show(stock("AAPL")) == "TRADES")

    # risk guard with a fake IB
    class FakeIB:
        def __init__(self, n_pos=0):
            self._n = n_pos
        def positions(self):
            return [object()] * self._n
        def accountValues(self):
            return []

    with tempfile.TemporaryDirectory() as td:
        g = RiskGuard(Path(td) / "limits.json")
        ok, r = g.check(FakeIB(), stock("AAPL"), 10, 100.0, has_stop=True)
        check("guard allows sane order", ok)
        ok, r = g.check(FakeIB(), stock("AAPL"), 1000, 100.0, has_stop=True)
        check("guard blocks oversized notional", not ok and "notional" in r)
        ok, r = g.check(FakeIB(), stock("AAPL"), 10, 100.0, has_stop=False)
        check("guard blocks stopless order", not ok and "stop" in r)
        ok, r = g.check(FakeIB(n_pos=5), stock("AAPL"), 10, 100.0, has_stop=True)
        check("guard blocks at max positions", not ok and "max_open_positions" in r)
        ok, r = g.check(FakeIB(), stock("AAPL"), 0, 100.0, has_stop=True)
        check("guard blocks zero quantity", not ok)

        # A limit must cap new exposure, never trap you in a position. On
        # 2026-07-27 an oversized-notional block hit the EXITS for both open
        # positions (bought under the cap, appreciated past it) and the
        # rebalance silently held instead of rotating.
        ok, r = g.check(FakeIB(), stock("AAPL"), 1000, 100.0,
                        has_stop=True, opening=False)
        check("guard allows oversized EXIT (closing reduces risk)", ok)
        ok, r = g.check(FakeIB(n_pos=5), stock("AAPL"), 10, 100.0,
                        has_stop=True, opening=False)
        check("guard allows EXIT at max positions", ok)

        # journal roundtrip
        jp = Path(td) / "journal.csv"
        journal("SUBMIT", stock("AAPL"), "BUY", 10, 100.0, 95.0, 120.0,
                status="submitted", detail="selftest", path=jp)
        journal("BLOCKED", stock("AAPL"), "BUY", 999, 100.0,
                status="blocked", detail="selftest block", path=jp)
        rows = list(csv.reader(open(jp)))
        check("journal writes header + rows", len(rows) == 3 and rows[0] == JOURNAL_COLUMNS)
        check("journal records events", rows[1][1] == "SUBMIT" and rows[2][1] == "BLOCKED")

    # bracket validation (the real function, no connection needed)
    def raises(fn):
        try:
            fn(); return False
        except ValueError:
            return True
    check("bracket rejects BUY stop above entry", raises(lambda: _validate_bracket("BUY", 100.0, 105.0)))
    check("bracket rejects SELL stop below entry", raises(lambda: _validate_bracket("SELL", 100.0, 95.0)))
    check("bracket rejects bad action", raises(lambda: _validate_bracket("HOLD", 100.0, 95.0)))
    ok_valid = not raises(lambda: _validate_bracket("BUY", 100.0, 95.0))
    check("bracket accepts valid BUY", ok_valid)

    # stop-protection predicate — the check that a filled bracket kept its stop
    def stp(qty, tif="GTC", status="PreSubmitted"):
        return {"qty": qty, "tif": tif, "status": status}

    ok, r = stop_protection_status([stp(15)], 15)
    check("protection: full-size GTC stop passes", ok)
    ok, r = stop_protection_status([stp(10), stp(5)], 15)
    check("protection: two GTC stops sum to full size", ok)
    ok, r = stop_protection_status([], 15)
    check("protection: no stop at all fails", not ok and "no live stop" in r)
    ok, r = stop_protection_status([stp(15, tif="DAY")], 15)
    check("protection: DAY stop counts as unprotected", not ok and "GTC" in r)
    ok, r = stop_protection_status([stp(10)], 15)
    check("protection: partial cover fails", not ok and "unprotected" in r)
    ok, r = stop_protection_status([stp(15, status="Cancelled")], 15)
    check("protection: cancelled stop is not protection", not ok)
    ok, r = stop_protection_status([stp(15, status="Inactive")], 15)
    check("protection: inactive stop is not protection", not ok)
    # The exact 2026-07-27 shape: parent filled, stop leg killed by error 10349.
    ok, r = stop_protection_status([stp(21, status="Cancelled"), stp(21, tif="DAY")], 21)
    check("protection: 10349 shape (cancelled GTC + DAY survivor) fails", not ok)

    # the live-Trade adapter, with duck-typed stand-ins
    class FakeOrder:
        def __init__(self, action, otype, qty, tif):
            self.action, self.orderType, self.totalQuantity, self.tif = action, otype, qty, tif

    class FakeTrade:
        def __init__(self, sym, action, otype, qty, tif, status):
            self.contract = stock(sym)
            self.order = FakeOrder(action, otype, qty, tif)
            self.orderStatus = type("S", (), {"status": status})()

    live = [FakeTrade("AAPL", "SELL", "STP", 15, "GTC", "PreSubmitted"),
            FakeTrade("JNJ", "SELL", "STP", 19, "GTC", "PreSubmitted"),   # other symbol
            FakeTrade("AAPL", "BUY", "LMT", 15, "GTC", "Submitted")]      # not a stop
    found = _open_stops_for(live, "AAPL", "SELL")
    check("adapter picks only this symbol's stops", len(found) == 1 and found[0]["qty"] == 15)
    check("adapter output feeds the predicate", stop_protection_status(found, 15)[0])

    # IBKR error capture — the thing that made 10349 invisible for a day
    class FakeEvent:
        def __init__(self): self.handlers = []
        def __iadd__(self, h): self.handlers.append(h); return self
        def __isub__(self, h): self.handlers.remove(h); return self
        def emit(self, *a):
            for h in list(self.handlers):
                h(*a)

    class FakeErrIB:
        def __init__(self): self.errorEvent = FakeEvent()

    fib = FakeErrIB()
    with OrderErrorCollector(fib, [101, 102]) as errs:
        fib.errorEvent.emit(101, 10349, "Order TIF was set to DAY based on order preset", None)
        fib.errorEvent.emit(102, 2104, "Market data farm connection is OK", None)  # noise
        fib.errorEvent.emit(999, 201, "Order rejected - some other order", None)   # not ours
        fib.errorEvent.emit(101, 10349, "Order TIF was set to DAY based on order preset", None)
    check("error collector keeps order-preset error", 10349 in errs.codes())
    check("error collector drops routine chatter", 2104 not in errs.codes())
    check("error collector ignores other orders' errors", 201 not in errs.codes())
    check("error collector dedupes repeats", errs.summary().count("10349") == 1)
    check("error summary is journal-safe one-liner", "\n" not in errs.summary())
    check("collector unsubscribes on exit", not fib.errorEvent.handlers)
    with OrderErrorCollector(fib, [1]) as quiet:
        pass
    check("silent placement summarises to empty string", quiet.summary() == "")

    # An unanswered open-orders request must NOT read as "no stop found".
    # This is the distinction that stops a wedged Gateway manufacturing a
    # false naked-position alarm (observed for real 2026-07-29).
    class HangingIB:
        """Answers positions fine, never answers open orders — the real
        2026-07-29 Gateway behaviour."""
        def __init__(self):
            self.errorEvent = FakeEvent()
        def reqAllOpenOrdersAsync(self):
            return None
        def run(self, *a, **k):
            raise TimeoutError("no response")
        def sleep(self, *a):
            pass
        def openTrades(self):
            return []

    try:
        verify_stop_protection(HangingIB(), stock("AAPL"), "SELL", 15)
        check("unanswered open-orders request raises, not 'unprotected'", False)
    except OpenOrderStateUnknown as e:
        check("unanswered open-orders request raises OpenOrderStateUnknown", True)
        check("the raise says it is NOT evidence of a missing stop",
              "NOT evidence" in str(e))
    except Exception:
        check("unanswered open-orders request raises OpenOrderStateUnknown", False)

    check("OpenOrderStateUnknown is not confusable with a normal failure",
          issubclass(OpenOrderStateUnknown, RuntimeError))

    # UNPROTECTED must be journallable like any other event
    with tempfile.TemporaryDirectory() as td:
        jp = Path(td) / "j.csv"
        journal("UNPROTECTED", stock("AAPL"), "SELL", 15,
                status="NO GTC STOP", detail="selftest", path=jp)
        rows = list(csv.reader(open(jp)))
        check("journal records UNPROTECTED", len(rows) == 2 and rows[1][1] == "UNPROTECTED")

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILURES: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())

    # Connected smoke test against the PAPER account — confirms Gateway/TWS
    # is reachable, verifies the account is paper, and pulls one asset from
    # each class. Places NO orders.
    ib = connect()
    acct = verify_paper_account(ib)
    print(f"Connected. Paper account verified: {acct}")

    for c in [stock("AAPL"), forex_pair("EURUSD"), crypto("BTC")]:
        df = get_15min_bars(ib, c, duration="1 D")
        print(f"\n{c.symbol} — last 5 bars (15min):")
        print(df.tail() if df is not None else "no data")

    ib.disconnect()
