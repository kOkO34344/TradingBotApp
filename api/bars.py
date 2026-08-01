"""
bars.py — historical bars for the web charts, with a pacing-aware cache.

IBKR paces historical-data requests (~60 per 10 minutes per connection) and
throttles harder if you flip timeframes across many symbols quickly — which
is exactly what a chart with a 1m/5m/15m/1h/1d switcher invites. So every
request lands here first: identical (symbol, timeframe, duration) requests
inside the TTL are served from memory, and a `cachedAt` timestamp goes back
with the data so the UI can show its age instead of implying freshness.

The account has no live market-data subscription, so `reqMarketDataType(3)`
(delayed) is set on the hub. Every response therefore carries an explicit
`delayed: true` — a 15-minute-old print rendered as if it were live is the
kind of quiet wrongness this project keeps getting bitten by.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ibkr_service as ib_svc  # noqa: E402
from ib_async import util  # noqa: E402

from .contracts import ResolvedSymbol  # noqa: E402

log = logging.getLogger("api.bars")


@dataclass(frozen=True)
class Timeframe:
    key: str
    bar_size: str        # IBKR barSizeSetting
    default_duration: str
    max_duration: str
    seconds: int
    cache_ttl: int       # how long a cached pull stays servable, in seconds


# TTLs are ~half a bar: long enough to absorb a chart re-render or a second
# browser tab, short enough that you never look at a bar that closed twice ago.
TIMEFRAMES: dict[str, Timeframe] = {
    "1m":  Timeframe("1m",  "1 min",   "1 D",  "5 D",    60,    30),
    "5m":  Timeframe("5m",  "5 mins",  "5 D",  "1 M",   300,   120),
    "15m": Timeframe("15m", "15 mins", "10 D", "1 M",   900,   300),
    "1h":  Timeframe("1h",  "1 hour",  "2 M",  "1 Y",  3600,   900),
    "1d":  Timeframe("1d",  "1 day",   "1 Y",  "10 Y", 86400,  900),
}

DEFAULT_TIMEFRAME = "1d"


class BarFetchError(RuntimeError):
    """IBKR returned no usable bars, with the reason it gave."""


@dataclass
class CacheEntry:
    bars: list[dict]
    fetched_at: float
    delayed: bool


class BarCache:
    """Process-wide bar cache, keyed by symbol + timeframe + duration."""

    def __init__(self):
        self._entries: dict[tuple, CacheEntry] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}

    def get(self, key: tuple, ttl: int) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry and (time.time() - entry.fetched_at) < ttl:
            return entry
        return None

    def put(self, key: tuple, entry: CacheEntry) -> None:
        self._entries[key] = entry

    def peek(self, key: tuple) -> CacheEntry | None:
        """Ignore the TTL — used only to serve something during an outage,
        and always labelled stale when it is."""
        return self._entries.get(key)

    def inflight(self, key: tuple) -> asyncio.Future | None:
        return self._inflight.get(key)

    def set_inflight(self, key: tuple, fut: asyncio.Future) -> None:
        self._inflight[key] = fut

    def clear_inflight(self, key: tuple) -> None:
        self._inflight.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()


_cache = BarCache()


def _clean(value) -> float | None:
    """IBKR sends NaN for absent volume/WAP on some contract types."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _clean_volume(value) -> float | None:
    """Volume, or None when the contract simply doesn't report it.

    Forex bars are MIDPOINT — there is no trades tape, and IBKR signals that
    by returning **-1** rather than 0 or null. Passed through untouched it
    renders as a full-height histogram of -1s across the whole chart, which
    is what happened on the first EUR/USD render. A sentinel is not data.
    """
    f = _clean(value)
    if f is None or f < 0:
        return None
    return f


def _to_rows(df) -> list[dict]:
    """DataFrame -> the shape the charting library wants.

    `time` is emitted as a UNIX timestamp in seconds so the frontend never
    has to parse an IBKR date string or guess a timezone.
    """
    if df is None or len(df) == 0:
        return []
    rows: list[dict] = []
    for rec in df.to_dict("records"):
        dt = rec.get("date")
        if dt is None:
            continue
        ts = getattr(dt, "timestamp", None)
        if callable(ts):
            t = int(dt.timestamp())
        else:  # a plain date (daily bars) — treat as UTC midnight
            import datetime as _dt
            t = int(_dt.datetime.combine(dt, _dt.time()).replace(
                tzinfo=_dt.timezone.utc).timestamp())
        o, h, low, c = (_clean(rec.get(k)) for k in ("open", "high", "low", "close"))
        if None in (o, h, low, c):
            continue
        rows.append({
            "time": t, "open": o, "high": h, "low": low, "close": c,
            "volume": _clean_volume(rec.get("volume")),
        })
    rows.sort(key=lambda r: r["time"])
    return rows


async def fetch_bars(hub, sym: ResolvedSymbol, timeframe: str = DEFAULT_TIMEFRAME,
                     duration: str | None = None, use_rth: bool = False) -> dict:
    """Bars for one symbol/timeframe, cached. Raises BarFetchError."""
    tf = TIMEFRAMES.get(timeframe)
    if tf is None:
        raise BarFetchError(
            f"Unknown timeframe '{timeframe}'. Use one of: {', '.join(TIMEFRAMES)}."
        )
    duration = duration or tf.default_duration
    key = (sym.key, tf.key, duration, use_rth)

    cached = _cache.get(key, tf.cache_ttl)
    if cached:
        return _envelope(sym, tf, duration, cached, from_cache=True)

    # Collapse duplicate concurrent requests (two tabs, same chart) into one
    # IBKR call rather than two — pacing counts calls, not callers.
    existing = _cache.inflight(key)
    if existing is not None:
        entry = await existing
        return _envelope(sym, tf, duration, entry, from_cache=True)

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _cache.set_inflight(key, fut)
    try:
        entry = await _do_fetch(hub, sym, tf, duration, use_rth)
        _cache.put(key, entry)
        if not fut.done():
            fut.set_result(entry)
        return _envelope(sym, tf, duration, entry, from_cache=False)
    except Exception as exc:
        if not fut.done():
            fut.set_exception(exc)
        # Serve stale data rather than nothing if the Gateway just went away,
        # but say so loudly — the UI renders it greyed with the age shown.
        stale = _cache.peek(key)
        if stale is not None:
            log.warning("Serving stale bars for %s %s: %s", sym.key, tf.key, exc)
            env = _envelope(sym, tf, duration, stale, from_cache=True)
            env["stale"] = True
            env["error"] = str(exc)
            return env
        raise
    finally:
        _cache.clear_inflight(key)
        # Nobody awaits a failed future beyond this point; retrieve the
        # exception so asyncio doesn't log it as "never retrieved".
        if fut.done() and fut.exception() is not None:
            fut.exception()


# Qualified contracts, keyed by ResolvedSymbol.key. Qualification is a
# round trip to IBKR and the answer only changes when a future rolls, so
# caching it keeps chart navigation off the contract-details channel.
_qualified_cache: dict[str, object] = {}


async def qualify(hub, sym: ResolvedSymbol):
    """Resolve a ResolvedSymbol to one concrete, tradeable IBKR contract.

    Futures need real work here. `Future("ES", "", "CME")` is ambiguous —
    IBKR lists 21 ES contracts out to 2031 — and `qualifyContractsAsync`
    answers an ambiguous contract with `[None]`, not an empty list or an
    exception. Indexing that gives a confusing AttributeError several frames
    later. So when no expiry was specified we ask for contract details and
    pick the front month ourselves: the earliest expiry that hasn't passed.
    """
    cached = _qualified_cache.get(sym.key)
    if cached is not None:
        return cached

    ib = hub.require()
    contract = sym.contract()

    if sym.kind == "future" and not sym.expiry:
        details = await hub.run(ib.reqContractDetailsAsync, contract)
        if not details:
            raise BarFetchError(
                f"IBKR lists no futures contracts for root '{sym.symbol}' on "
                f"{sym.exchange}. Check the root and exchange."
            )
        today = time.strftime("%Y%m%d")
        candidates = sorted(
            (d.contract for d in details if d.contract.lastTradeDateOrContractMonth),
            key=lambda c: c.lastTradeDateOrContractMonth,
        )
        live = [c for c in candidates
                if c.lastTradeDateOrContractMonth >= today] or candidates
        resolved = live[0]
        log.info("Front month for %s: %s (expires %s)", sym.symbol,
                 resolved.localSymbol, resolved.lastTradeDateOrContractMonth)
    else:
        qualified = await hub.run(ib.qualifyContractsAsync, contract)
        resolved = qualified[0] if qualified else None
        if resolved is None:
            raise BarFetchError(
                f"IBKR could not qualify {sym.label}. The symbol may not exist, "
                "may not be tradeable on this account, or (for futures) may be "
                "ambiguous — specify the expiry as FUT:ROOT:YYYYMM:EXCHANGE."
            )

    _qualified_cache[sym.key] = resolved
    return resolved


async def _do_fetch(hub, sym: ResolvedSymbol, tf: Timeframe,
                    duration: str, use_rth: bool) -> CacheEntry:
    ib = hub.require()
    contract = await qualify(hub, sym)

    bars = await hub.run(
        ib.reqHistoricalDataAsync,
        contract, endDateTime="", durationStr=duration,
        barSizeSetting=tf.bar_size,
        whatToShow=ib_svc._what_to_show(contract),
        useRTH=use_rth, formatDate=2,
    )
    rows = _to_rows(util.df(bars))
    if not rows:
        raise BarFetchError(
            f"No bars returned for {sym.label} at {tf.key}. Common causes: "
            "the contract has no data permission on this account, the "
            "duration exceeds what IBKR allows for this bar size, or the "
            "market has never traded in the requested window."
        )
    return CacheEntry(bars=rows, fetched_at=time.time(), delayed=True)


def _envelope(sym: ResolvedSymbol, tf: Timeframe, duration: str,
              entry: CacheEntry, from_cache: bool) -> dict:
    age = time.time() - entry.fetched_at
    return {
        "symbol": sym.key,
        "label": sym.label,
        "kind": sym.kind,
        "timeframe": tf.key,
        "duration": duration,
        "bars": entry.bars,
        "count": len(entry.bars),
        "source": "IBKR",
        "delayed": entry.delayed,
        "fetchedAt": entry.fetched_at,
        "ageSeconds": round(age, 1),
        "fromCache": from_cache,
        "stale": False,
        "error": None,
    }


def clear_cache() -> None:
    _cache.clear()
    _qualified_cache.clear()


def timeframe_list() -> list[dict]:
    return [
        {"key": tf.key, "barSize": tf.bar_size, "defaultDuration": tf.default_duration,
         "maxDuration": tf.max_duration, "seconds": tf.seconds}
        for tf in TIMEFRAMES.values()
    ]
