"""
ibkr_service.py

Connection + multi-asset data/execution layer for Interactive Brokers,
built for a 15-minute-interval trading loop across stocks, forex,
futures, and crypto — all through one IBKR account/API.

IMPORTANT
---------
- Requires TWS or IB Gateway RUNNING LOCALLY with the API enabled
  (Edit -> Global Configuration -> API -> Settings in TWS/Gateway).
- Defaults to the PAPER TRADING port (7497 for TWS, 4002 for IB
  Gateway). Do not point this at the live port (7496 / 4001) until
  you've run your actual strategy logic on paper and are confident
  in it — futures and forex are leveraged, so live losses can exceed
  what you put in.
- Install: pip install ib_async pandas
- ib_insync is unmaintained; this uses ib_async, its actively
  maintained successor (same API surface).
"""

from ib_async import IB, Stock, Forex, Future, Crypto, MarketOrder, util

PAPER_PORT_TWS = 7497
PAPER_PORT_GATEWAY = 4002
LIVE_PORT_TWS = 7496
LIVE_PORT_GATEWAY = 4001


def connect(port: int = PAPER_PORT_TWS, host: str = "127.0.0.1", client_id: int = 1,
            allow_live: bool = False) -> IB:
    """Connect to a running TWS / IB Gateway instance. Defaults to paper trading.

    Live ports are refused unless allow_live=True is passed explicitly —
    a deliberate speed bump so automated code can't silently touch real
    money because of a config typo."""
    if port in (LIVE_PORT_TWS, LIVE_PORT_GATEWAY) and not allow_live:
        raise RuntimeError(
            f"Port {port} is a LIVE trading port. Pass allow_live=True only "
            "after your strategy has months of paper-trading evidence behind it."
        )
    ib = IB()
    ib.connect(host, port, clientId=client_id)
    return ib


# ---------------------------------------------------------------- contracts
# One unified Order type works across all of these once the contract
# is qualified — this is the main advantage of building on IBKR for a
# multi-asset bot instead of stitching together separate broker APIs.

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


# ---------------------------------------------------------------- execution

def place_market_order(ib: IB, contract, quantity: float, action: str = "BUY"):
    """
    action: "BUY" or "SELL". quantity is in the contract's native unit
    (shares, FX base-currency units, futures contracts, crypto units).
    Returns the Trade object so you can track fill status.
    """
    ib.qualifyContracts(contract)
    order = MarketOrder(action, quantity)
    trade = ib.placeOrder(contract, order)
    return trade


if __name__ == "__main__":
    # Smoke test against your PAPER account — confirms Gateway/TWS is
    # reachable and pulls one asset from each class.
    ib = connect()
    print("Connected:", ib.isConnected())

    for c in [stock("AAPL"), forex_pair("EURUSD"), crypto("BTC")]:
        df = get_15min_bars(ib, c, duration="1 D")
        print(f"\n{c.symbol} — last 5 bars (15min):")
        print(df.tail())

    ib.disconnect()
