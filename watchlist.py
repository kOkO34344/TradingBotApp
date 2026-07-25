"""Watchlist management — named groups, validated symbols, one traded universe.

The watchlist used to be a flat `settings["tickers"]` list edited as a raw
comma-separated string. This module makes it structured and validated:

    settings["watchlist_groups"] = {"Core": ["AAPL", ...], "Tech": [...]}
    settings["tickers"]          = deduped union of every group  (DERIVED)

`watchlist_groups` is the SOURCE OF TRUTH; `tickers` is regenerated from it
on every save. That split is deliberate: `paper_trader.py`, `autotrade_runner.py`,
`run_research_agent_watchlist.py`, `kronos_agent.py` and `trader_app.py` all read
`settings["tickers"]` and keep working unchanged — grouping is an editing and
reporting convenience layered on top, not a new contract they have to learn.

Groups mirror how the owner organizes watchlists in IBKR. They are NOT synced
from IBKR automatically: the TWS API exposes no watchlist endpoint (verified
against ib_async 2.1.0 — there is `reqScannerData`, `reqPositions`,
`reqContractDetails`, but nothing that reads a TWS watchlist; they are a
client-side UI feature). Auto-sync would need IBKR's separate Client Portal
Web API — a second gateway, browser login and session keepalive — which was
considered and deliberately not built. Editing happens here instead.

Two safety properties this module enforces, both learned the hard way
elsewhere in this project:

1. **Symbols are validated before they enter the list.** The traded universe
   is the list `paper_trader`/`autotrade_runner` rank and buy from, so a typo
   or an instrument yfinance cannot serve (forex, futures, foreign listings)
   must never reach it. Unsupported symbols are dropped and REPORTED, never
   silently discarded — same precedent as `broad_universe_momentum.py`.

2. **Removing a ticker you hold a position in is guarded.** `paper_trader.py`
   filters current holdings with `if sym in tickers` — so dropping a held
   symbol from the watchlist makes its position INVISIBLE to the trader,
   which then neither manages nor exits it. The position keeps its GTC stop
   but nothing else will ever touch it. `held_symbols()` exists so callers
   can warn before that happens.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

APP_DIR = Path(__file__).parent

# Client id for the read-only position check. Distinct from every other
# tool so a watchlist edit can never collide with a running trader:
# 7=trader_app, 9=paper_trader, 11=reflect_on_trades, 13=autotrade_runner.
POSITION_CHECK_CLIENT_ID = 15

DEFAULT_GROUP = "Core"


# ------------------------------------------------------------------ groups

def get_groups(settings: dict) -> dict[str, list[str]]:
    """Groups from settings, migrating a legacy flat `tickers` list on first use.

    Returns a copy — mutate it and pass it to `save_groups()` to persist.
    """
    groups = settings.get("watchlist_groups")
    if not groups:
        # Legacy settings, or a fresh install: everything starts in one group.
        return {DEFAULT_GROUP: list(settings.get("tickers", []))}
    return {name: list(syms) for name, syms in groups.items()}


def flatten(groups: dict[str, list[str]]) -> list[str]:
    """Ordered, deduped union of every group — the traded universe.

    Order follows first appearance so the list stays stable across edits
    (a ticker in two groups keeps the position of its first group).
    """
    seen: list[str] = []
    for syms in groups.values():
        for s in syms:
            if s not in seen:
                seen.append(s)
    return seen


def save_groups(settings: dict, groups: dict[str, list[str]]) -> list[str]:
    """Write groups into settings and regenerate the derived `tickers` union.

    Does NOT write to disk — the caller owns that (trader_app.save_settings),
    so a menu can batch several edits into one save. Returns the new union.
    """
    settings["watchlist_groups"] = {name: list(syms) for name, syms in groups.items()}
    tickers = flatten(groups)
    settings["tickers"] = tickers
    return tickers


def group_of(groups: dict[str, list[str]], symbol: str) -> list[str]:
    """Which group names contain `symbol` (a ticker may live in several)."""
    return [name for name, syms in groups.items() if symbol in syms]


# ------------------------------------------------------- symbol validation

def normalize_symbol(raw: str) -> str:
    """Best-effort IBKR-style symbol -> yfinance symbol.

    IBKR writes class shares with a space or dot (`BRK B`, `BRK.B`) where
    yfinance wants a dash (`BRK-B`). Everything else is just cleaned up.
    """
    s = raw.strip().upper()
    if not s:
        return s
    # "BRK B" / "BRK.B" -> "BRK-B", but leave real suffixed tickers alone
    # (e.g. "9988.HK" keeps its exchange suffix so validation can reject it
    # honestly rather than mangling it into something that looks valid).
    if " " in s:
        s = s.replace(" ", "-")
    elif "." in s and not s.rsplit(".", 1)[1].isalpha():
        pass  # numeric-ish suffix, leave as-is
    elif "." in s and len(s.rsplit(".", 1)[1]) == 1:
        s = s.replace(".", "-")
    return s


# Quote currencies that mark a symbol as an FX or crypto pair rather than a
# US-listed stock. `BRK-B` (class share) and `BTC-USD` (crypto) both contain a
# dash, so the dash alone can't tell them apart — the suffix is what does.
_PAIR_SUFFIXES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "USDT", "BTC", "ETH"}


def pipeline_supported(sym: str) -> str | None:
    """Reason `sym` can't go through this project's order path, or None if fine.

    The execution path (`ibkr_service.place_bracket_order`) submits US `STK`
    contracts. yfinance will happily serve foreign listings and crypto pairs,
    so "the data fetches" is NOT sufficient — a symbol that fetches but can't
    be routed as a US stock would pass validation and then fail (or worse,
    fill wrong) at execution. Checked before the network call: it's cheaper
    and gives a more precise reason than an empty DataFrame would.
    """
    if "." in sym:
        suffix = sym.rsplit(".", 1)[1]
        if suffix in _PAIR_SUFFIXES:
            # IBKR writes FX pairs as EUR.USD — a dot, but not an exchange.
            return "FX pair — pipeline trades US stocks only"
        # yfinance marks non-US listings with an exchange suffix: 9988.HK,
        # ASML.AS, VOD.L. US listings carry no suffix.
        return f"foreign listing ({suffix}) — pipeline trades US stocks only"
    if "-" in sym and sym.rsplit("-", 1)[1] in _PAIR_SUFFIXES:
        return "FX/crypto pair — pipeline trades US stocks only"
    if "=" in sym:
        # yfinance futures (ES=F) and FX (EURUSD=X)
        return "future/FX contract — pipeline trades US stocks only"
    return None


def validate_symbols(symbols: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Check each symbol is pipeline-tradeable AND resolves to real price data.

    Returns `(ok, dropped)` where `ok` is the normalized symbols that passed
    and `dropped` is `[(original_symbol, reason), ...]`. Entries whose reason
    starts with "KEPT" are informational (a symbol was renamed), not failures.

    Uses a short recent window rather than full history — this only needs to
    answer "does this symbol serve data at all", and a 5-day pull is orders of
    magnitude cheaper than the multi-year download the backtest cache does.
    """
    ok: list[str] = []
    dropped: list[tuple[str, str]] = []

    for raw in symbols:
        sym = normalize_symbol(raw)
        if not sym:
            continue
        unsupported = pipeline_supported(sym)
        if unsupported:
            dropped.append((raw, unsupported))
            continue
        note = "" if sym == raw.strip().upper() else f" (normalized from {raw.strip().upper()})"
        try:
            df = yf.download(sym, period="5d", progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty or "Close" not in df or df["Close"].dropna().empty:
                dropped.append((raw, "no price data from yfinance "
                                     "(delisted, non-equity, or wrong symbol)"))
                continue
        except Exception as e:
            dropped.append((raw, f"fetch failed: {e}"))
            continue
        ok.append(sym)
        if note:
            # Not a failure — record the rename so the owner sees it happened.
            dropped.append((raw, f"KEPT as {sym}{note}"))

    return ok, dropped


# ------------------------------------------------------- position safety

def held_symbols(settings: dict) -> set[str] | None:
    """Symbols with a non-zero position on the paper account.

    Returns `None` if IBKR is unreachable — the caller MUST treat that as
    "unknown", not "nothing held".

    This function only connects, reads `ib.positions()` and disconnects — it
    calls nothing that places or cancels an order. Note that is a property of
    this code, NOT of the socket: `ibkr_service.connect()` takes no `readonly`
    flag, so the connection is a normal read/write one. ib_async's
    `IB.connect(readonly=True)` would make TWS itself refuse orders on this
    connection; wiring that through `ibkr_service.connect()` would be a real
    strengthening for every read-only caller here (this one, and
    `paper_trader.py --dry-run`, whose help text already claims "read-only"
    on the same convention-only basis).
    """
    try:
        import ibkr_service as ibs
    except Exception:
        return None

    ib = None
    try:
        ib = ibs.connect(port=settings.get("ibkr_port", 4002),
                         client_id=POSITION_CHECK_CLIENT_ID)
        ibs.verify_paper_account(ib)
        return {p.contract.symbol for p in ib.positions() if p.position != 0}
    except Exception:
        return None
    finally:
        if ib is not None:
            try:
                ib.disconnect()
            except Exception:
                pass


# ------------------------------------------------------------------ selftest

def _selftest() -> None:
    """Offline checks for the pure logic (no network, no IBKR)."""
    print("watchlist.py selftest")

    # flatten: dedupes across groups, preserves first-appearance order
    g = {"Tech": ["AAPL", "NVDA"], "Div": ["KO", "AAPL"], "New": ["TSM"]}
    assert flatten(g) == ["AAPL", "NVDA", "KO", "TSM"], flatten(g)
    print("  flatten dedupe/order            ok")

    # migration from a legacy flat list
    legacy = {"tickers": ["AAPL", "MSFT"]}
    assert get_groups(legacy) == {DEFAULT_GROUP: ["AAPL", "MSFT"]}
    print("  legacy flat-list migration      ok")

    # save_groups regenerates the derived union
    s: dict = {"tickers": ["OLD"]}
    union = save_groups(s, g)
    assert s["tickers"] == union == ["AAPL", "NVDA", "KO", "TSM"], s["tickers"]
    assert s["watchlist_groups"]["Tech"] == ["AAPL", "NVDA"]
    print("  save_groups derives tickers     ok")

    # get_groups returns a copy — mutating it must not touch settings
    got = get_groups(s)
    got["Tech"].append("MUTATED")
    assert "MUTATED" not in s["watchlist_groups"]["Tech"]
    print("  get_groups returns a copy       ok")

    # symbol normalization
    assert normalize_symbol(" aapl ") == "AAPL"
    assert normalize_symbol("BRK B") == "BRK-B"
    assert normalize_symbol("BRK.B") == "BRK-B"
    assert normalize_symbol("9988.HK") == "9988.HK"  # left alone, rejected later
    print("  normalize_symbol                ok")

    # pipeline support — the checks that stop an untradeable symbol entering
    # the universe paper_trader/autotrade buy from
    assert pipeline_supported("AAPL") is None
    assert pipeline_supported("BRK-B") is None          # class share, tradeable
    assert "foreign listing" in pipeline_supported("9988.HK")
    assert "foreign listing" in pipeline_supported("ASML.AS")
    assert "FX pair" in pipeline_supported("EUR.USD")    # IBKR-style FX, not an exchange
    assert "FX/crypto" in pipeline_supported("BTC-USD")
    assert "future/FX" in pipeline_supported("ES=F")
    assert "future/FX" in pipeline_supported("EURUSD=X")
    print("  pipeline_supported              ok")

    # group_of
    assert sorted(group_of(g, "AAPL")) == ["Div", "Tech"]
    assert group_of(g, "NOPE") == []
    print("  group_of                        ok")

    print("all offline checks passed")


if __name__ == "__main__":
    _selftest()
