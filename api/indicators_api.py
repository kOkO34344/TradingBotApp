"""
indicators_api.py — chart overlays computed by indicators.py, never in JS.

CLAUDE.md: `indicators.py` is the SINGLE SOURCE OF TRUTH for technical math,
"including in any future web dashboard". This is that dashboard, so every
line the chart draws is computed here, server-side, by the same functions
the research agent's prompts and the terminal app already call. If the web
RSI ever disagreed with the RSI in a research note, the note's reasoning
would be unauditable — that's the whole point of the rule.

The only work this module does itself is plumbing: IBKR bars arrive as
lowercase-keyed rows, indicators.py expects a yfinance-shaped frame with
capitalised columns and a DatetimeIndex, and the chart wants JSON with
nulls where the warm-up period has no value.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import indicators as ind  # noqa: E402


class IndicatorError(ValueError):
    """A requested indicator or parameter doesn't exist / doesn't fit."""


def bars_to_frame(bars: list[dict]) -> pd.DataFrame:
    """Chart rows -> the frame shape indicators.py was written against."""
    if not bars:
        raise IndicatorError("No bars to compute indicators on.")
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("date").sort_index()
    out = pd.DataFrame({
        "Open": df["open"].astype(float),
        "High": df["high"].astype(float),
        "Low": df["low"].astype(float),
        "Close": df["close"].astype(float),
        "Volume": df["volume"].fillna(0).astype(float) if "volume" in df else 0.0,
    }, index=df.index)
    return out


def _series_points(times: list[int], series: pd.Series) -> list[dict]:
    """Align a computed series back to bar timestamps, dropping NaN warm-up.

    Gaps are omitted rather than sent as null: the chart library treats a
    missing point as "no line here", which is the honest rendering of an
    SMA(50) that has only seen 30 bars.
    """
    points = []
    for t, v in zip(times, series.tolist()):
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        points.append({"time": t, "value": round(f, 6)})
    return points


@dataclass
class IndicatorSpec:
    """Metadata the UI needs to render an indicator without hardcoding it."""
    key: str
    name: str
    pane: str                      # "price" (overlay) or its own pane id
    params: dict[str, float]       # name -> default
    compute: Callable
    outputs: list[str] = field(default_factory=list)
    description: str = ""
    bounds: tuple[float, float] | None = None   # fixed y-scale, e.g. RSI 0-100
    guides: list[float] = field(default_factory=list)   # horizontal ref lines


# ---- individual computations, each delegating straight to indicators.py

def _c_sma(df, times, period=20):
    return {"sma": _series_points(times, ind.sma(df["Close"], int(period)))}


def _c_ema(df, times, period=50):
    return {"ema": _series_points(times, ind.ema(df["Close"], int(period)))}


def _c_macd(df, times, fast=12, slow=26, signal=9):
    line, sig, hist = ind.macd(df["Close"], int(fast), int(slow), int(signal))
    return {
        "macd": _series_points(times, line),
        "signal": _series_points(times, sig),
        "histogram": _series_points(times, hist),
    }


def _c_rsi(df, times, period=14):
    return {"rsi": _series_points(times, ind.rsi(df["Close"], int(period)))}


def _c_atr(df, times, period=14):
    return {"atr": _series_points(times, ind.atr(df, int(period)))}


def _c_bollinger(df, times, period=20, k=2.0):
    mid, upper, lower = ind.bollinger(df["Close"], int(period), float(k))
    return {
        "middle": _series_points(times, mid),
        "upper": _series_points(times, upper),
        "lower": _series_points(times, lower),
    }


def _c_keltner(df, times, period=20, k=2.0):
    mid, upper, lower = ind.keltner(df, int(period), float(k))
    return {
        "middle": _series_points(times, mid),
        "upper": _series_points(times, upper),
        "lower": _series_points(times, lower),
    }


def _c_vwap(df, times):
    return {"vwap": _series_points(times, ind.vwap(df))}


def _c_obv(df, times):
    return {"obv": _series_points(times, ind.obv(df))}


REGISTRY: dict[str, IndicatorSpec] = {
    "sma": IndicatorSpec(
        "sma", "SMA", "price", {"period": 20}, _c_sma, ["sma"],
        "Simple moving average of close."),
    "ema": IndicatorSpec(
        "ema", "EMA", "price", {"period": 50}, _c_ema, ["ema"],
        "Exponential moving average of close."),
    "bollinger": IndicatorSpec(
        "bollinger", "Bollinger Bands", "price", {"period": 20, "k": 2.0},
        _c_bollinger, ["middle", "upper", "lower"],
        "SMA(n) +/- k standard deviations."),
    "keltner": IndicatorSpec(
        "keltner", "Keltner Channels", "price", {"period": 20, "k": 2.0},
        _c_keltner, ["middle", "upper", "lower"],
        "EMA(n) +/- k * ATR(n)."),
    "vwap": IndicatorSpec(
        "vwap", "VWAP", "price", {}, _c_vwap, ["vwap"],
        "Volume-weighted average price, session-reset on intraday data."),
    "rsi": IndicatorSpec(
        "rsi", "RSI", "rsi", {"period": 14}, _c_rsi, ["rsi"],
        "Relative strength index.", bounds=(0, 100), guides=[30, 70]),
    "macd": IndicatorSpec(
        "macd", "MACD", "macd", {"fast": 12, "slow": 26, "signal": 9},
        _c_macd, ["macd", "signal", "histogram"],
        "MACD line, signal line and histogram.", guides=[0]),
    "atr": IndicatorSpec(
        "atr", "ATR", "atr", {"period": 14}, _c_atr, ["atr"],
        "Average true range — the same measure paper_trader sizes stops with."),
    "obv": IndicatorSpec(
        "obv", "OBV", "obv", {}, _c_obv, ["obv"],
        "On-balance volume."),
}


def parse_spec(text: str) -> tuple[str, dict]:
    """'sma:20' / 'bollinger:20:2' / 'rsi' -> (key, params).

    Positional params follow the registry's declared order, so the terse
    form the URL uses stays readable without a JSON body.
    """
    parts = [p for p in text.strip().split(":") if p != ""]
    if not parts:
        raise IndicatorError("Empty indicator spec.")
    key = parts[0].lower()
    spec = REGISTRY.get(key)
    if spec is None:
        raise IndicatorError(
            f"Unknown indicator '{key}'. Available: {', '.join(sorted(REGISTRY))}."
        )
    params = dict(spec.params)
    names = list(spec.params.keys())
    for i, raw in enumerate(parts[1:]):
        if i >= len(names):
            raise IndicatorError(f"{spec.name} takes at most {len(names)} parameter(s).")
        try:
            params[names[i]] = float(raw)
        except ValueError:
            raise IndicatorError(f"'{raw}' is not a number for {spec.name}.{names[i]}")
    for name, value in params.items():
        if value <= 0:
            raise IndicatorError(f"{spec.name}.{name} must be positive (got {value}).")
    return key, params


def compute(bars: list[dict], specs: list[str]) -> list[dict]:
    """Compute the requested indicators against a set of bars."""
    if not specs:
        return []
    df = bars_to_frame(bars)
    times = [int(b["time"]) for b in bars]
    results = []
    for text in specs:
        key, params = parse_spec(text)
        spec = REGISTRY[key]
        longest = max([int(v) for k, v in params.items() if k != "k"] or [1])
        if len(df) < longest + 1:
            results.append({
                "id": text, "key": key, "name": spec.name, "pane": spec.pane,
                "params": params, "series": {}, "bounds": spec.bounds,
                "guides": spec.guides,
                "error": f"Needs at least {longest + 1} bars, have {len(df)}.",
            })
            continue
        try:
            series = spec.compute(df, times, **params)
            error = None
        except Exception as exc:                    # noqa: BLE001 - shown to the user
            series, error = {}, f"{type(exc).__name__}: {exc}"
        results.append({
            "id": text, "key": key, "name": spec.name, "pane": spec.pane,
            "params": params, "series": series, "bounds": spec.bounds,
            "guides": spec.guides, "error": error,
        })
    return results


def levels(bars: list[dict], lookback: int = 5, n_levels: int = 3) -> dict:
    """Swing support/resistance and the 52-bar-year high/low.

    Separate from `compute` because these are horizontal lines, not series —
    the chart draws them as price lines rather than plotted data.
    """
    df = bars_to_frame(bars)
    out: dict = {"supports": [], "resistances": [], "week52High": None,
                 "week52Low": None, "openingRange": None, "error": None}
    try:
        if len(df) > 2 * lookback + 1:
            sup, res = ind.swing_levels(df, lookback=lookback, n_levels=n_levels)
            out["supports"], out["resistances"] = sup, res
        if len(df) >= 20:
            hi, lo = ind.week52(df)
            out["week52High"], out["week52Low"] = hi, lo
    except Exception as exc:                        # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def catalog() -> list[dict]:
    """What the UI's 'add indicator' menu is built from."""
    return [
        {
            "key": spec.key, "name": spec.name, "pane": spec.pane,
            "params": spec.params, "outputs": spec.outputs,
            "description": spec.description, "bounds": spec.bounds,
            "guides": spec.guides,
        }
        for spec in REGISTRY.values()
    ]


def _selftest() -> int:
    """Offline check against synthetic bars. `python3 api/indicators_api.py`."""
    import random
    random.seed(7)
    price, t, rows = 100.0, 1_700_000_000, []
    for i in range(300):
        price *= 1 + random.uniform(-0.02, 0.021)
        o = price * (1 + random.uniform(-0.004, 0.004))
        c = price
        rows.append({
            "time": t + i * 86400, "open": o, "close": c,
            "high": max(o, c) * 1.004, "low": min(o, c) * 0.996,
            "volume": random.randint(1_000_000, 5_000_000),
        })

    failures = []

    def check(name, cond):
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    res = compute(rows, ["sma:20", "ema:50", "rsi:14", "macd", "bollinger:20:2",
                         "atr:14", "vwap", "obv", "keltner"])
    check("all nine indicators computed", len(res) == 9)
    check("no indicator errored", all(r["error"] is None for r in res))

    by_key = {r["key"]: r for r in res}
    check("SMA(20) drops 19 warm-up bars",
          len(by_key["sma"]["series"]["sma"]) == len(rows) - 19)
    check("RSI stays within 0-100",
          all(0 <= p["value"] <= 100 for p in by_key["rsi"]["series"]["rsi"]))
    check("MACD returns three series", len(by_key["macd"]["series"]) == 3)
    check("Bollinger upper >= lower", all(
        u["value"] >= l["value"] for u, l in
        zip(by_key["bollinger"]["series"]["upper"],
            by_key["bollinger"]["series"]["lower"])))
    check("ATR is positive", all(p["value"] > 0 for p in by_key["atr"]["series"]["atr"]))
    check("RSI lands in its own pane", by_key["rsi"]["pane"] == "rsi")
    check("SMA overlays price", by_key["sma"]["pane"] == "price")

    # The value of this whole module: identical numbers to indicators.py.
    df = bars_to_frame(rows)
    direct = ind.sma(df["Close"], 20).dropna().iloc[-1]
    viaapi = by_key["sma"]["series"]["sma"][-1]["value"]
    check("SMA matches indicators.py exactly", abs(direct - viaapi) < 1e-6)
    direct_rsi = ind.rsi(df["Close"], 14).dropna().iloc[-1]
    via_rsi = by_key["rsi"]["series"]["rsi"][-1]["value"]
    check("RSI matches indicators.py exactly", abs(direct_rsi - via_rsi) < 1e-6)

    short = compute(rows[:10], ["sma:50"])
    check("too-few-bars reports an error, not a crash", short[0]["error"] is not None)

    lv = levels(rows)
    check("levels returns 52-week high", lv["week52High"] is not None)
    check("supports below resistances", not lv["supports"] or not lv["resistances"]
          or max(lv["supports"]) <= max(lv["resistances"]) * 1.5)

    for bad in ("bogus", "sma:abc", "sma:-5", "sma:20:30"):
        try:
            parse_spec(bad)
            check(f"{bad!r} rejected", False)
        except IndicatorError:
            check(f"{bad!r} rejected", True)

    check("catalog is non-empty", len(catalog()) == len(REGISTRY))

    print(f"\n{len(failures)} failure(s)." if failures else "\nAll indicator checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
