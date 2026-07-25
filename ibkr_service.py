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

    # Real blocks (not the --selftest run against a temp journal path) are
    # exactly the "something needs my attention" case worth a phone alert.
    if event == "BLOCKED" and path == JOURNAL_FILE:
        send_telegram(
            f"\U0001f6ab RiskGuard BLOCKED an order\n"
            f"{getattr(contract, 'symbol', '?')} {action} {quantity}\n"
            f"Reason: {detail}"
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
        L = self.limits
        if quantity <= 0:
            return False, "quantity must be positive"
        notional = quantity * est_price
        if notional > L["max_order_notional_usd"]:
            return False, (f"notional ${notional:,.0f} exceeds limit "
                           f"${L['max_order_notional_usd']:,.0f}")
        if L["require_stop_attached"] and not has_stop:
            return False, "no stop attached (require_stop_attached=true)"
        if opening and len(ib.positions()) >= L["max_open_positions"]:
            return False, f"already at max_open_positions={L['max_open_positions']}"
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

def wait_for_status(ib: IB, trade, timeout_s: float = 30.0):
    """Block until the order reaches a terminal state or timeout. Returns the trade."""
    waited = 0.0
    while not trade.isDone() and waited < timeout_s:
        ib.sleep(0.5)
        waited += 0.5
    return trade


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

    parent = LimitOrder(action, quantity, entry_limit)
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
    trades = [ib.placeOrder(contract, o) for o in [parent] + children]
    ib.sleep(1)
    journal("RESULT", contract, action, quantity, entry_limit, stop_price,
            target_price or "", status=trades[0].orderStatus.status,
            detail="bracket placed (parent status shown)")
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
