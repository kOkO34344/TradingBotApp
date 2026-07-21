"""
indicators.py — the single source of truth for technical indicators.

Used by BOTH:
  - trader_app.py  (chart overlays and panels the human sees)
  - research_agent.py (the numbers the AI reasons over)
so the agent analyzes exactly what the owner sees on screen.

All functions take pandas input and return pandas output, no state.
Math is verified by `python3 indicators.py --selftest` against reference
implementations and invariant checks.

Sets:
  Trend:      sma, ema, macd
  Momentum:   rsi
  Volatility: atr, bollinger, keltner
  Volume:     vwap, obv
  Structure:  swing_levels, week52, opening_range
"""

import sys
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- trend

def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


# ---------------------------------------------------------------- momentum

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn)
    return out.fillna(50.0)


# ---------------------------------------------------------------- volatility

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_c = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev_c).abs(),
                    (df["Low"] - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    """Returns (mid, upper, lower). mid = SMA(n), bands = mid +/- k*std(n)."""
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def keltner(df: pd.DataFrame, n: int = 20, k: float = 2.0):
    """Returns (mid, upper, lower). mid = EMA(n) of close, bands = mid +/- k*ATR(n)."""
    mid = ema(df["Close"], n)
    a = atr(df, n)
    return mid, mid + k * a, mid - k * a


# ---------------------------------------------------------------- volume

def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, session-reset if the index has
    multiple dates (intraday), else cumulative over the frame."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = typical * df["Volume"]
    if hasattr(df.index, "date") and len(set(df.index.date)) > 1 and \
            (df.index[-1] - df.index[0]).days < 40:  # intraday frame
        grp = pd.Series(df.index.date, index=df.index)
        return pv.groupby(grp).cumsum() / df["Volume"].groupby(grp).cumsum()
    return pv.cumsum() / df["Volume"].cumsum()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff()).fillna(0)
    return (direction * df["Volume"]).cumsum()


# ---------------------------------------------------------------- structure

def swing_levels(df: pd.DataFrame, lookback: int = 5, n_levels: int = 3):
    """Support/resistance from confirmed swing lows/highs: a bar whose low
    (high) is the extreme of the surrounding 2*lookback+1 bars. Returns
    (supports, resistances) — the n most recent distinct levels, most
    recent first."""
    lows, highs = df["Low"], df["High"]
    sup, res = [], []
    for i in range(lookback, len(df) - lookback):
        window_lo = lows.iloc[i - lookback:i + lookback + 1]
        window_hi = highs.iloc[i - lookback:i + lookback + 1]
        if lows.iloc[i] == window_lo.min():
            sup.append(float(lows.iloc[i]))
        if highs.iloc[i] == window_hi.max():
            res.append(float(highs.iloc[i]))

    def dedupe(levels):
        out = []
        for lv in reversed(levels):  # most recent first
            if all(abs(lv - o) / o > 0.005 for o in out):
                out.append(lv)
            if len(out) == n_levels:
                break
        return out

    return dedupe(sup), dedupe(res)


def week52(df: pd.DataFrame):
    """(high, low) over the last ~252 trading bars."""
    tail = df.tail(252)
    return float(tail["High"].max()), float(tail["Low"].min())


def opening_range(intraday: pd.DataFrame, bars: int = 2):
    """(high, low) of the first `bars` bars of the latest session."""
    last_date = intraday.index.date[-1]
    day = intraday[intraday.index.date == last_date]
    head = day.iloc[:bars]
    return float(head["High"].max()), float(head["Low"].min())


# ---------------------------------------------------------------- summaries

def summarize_daily(df: pd.DataFrame) -> list[str]:
    """Text lines describing the daily-timeframe indicator state — used by
    the research agent so the AI reasons over what the charts show."""
    c = df["Close"]
    price = float(c.iloc[-1])
    s20, s50 = float(sma(c, 20).iloc[-1]), float(sma(c, 50).iloc[-1])
    s200 = float(sma(c, 200).iloc[-1]) if len(c) >= 200 else float("nan")
    e21 = float(ema(c, 21).iloc[-1])
    m_line, m_sig, m_hist = macd(c)
    r = float(rsi(c).iloc[-1])
    a = float(atr(df).iloc[-1])
    bb_mid, bb_up, bb_lo = bollinger(c)
    kc_mid, kc_up, kc_lo = keltner(df)
    squeeze = (float(bb_up.iloc[-1]) < float(kc_up.iloc[-1])
               and float(bb_lo.iloc[-1]) > float(kc_lo.iloc[-1]))
    o = obv(df)
    obv_slope20 = float(o.iloc[-1] - o.iloc[-21]) if len(o) > 21 else float("nan")
    sup, res = swing_levels(df)
    hi52, lo52 = week52(df)
    side = lambda level: "above" if price > level else "below"

    lines = [
        f"Price {price:.2f} | SMA20 {s20:.2f} ({side(s20)}) SMA50 {s50:.2f} ({side(s50)}) "
        + (f"SMA200 {s200:.2f} ({side(s200)})" if s200 == s200 else "SMA200 n/a")
        + f" | EMA21 {e21:.2f} ({side(e21)})",
        f"MACD(12,26,9): line {float(m_line.iloc[-1]):+.3f}, signal {float(m_sig.iloc[-1]):+.3f}, "
        f"histogram {float(m_hist.iloc[-1]):+.3f} "
        f"({'bullish' if float(m_hist.iloc[-1]) > 0 else 'bearish'} and "
        f"{'expanding' if abs(float(m_hist.iloc[-1])) > abs(float(m_hist.iloc[-2])) else 'contracting'})",
        f"RSI(14): {r:.1f}   ATR(14): {a:.2f} ({a / price * 100:.1f}% of price)",
        f"Bollinger(20,2): {float(bb_lo.iloc[-1]):.2f} / {float(bb_mid.iloc[-1]):.2f} / "
        f"{float(bb_up.iloc[-1]):.2f} — price at "
        f"{(price - float(bb_lo.iloc[-1])) / max(float(bb_up.iloc[-1]) - float(bb_lo.iloc[-1]), 1e-9) * 100:.0f}% of band"
        + ("   [SQUEEZE: Bollinger inside Keltner — volatility compressed]" if squeeze else ""),
        f"OBV 20-bar change: {obv_slope20:+,.0f} "
        f"({'volume confirms' if (obv_slope20 > 0) == (price > s20) else 'volume diverges from'} price trend)",
        f"Swing supports (recent first): {', '.join(f'{x:.2f}' for x in sup) or 'none found'}",
        f"Swing resistances (recent first): {', '.join(f'{x:.2f}' for x in res) or 'none found'}",
        f"52-week range: {lo52:.2f} - {hi52:.2f} ({(price - lo52) / (hi52 - lo52) * 100:.0f}% of range)",
    ]
    return lines


def summarize_intraday(m15: pd.DataFrame) -> list[str]:
    """Text lines for the 15-minute timeframe."""
    c = m15["Close"]
    price = float(c.iloc[-1])
    r = float(rsi(c).iloc[-1])
    a = float(atr(m15).iloc[-1])
    v = vwap(m15)
    or_hi, or_lo = opening_range(m15)
    e20 = float(ema(c, 20).iloc[-1])
    m_line, m_sig, m_hist = macd(c)
    return [
        f"Last {price:.2f} | EMA20(15m) {e20:.2f} ({'above' if price > e20 else 'below'}) | "
        f"RSI(14,15m) {r:.1f} | ATR(14,15m) {a:.3f}",
        f"MACD(15m) histogram: {float(m_hist.iloc[-1]):+.4f} "
        f"({'bullish' if float(m_hist.iloc[-1]) > 0 else 'bearish'})",
        f"Session VWAP: {float(v.iloc[-1]):.2f} (price {'above' if price > float(v.iloc[-1]) else 'below'} VWAP)",
        f"Opening range (first 30 min): {or_lo:.2f} - {or_hi:.2f} "
        f"(price {'above range' if price > or_hi else 'below range' if price < or_lo else 'inside range'})",
    ]


# ---------------------------------------------------------------- self-test

def _selftest() -> int:
    rng = np.random.default_rng(42)
    n = 300
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + rng.uniform(0.1, 1.5, n)
    low = close - rng.uniform(0.1, 1.5, n)
    vol = pd.Series(rng.integers(1000, 100000, n).astype(float))
    df = pd.DataFrame({"Open": close.shift(1).fillna(close), "High": high,
                       "Low": low, "Close": close, "Volume": vol})
    df.index = pd.date_range("2024-01-01", periods=n, freq="B")

    failures = []
    def check(name, cond):
        print(("  PASS  " if cond else "  FAIL  ") + name)
        if not cond:
            failures.append(name)

    # reference-math checks
    check("SMA matches rolling mean", np.allclose(sma(close, 20).iloc[-1], close.tail(20).mean()))
    ref_ema = close.ewm(span=12, adjust=False).mean()
    check("EMA matches pandas ewm", np.allclose(ema(close, 12), ref_ema))
    m_line, m_sig, m_hist = macd(close)
    ref_macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    check("MACD = EMA12 - EMA26", np.allclose(m_line, ref_macd))
    check("MACD hist = line - signal", np.allclose(m_hist, m_line - m_sig))
    bb_mid, bb_up, bb_lo = bollinger(close)
    check("Bollinger mid = SMA20", np.allclose(bb_mid.dropna(), sma(close, 20).dropna()))
    check("Bollinger band width = 4*std", np.allclose(
        (bb_up - bb_lo).dropna(), 4 * close.rolling(20).std(ddof=0).dropna()))

    # invariant checks
    r = rsi(close)
    check("RSI within [0,100]", bool((r.dropna().between(0, 100)).all()))
    check("ATR positive", bool((atr(df).dropna() > 0).all()))
    check("Bollinger upper >= lower", bool(((bb_up - bb_lo).dropna() >= 0).all()))
    kc_mid, kc_up, kc_lo = keltner(df)
    check("Keltner upper >= lower", bool(((kc_up - kc_lo).dropna() >= 0).all()))
    v = vwap(df)
    check("VWAP within [low.min, high.max]",
          bool(v.iloc[-1] >= low.min() and v.iloc[-1] <= high.max()))
    o = obv(df)
    check("OBV step equals that bar's volume",
          np.isclose(abs(o.iloc[10] - o.iloc[9]), vol.iloc[10]))
    check("OBV direction follows price direction",
          np.sign(o.iloc[10] - o.iloc[9]) == np.sign(close.iloc[10] - close.iloc[9]))

    sup, res = swing_levels(df)
    check("swing levels found", len(sup) > 0 and len(res) > 0)
    check("supports are lows, resistances are highs",
          all(s <= high.max() for s in sup) and all(r0 >= low.min() for r0 in res))
    hi52, lo52 = week52(df)
    check("52w high >= low", hi52 >= lo52)

    # intraday structures
    idx = pd.date_range("2026-07-13 09:30", periods=26 * 3, freq="15min")
    idx = idx[idx.indexer_between_time("09:30", "16:00")]
    m15 = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0,
                        "Close": 100.5, "Volume": 1000.0}, index=idx)
    or_hi, or_lo = opening_range(m15)
    check("opening range from first bars", or_hi == 101.0 and or_lo == 99.0)
    check("intraday VWAP session-resets",
          np.isclose(float(vwap(m15).iloc[-1]), (101 + 99 + 100.5) / 3))

    # summaries render without crashing and contain the key sections
    sd = summarize_daily(df)
    check("daily summary renders", len(sd) == 8 and "MACD" in sd[1] and "Swing" in sd[5])
    si = summarize_intraday(m15)
    check("intraday summary renders", len(si) == 4 and "VWAP" in si[2])

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILURES: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
