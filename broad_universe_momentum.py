"""
Broad-universe retest of momentum rotation (top-3, monthly rebalance).

The mega-cap-10 momentum result (~18.5% CAGR vs SPY 16%, see CLAUDE.md) was
flagged as possibly flattered by hand-picking 10 large, well-known survivors.
This script reruns the identical strategy (see momentum_rotation() in
strategy_shootout.py, reused unchanged here) against today's ~500 S&P 500
constituents instead of 10 tickers.

HONESTY CAVEAT — read before trusting the number this prints:
Universe is fetched from today's S&P 500 membership (Wikipedia), not a
point-in-time historical membership snapshot. That means every name that
was removed from the index (bankruptcy, acquisition, demotion) between
2019 and today is absent from this test. This does NOT eliminate
survivorship bias, it only dilutes it from 10 names to ~500. A stronger
test would need point-in-time index membership and delisted-ticker prices,
which we do not have. Treat a positive result here as "the edge doesn't
depend on which 10 mega-caps you picked," not as "survivorship-bias-free."

Some current constituents will be dropped from the test automatically:
recent IPOs / spinoffs without a full 2017-06-01..2026-07-18 price history,
and any ticker yfinance fails to serve. Dropped tickers are reported, not
silently discarded.
"""
import io
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from sma_crossover_backtest import buy_and_hold_stats
from strategy_shootout import momentum_rotation

MOMENTUM_START = "2017-06-01"  # matches strategy_shootout.py (12mo lookback warmup before 2019 OOS start)
FETCH_START = "2015-01-01"  # buffer before MOMENTUM_START
END = "2026-07-18"
OUT_OF_SAMPLE_START = "2019-01-01"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Deliberately isolated from price_data/ — that shared cache gets silently
# truncated to ~2yr by paper_trader.py's force-refetch of its ranking
# universe (see broad_universe_momentum.py run of 2026-07-23, which caught
# 10 mega-caps corrupted to 530 rows). Backtests must own their own cache.
DATA_DIR = Path(__file__).parent / "price_data_backtest"
DATA_DIR.mkdir(exist_ok=True)


def fetch(ticker: str) -> pd.DataFrame:
    cache_path = DATA_DIR / f"{ticker}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
    else:
        raw = yf.download(ticker, start=FETCH_START, end=END, progress=False, auto_adjust=True)
        if raw.empty:
            raise RuntimeError(f"No data returned for {ticker}")
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.to_csv(cache_path)
    df.index = pd.to_datetime(df.index)
    return df.dropna()


def fetch_sp500_constituents() -> list[str]:
    headers = {"User-Agent": "research script; contact koko06ivanov@gmail.com"}
    r = requests.get(SP500_URL, headers=headers, timeout=15)
    r.raise_for_status()
    df = pd.read_html(io.StringIO(r.text))[0]
    tickers = df["Symbol"].str.replace(".", "-", regex=False).tolist()  # BRK.B -> BRK-B for yfinance
    return sorted(set(tickers))


def main():
    tickers = fetch_sp500_constituents()
    print(f"S&P 500 constituents fetched (today's list): {len(tickers)}")

    closes = {}
    skipped = []
    for i, t in enumerate(tickers, 1):
        try:
            df = fetch(t)
            df = df.loc[MOMENTUM_START:END]
            if df.empty or df.index[0] > pd.Timestamp(MOMENTUM_START) + pd.Timedelta(days=60):
                skipped.append((t, "insufficient history"))
                continue
            closes[t] = df["Close"]
        except Exception as e:
            skipped.append((t, str(e)[:80]))
        if i % 50 == 0:
            print(f"  fetched {i}/{len(tickers)}...")

    print(f"\nUsable tickers: {len(closes)} / {len(tickers)}")
    print(f"Skipped: {len(skipped)}")
    if skipped:
        print("  " + ", ".join(f"{t} ({reason})" for t, reason in skipped[:30]))
        if len(skipped) > 30:
            print(f"  ... and {len(skipped) - 30} more")

    closes_df = pd.DataFrame(closes).loc[MOMENTUM_START:END]

    spy = fetch("SPY").loc[OUT_OF_SAMPLE_START:END]
    spy_bh = buy_and_hold_stats(spy)

    cagr, dd, sharpe = momentum_rotation(closes_df, top_n=3)

    print("\n" + "=" * 60)
    print("BROAD-UNIVERSE MOMENTUM ROTATION (top-3, monthly, S&P 500 today's list)")
    print("=" * 60)
    print(f"{'Momentum top-3 (broad)':<28}{cagr:>8.1f}% CAGR{dd:>9.1f}% maxDD{sharpe:>8.2f} Sharpe")
    print(f"{'SPY buy & hold':<28}{spy_bh['CAGR [%]']:>8.1f}% CAGR")
    print()
    print("Reference — mega-cap-10 result from strategy_shootout.py (do not re-derive, already logged in CLAUDE.md):")
    print("  Momentum top-3 (10 mega-caps)  ~18.5% CAGR, -21.7% maxDD, vs SPY ~16% CAGR, -33.7% maxDD")
    print()
    print("NOT a like-for-like comparison to the number above: top-3 of ~460 usable names is a far")
    print("more selective cut (~0.6% of the pool) than top-3 of 10 (30% of the pool). This measures")
    print("'same 3-position portfolio, much larger selection pool' — a different, higher-variance,")
    print("more concentrated-in-extreme-winners strategy, not the same strategy proven at scale.")
    print("The gap between the two numbers should NOT be read as 'the mega-cap curation understated")
    print("the edge' — it's largely a selectivity effect. An apples-to-apples test would sweep top_n")
    print("(e.g. 3/10/30/50) or fix the selection ratio; neither was done here.")
    print()
    print("CAVEAT: today's-list universe, not point-in-time. Survivorship bias diluted, not eliminated.")


if __name__ == "__main__":
    main()
