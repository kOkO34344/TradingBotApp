"""Watchlist management — named groups, validated symbols, one research universe.

The watchlist used to be a flat `settings["tickers"]` list edited as a raw
comma-separated string. This module makes it structured and validated:

    settings["watchlist_groups"] = {"Core": ["AAPL", ...], "Tech": [...]}
    settings["tickers"]          = deduped union of every group  (DERIVED)

`watchlist_groups` is the SOURCE OF TRUTH; `tickers` is regenerated from it
on every save. That split is deliberate: `run_research_agent_watchlist.py`,
`kronos_agent.py`, the backtest scripts and `trader_app.py` all read
`settings["tickers"]` and keep working unchanged — grouping is an editing and
reporting convenience layered on top, not a new contract they have to learn.

THIS IS THE RESEARCH UNIVERSE, NOT THE TRADED ONE (2026-08-09). It was both
until IBKR was removed. FTMO's tradeable universe is a different set of
instruments entirely — CFDs named `EURUSD`, `US30.cash`, `NATGAS.cash` — and it
is derived from the venue's own symbol capture by `ftmo_signal.build_universe`,
not from anything here. Keeping the two apart is the honest arrangement:
researching AAPL on yfinance daily bars is a real activity, and pretending the
list also describes what can be bought would be the kind of quiet mismatch this
project keeps getting bitten by.

One safety property this module enforces:

**Symbols are validated before they enter the list.** A typo, or an instrument
yfinance cannot serve properly, must never reach the list the research agent
and Kronos read. Unsupported symbols are dropped and REPORTED, never silently
discarded — same precedent as `broad_universe_momentum.py`.

(The old second property — guarding removal of a symbol you held — went with
IBKR. It existed because `paper_trader.py` filtered live holdings with
`if sym in tickers`, so a removed ticker's position became invisible and
stopped being managed. Nothing filters positions by this list any more: FTMO
positions are reconciled against the venue itself by `ftmo_closes.py`, which
reads what is actually open rather than what is configured. If any consumer
ever starts filtering holdings by the watchlist again, put the guard back.)
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

APP_DIR = Path(__file__).parent

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
    """Best-effort broker-style symbol -> yfinance symbol.

    Most data sources and brokers write class shares with a space or dot
    (`BRK B`, `BRK.B`) where yfinance wants a dash (`BRK-B`). Everything else
    is just cleaned up.
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
    """Reason `sym` can't be researched by this project, or None if fine.

    The bar was "IBKR can route it as a US stock" until 2026-08-09. That
    reason left with IBKR, but the CHECK stays, because a better one was
    already sitting in this project's evidence: **yfinance reports volume as
    identically ZERO for spot FX pairs and serves foreign listings and futures
    with gaps and holidays a US session does not have** — and Kronos, which is
    the main consumer of this list, conditions on volume. Scoring a model on a
    dead input returns a confident artifact, which is exactly why the asset-
    class IC screen had to use CME futures rather than spot FX.

    So this is no longer "the order path can't fill it"; it is "the research
    would be measuring nothing". Same symbols dropped, an honest reason.

    Checked before the network call: cheaper, and a more precise reason than
    an empty DataFrame would give.
    """
    if "." in sym:
        suffix = sym.rsplit(".", 1)[1]
        if suffix in _PAIR_SUFFIXES:
            return "FX pair — yfinance reports zero volume; Kronos needs volume"
        # yfinance marks non-US listings with an exchange suffix: 9988.HK,
        # ASML.AS, VOD.L. US listings carry no suffix.
        return f"foreign listing ({suffix}) — research universe is US-listed"
    if "-" in sym and sym.rsplit("-", 1)[1] in _PAIR_SUFFIXES:
        return "FX/crypto pair — yfinance volume is unreliable for these"
    if "=" in sym:
        # yfinance futures (ES=F) and FX (EURUSD=X)
        return "future/FX contract — research universe is US-listed equities"
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


# ------------------------------------------------------------------ selftest

def _selftest() -> None:
    """Offline checks for the pure logic (no network, no venue)."""
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

    # research support — the checks that stop a symbol yfinance cannot
    # meaningfully serve from entering the universe Kronos and the research
    # agent read
    assert pipeline_supported("AAPL") is None
    assert pipeline_supported("BRK-B") is None          # class share, researchable
    assert "foreign listing" in pipeline_supported("9988.HK")
    assert "foreign listing" in pipeline_supported("ASML.AS")
    assert "FX pair" in pipeline_supported("EUR.USD")   # dotted FX, not an exchange
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
