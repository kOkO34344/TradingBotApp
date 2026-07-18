"""
Phase 2 of the trading agent plan: one narrow, explicit strategy, backtested rigorously.

Strategy: 20/50-day SMA crossover, long-only, one ticker at a time.
  - Buy when SMA(20) crosses above SMA(50).
  - Close the position when SMA(20) crosses back below SMA(50).

This script:
  1. Pulls daily price history for a fixed watchlist of liquid large-caps + SPY (benchmark).
  2. Runs the strategy in-sample (2010-2018) and out-of-sample (2019-present).
  3. Applies a 0.1% per-trade cost to approximate commission + slippage
     (Alpaca itself is commission-free for stocks, but spread/slippage is real).
  4. Compares strategy CAGR/Sharpe/drawdown against simply buying and holding
     that same ticker, and against SPY buy-and-hold.
  5. Writes a results table to CSV and prints a summary.

Honesty note: this script's job is to run without bugs and produce a trustworthy
number. It is not designed to produce a flattering number. If the strategy loses
to buy-and-hold, that result gets reported, not massaged.
"""

import warnings
import sys
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

warnings.filterwarnings("ignore")

WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "JPM", "JNJ", "PG", "XOM", "KO", "DIS"]
BENCHMARK = "SPY"
ALL_TICKERS = WATCHLIST + [BENCHMARK]

START = "2010-01-01"
END = "2026-07-18"
IN_SAMPLE_END = "2018-12-31"
OUT_OF_SAMPLE_START = "2019-01-01"

CASH = 10_000
COMMISSION = 0.001  # 0.1% per trade, approximating spread + slippage
DATA_DIR = Path(__file__).parent / "price_data"
DATA_DIR.mkdir(exist_ok=True)


def fetch(ticker: str) -> pd.DataFrame:
    """Download (or load cached) daily OHLCV data for a ticker."""
    cache_path = DATA_DIR / f"{ticker}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
    else:
        raw = yf.download(ticker, start=START, end=END, progress=False, auto_adjust=True)
        if raw.empty:
            raise RuntimeError(f"No data returned for {ticker}")
        # yfinance returns MultiIndex columns (Price, Ticker) for recent versions;
        # flatten to plain OHLCV columns regardless of yfinance version.
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.to_csv(cache_path)
    df.index = pd.to_datetime(df.index)
    return df.dropna()


class SmaCross(Strategy):
    n1 = 20
    n2 = 50

    def init(self):
        close = self.data.Close
        self.sma1 = self.I(lambda x: pd.Series(x).rolling(self.n1).mean(), close)
        self.sma2 = self.I(lambda x: pd.Series(x).rolling(self.n2).mean(), close)

    def next(self):
        if crossover(self.sma1, self.sma2):
            self.buy()
        elif crossover(self.sma2, self.sma1):
            self.position.close()


def buy_and_hold_stats(df: pd.DataFrame) -> dict:
    """Benchmark: buy on day 1, hold to the end, no trading costs (nothing to trade)."""
    start_price = df["Close"].iloc[0]
    end_price = df["Close"].iloc[-1]
    n_days = (df.index[-1] - df.index[0]).days
    years = n_days / 365.25
    total_return = end_price / start_price - 1
    cagr = (end_price / start_price) ** (1 / years) - 1 if years > 0 else float("nan")
    running_max = df["Close"].cummax()
    drawdown = (df["Close"] - running_max) / running_max
    max_dd = drawdown.min()
    daily_ret = df["Close"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else float("nan")
    return {
        "Return [%]": total_return * 100,
        "CAGR [%]": cagr * 100,
        "Max. Drawdown [%]": max_dd * 100,
        "Sharpe Ratio": sharpe,
        "# Trades": 1,
        "Win Rate [%]": np.nan,
    }


def run_period(df: pd.DataFrame, start: str, end: str) -> dict:
    sliced = df.loc[start:end]
    if len(sliced) < 60:
        raise RuntimeError(f"Not enough data in period {start}..{end} ({len(sliced)} rows)")
    bt = Backtest(sliced, SmaCross, cash=CASH, commission=COMMISSION, exclusive_orders=True)
    stats = bt.run()
    bh = buy_and_hold_stats(sliced)
    return {
        "strategy_return_pct": stats["Return [%]"],
        "strategy_cagr_pct": _cagr(sliced, stats["Return [%]"]),
        "strategy_max_dd_pct": stats["Max. Drawdown [%]"],
        "strategy_sharpe": stats["Sharpe Ratio"],
        "strategy_trades": stats["# Trades"],
        "strategy_win_rate_pct": stats["Win Rate [%]"],
        "bh_return_pct": bh["Return [%]"],
        "bh_cagr_pct": bh["CAGR [%]"],
        "bh_max_dd_pct": bh["Max. Drawdown [%]"],
        "bh_sharpe": bh["Sharpe Ratio"],
    }


def _cagr(df: pd.DataFrame, total_return_pct: float) -> float:
    n_days = (df.index[-1] - df.index[0]).days
    years = n_days / 365.25
    if years <= 0:
        return float("nan")
    total_return = total_return_pct / 100
    return (((1 + total_return) ** (1 / years)) - 1) * 100


def main():
    print(f"Fetching data for {len(ALL_TICKERS)} tickers...", file=sys.stderr)
    data = {}
    for t in ALL_TICKERS:
        try:
            data[t] = fetch(t)
            print(f"  {t}: {len(data[t])} rows ({data[t].index[0].date()} to {data[t].index[-1].date()})",
                  file=sys.stderr)
        except Exception as e:
            print(f"  {t}: FAILED - {e}", file=sys.stderr)

    spy = data.get(BENCHMARK)
    if spy is None:
        raise RuntimeError("Benchmark SPY failed to download; cannot compare.")

    periods = {
        "full_history": (START, END),
        "in_sample_2010_2018": (START, IN_SAMPLE_END),
        "out_of_sample_2019_present": (OUT_OF_SAMPLE_START, END),
    }

    rows = []
    for ticker in WATCHLIST:
        if ticker not in data:
            continue
        for period_name, (p_start, p_end) in periods.items():
            try:
                result = run_period(data[ticker], p_start, p_end)
            except Exception as e:
                print(f"  SKIP {ticker} / {period_name}: {e}", file=sys.stderr)
                continue
            spy_bh = buy_and_hold_stats(spy.loc[p_start:p_end])
            rows.append({
                "ticker": ticker,
                "period": period_name,
                **result,
                "spy_bh_return_pct": spy_bh["Return [%]"],
                "spy_bh_cagr_pct": spy_bh["CAGR [%]"],
            })

    results = pd.DataFrame(rows)
    out_csv = Path(__file__).parent / "backtest_results.csv"
    results.to_csv(out_csv, index=False)
    print(f"\nWrote {len(results)} rows to {out_csv}", file=sys.stderr)

    # Summary: does the strategy beat buy-and-hold (same ticker) and SPY, out of sample?
    oos = results[results["period"] == "out_of_sample_2019_present"].copy()
    oos["beats_own_bh"] = oos["strategy_cagr_pct"] > oos["bh_cagr_pct"]
    oos["beats_spy"] = oos["strategy_cagr_pct"] > oos["spy_bh_cagr_pct"]

    print("\n=== OUT-OF-SAMPLE (2019-present) SUMMARY ===")
    print(oos[["ticker", "strategy_cagr_pct", "bh_cagr_pct", "spy_bh_cagr_pct",
                "strategy_sharpe", "strategy_max_dd_pct", "strategy_trades",
                "beats_own_bh", "beats_spy"]].to_string(index=False))

    n = len(oos)
    print(f"\nStrategy beat its own ticker's buy-and-hold in {oos['beats_own_bh'].sum()}/{n} tickers.")
    print(f"Strategy beat SPY buy-and-hold in {oos['beats_spy'].sum()}/{n} tickers.")
    print(f"Average strategy CAGR: {oos['strategy_cagr_pct'].mean():.2f}%  "
          f"Average buy-and-hold CAGR: {oos['bh_cagr_pct'].mean():.2f}%  "
          f"SPY CAGR: {oos['spy_bh_cagr_pct'].mean():.2f}%")


if __name__ == "__main__":
    main()
