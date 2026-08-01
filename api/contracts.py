"""
contracts.py — symbol string -> qualified IBKR contract, for the web UI.

The web UI has one search box and five asset classes behind it, so it needs
a parser that turns whatever the owner types into the right contract type.
This is the ONLY place that mapping lives; `ibkr_service.py` stays the
source of truth for how each contract type is actually constructed
(`stock()` / `forex_pair()` / `future()` / `crypto()`), and this module
only decides which of those to call.

Accepted forms (case-insensitive):
    AAPL                 -> Stock (SMART, USD)
    STK:AAPL             -> Stock, explicit
    EUR.USD  EURUSD      -> Forex
    FX:EURUSD            -> Forex, explicit
    BTC-USD  BTC.USD     -> Crypto (PAXOS)
    CRYPTO:BTC           -> Crypto, explicit
    ES=F                 -> Future, front month resolved by IBKR
    FUT:ES:202612:CME    -> Future, fully specified
    FUT:MGC              -> Future, exchange looked up, front month

Ambiguity is resolved in this order: explicit prefix > separator shape >
known-symbol table > plain stock. A bare 4-6 letter string that happens to
look like an FX pair (e.g. "USDJPY") is only treated as forex if both halves
are real currency codes — otherwise it's a stock, because the watchlist is
equities and a wrong guess there is far more likely.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The trading modules live one level up and are imported as top-level names
# everywhere else in this project (`import ibkr_service`). Keep that working
# whether the API is started from the repo root or this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ibkr_service as ib_svc  # noqa: E402

# ISO currency codes IBKR actually quotes as FX pairs. Deliberately not the
# full ISO-4217 list — a short list keeps "PLTR"-shaped strings out of forex.
CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "MXN", "ZAR",
    "HKD", "SGD", "CNH", "TRY", "ILS", "KRW",
}

# Crypto IBKR supports through Paxos. Small on purpose: this is the set that
# can actually be traded, not the set that exists.
CRYPTO_SYMBOLS = {"BTC", "ETH", "LTC", "BCH", "PAXG"}

# Futures root -> exchange. IBKR needs the exchange; the owner shouldn't have
# to remember that gold is COMEX and crude is NYMEX.
FUTURES_EXCHANGES = {
    "ES": "CME", "MES": "CME", "NQ": "CME", "MNQ": "CME",
    "RTY": "CME", "M2K": "CME", "YM": "CBOT", "MYM": "CBOT",
    "ZB": "CBOT", "ZN": "CBOT", "ZF": "CBOT", "ZC": "CBOT",
    "ZS": "CBOT", "ZW": "CBOT",
    "GC": "COMEX", "MGC": "COMEX", "SI": "COMEX", "SIL": "COMEX",
    "HG": "COMEX",
    "CL": "NYMEX", "MCL": "NYMEX", "NG": "NYMEX", "RB": "NYMEX",
    "HO": "NYMEX",
    "6E": "CME", "6B": "CME", "6J": "CME", "6A": "CME", "6C": "CME",
}

# yfinance-style futures suffix -> IBKR root, so "ES=F" (what the rest of
# this project writes) resolves without the owner learning a second syntax.
YF_FUTURES_ROOTS = {
    "ES": "ES", "NQ": "NQ", "YM": "YM", "RTY": "RTY",
    "GC": "GC", "SI": "SI", "HG": "HG", "CL": "CL", "NG": "NG",
    "ZC": "ZC", "ZS": "ZS", "ZW": "ZW", "ZB": "ZB", "ZN": "ZN",
}


class SymbolError(ValueError):
    """Raised when a typed symbol can't be turned into a contract."""


@dataclass
class ResolvedSymbol:
    """A parsed symbol, before it touches IBKR.

    `key` is the canonical string the UI and cache use — parsing is stable,
    so the same typed input always produces the same cache key.
    """
    kind: str                      # stock | forex | crypto | future
    symbol: str
    currency: str = "USD"
    exchange: str = ""
    expiry: str = ""
    key: str = ""
    label: str = ""
    meta: dict = field(default_factory=dict)

    def contract(self):
        """Build the ib_async contract via ibkr_service's constructors."""
        if self.kind == "stock":
            return ib_svc.stock(self.symbol, self.currency)
        if self.kind == "forex":
            return ib_svc.forex_pair(f"{self.symbol}{self.currency}")
        if self.kind == "crypto":
            return ib_svc.crypto(self.symbol, self.currency)
        if self.kind == "future":
            return ib_svc.future(self.symbol, self.expiry, self.exchange)
        raise SymbolError(f"Unknown contract kind: {self.kind}")


def _forex(base: str, quote: str) -> ResolvedSymbol:
    return ResolvedSymbol(
        kind="forex", symbol=base, currency=quote,
        key=f"FX:{base}{quote}", label=f"{base}/{quote}",
    )


def _crypto(sym: str, quote: str = "USD") -> ResolvedSymbol:
    return ResolvedSymbol(
        kind="crypto", symbol=sym, currency=quote,
        key=f"CRYPTO:{sym}{quote}", label=f"{sym}/{quote}",
    )


def _future(root: str, expiry: str = "", exchange: str = "") -> ResolvedSymbol:
    exchange = exchange or FUTURES_EXCHANGES.get(root, "")
    if not exchange:
        raise SymbolError(
            f"Don't know which exchange lists futures root '{root}'. "
            f"Use FUT:{root}:YYYYMM:EXCHANGE to say explicitly."
        )
    return ResolvedSymbol(
        kind="future", symbol=root, expiry=expiry, exchange=exchange,
        key=f"FUT:{root}:{expiry or 'front'}:{exchange}",
        label=f"{root} {expiry or 'front month'} ({exchange})",
    )


def _stock(sym: str, currency: str = "USD") -> ResolvedSymbol:
    return ResolvedSymbol(
        kind="stock", symbol=sym, currency=currency,
        key=f"STK:{sym}", label=sym,
    )


def _split_pair(text: str) -> tuple[str, str] | None:
    """'EURUSD' -> ('EUR','USD') only when both halves are real currencies."""
    if len(text) != 6:
        return None
    base, quote = text[:3], text[3:]
    if base in CURRENCIES and quote in CURRENCIES and base != quote:
        return base, quote
    return None


def resolve(raw: str) -> ResolvedSymbol:
    """Parse a typed symbol into a ResolvedSymbol. Raises SymbolError."""
    if not raw or not raw.strip():
        raise SymbolError("Empty symbol.")

    text = raw.strip().upper()

    # ---- explicit prefixes win over every heuristic below
    if ":" in text:
        prefix, _, rest = text.partition(":")
        parts = [p for p in rest.split(":") if p]
        if prefix in ("STK", "STOCK", "EQ"):
            if not parts:
                raise SymbolError("STK: needs a symbol, e.g. STK:AAPL")
            return _stock(parts[0], parts[1] if len(parts) > 1 else "USD")
        if prefix in ("FX", "FOREX", "CASH"):
            pair = "".join(parts).replace(".", "").replace("/", "")
            split = _split_pair(pair)
            if not split:
                raise SymbolError(f"'{rest}' is not a currency pair (try FX:EURUSD).")
            return _forex(*split)
        if prefix in ("CRYPTO", "COIN"):
            if not parts:
                raise SymbolError("CRYPTO: needs a symbol, e.g. CRYPTO:BTC")
            sym = parts[0]
            quote = parts[1] if len(parts) > 1 else "USD"
            return _crypto(sym, quote)
        if prefix in ("FUT", "FUTURE"):
            if not parts:
                raise SymbolError("FUT: needs a root, e.g. FUT:ES:202612:CME")
            root = parts[0]
            expiry = parts[1] if len(parts) > 1 else ""
            exchange = parts[2] if len(parts) > 2 else ""
            if expiry and not re.fullmatch(r"\d{6}|\d{8}", expiry):
                raise SymbolError(f"Expiry '{expiry}' should be YYYYMM or YYYYMMDD.")
            return _future(root, expiry, exchange)
        raise SymbolError(f"Unknown prefix '{prefix}:'. Use STK, FX, CRYPTO or FUT.")

    # ---- yfinance futures form: ES=F
    if text.endswith("=F"):
        root = text[:-2]
        return _future(YF_FUTURES_ROOTS.get(root, root))

    # ---- separator forms: EUR.USD / BTC-USD / EUR/USD
    for sep in (".", "-", "/"):
        if sep in text:
            left, _, right = text.partition(sep)
            if left in CRYPTO_SYMBOLS and right in CURRENCIES:
                return _crypto(left, right)
            if left in CURRENCIES and right in CURRENCIES:
                return _forex(left, right)
            # BRK.B and friends — a dotted share class is still a stock.
            if sep == "." and len(right) <= 2:
                return _stock(text)
            raise SymbolError(
                f"Can't tell what '{raw}' is. Try FX:EURUSD, CRYPTO:BTC or STK:{left}."
            )

    # ---- bare strings
    if text in CRYPTO_SYMBOLS:
        return _crypto(text)
    pair = _split_pair(text)
    if pair:
        return _forex(*pair)
    if not re.fullmatch(r"[A-Z0-9.]{1,12}", text):
        raise SymbolError(f"'{raw}' doesn't look like a tradeable symbol.")
    return _stock(text)


def describe(sym: ResolvedSymbol) -> dict:
    """JSON-safe view for the UI."""
    return {
        "key": sym.key,
        "kind": sym.kind,
        "symbol": sym.symbol,
        "currency": sym.currency,
        "exchange": sym.exchange,
        "expiry": sym.expiry,
        "label": sym.label,
    }


def _selftest() -> int:
    """Offline check — no IBKR connection needed. `python3 api/contracts.py`."""
    failures = []

    def check(name, cond):
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def kind_of(text):
        return resolve(text).kind

    check("AAPL is a stock", kind_of("AAPL") == "stock")
    check("lowercase aapl is a stock", resolve("aapl").symbol == "AAPL")
    check("BRK.B stays a stock", kind_of("BRK.B") == "stock")
    check("EUR.USD is forex", kind_of("EUR.USD") == "forex")
    check("EURUSD is forex", kind_of("EURUSD") == "forex")
    check("FX:EURUSD is forex", kind_of("FX:EURUSD") == "forex")
    check("forex splits correctly", resolve("EURUSD").currency == "USD")
    check("BTC-USD is crypto", kind_of("BTC-USD") == "crypto")
    check("bare BTC is crypto", kind_of("BTC") == "crypto")
    check("ES=F is a future", kind_of("ES=F") == "future")
    check("ES=F lands on CME", resolve("ES=F").exchange == "CME")
    check("MGC resolves to COMEX", resolve("FUT:MGC").exchange == "COMEX")
    check("explicit future keeps expiry", resolve("FUT:ES:202612:CME").expiry == "202612")

    # The ambiguity that matters: a 6-letter ticker that isn't two currencies
    # must stay a stock, or the watchlist breaks.
    check("PLTRXX is not forex", kind_of("PLTRXX") == "stock")
    check("USDUSD is not forex", kind_of("USDUSD") == "stock")

    # Cache keys must be stable across equivalent spellings.
    check("EURUSD and EUR.USD share a key", resolve("EURUSD").key == resolve("EUR.USD").key)
    check("AAPL and STK:AAPL share a key", resolve("AAPL").key == resolve("STK:AAPL").key)

    for bad in ("", "   ", "FX:AAPL", "BOGUS:X", "FUT:ZZZZ", "AAPL-XYZ"):
        try:
            resolve(bad)
            check(f"{bad!r} rejected", False)
        except SymbolError:
            check(f"{bad!r} rejected", True)

    print(f"\n{len(failures)} failure(s)." if failures else "\nAll contract checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
