"""Strategy family shootout — out-of-sample 2019-present, same 10 tickers, 0.1%/trade.

Families tested:
  1. Trend (SMA 20/50 cross)         — already known: avg 6.1% CAGR
  2. Trend, slow (golden cross 50/200)
  3. Breakout (Donchian: buy 20d high, exit 10d low)
  4. Mean reversion (RSI-2: buy RSI2<10 above SMA200, exit RSI2>70)
  5. Momentum rotation (hold top 3 of 10 by 12-month return, monthly rebalance)
  6. Buy & hold (each ticker) and SPY
"""
import warnings
warnings.filterwarnings("ignore")
import sys
import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
sys.path.insert(0, ".")
from sma_crossover_backtest import fetch, buy_and_hold_stats, WATCHLIST

START, END = "2019-01-01", "2026-07-18"
CASH, COMM = 10_000, 0.001


def sma(x, n): return pd.Series(x).rolling(n).mean()


def rsi(series, n=2):
    s = pd.Series(series)
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn
    return 100 - 100 / (1 + rs)


class SmaCross2050(Strategy):
    def init(self):
        self.s1 = self.I(sma, self.data.Close, 20)
        self.s2 = self.I(sma, self.data.Close, 50)
    def next(self):
        if crossover(self.s1, self.s2): self.buy()
        elif crossover(self.s2, self.s1): self.position.close()


class GoldenCross(Strategy):
    def init(self):
        self.s1 = self.I(sma, self.data.Close, 50)
        self.s2 = self.I(sma, self.data.Close, 200)
    def next(self):
        if crossover(self.s1, self.s2): self.buy()
        elif crossover(self.s2, self.s1): self.position.close()


class Donchian(Strategy):
    def init(self):
        self.hi = self.I(lambda x: pd.Series(x).rolling(20).max(), self.data.High)
        self.lo = self.I(lambda x: pd.Series(x).rolling(10).min(), self.data.Low)
    def next(self):
        if not self.position and self.data.Close[-1] >= self.hi[-2]:
            self.buy()
        elif self.position and self.data.Close[-1] <= self.lo[-2]:
            self.position.close()


class Rsi2MeanRev(Strategy):
    def init(self):
        self.r = self.I(rsi, self.data.Close, 2)
        self.s200 = self.I(sma, self.data.Close, 200)
    def next(self):
        if not self.position and self.r[-1] < 10 and self.data.Close[-1] > self.s200[-1]:
            self.buy()
        elif self.position and self.r[-1] > 70:
            self.position.close()


def cagr_pct(df, total_ret_pct):
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    return (((1 + total_ret_pct / 100) ** (1 / yrs)) - 1) * 100


def momentum_rotation(closes: pd.DataFrame, top_n=3, cost=COMM):
    """Monthly: rank by 12-month return, hold equal-weight top N."""
    monthly = closes.resample("ME").last()
    mom = monthly.pct_change(12)
    daily_ret = closes.pct_change()
    equity = 1.0
    curve = []
    holdings = []
    prev = set()
    for i in range(12, len(monthly) - 1):
        ranked = mom.iloc[i].dropna().sort_values(ascending=False)
        top = list(ranked.index[:top_n])
        month_start, month_end = monthly.index[i], monthly.index[i + 1]
        seg = daily_ret.loc[month_start:month_end, top].iloc[1:]
        seg_ret = (1 + seg.mean(axis=1)).prod() - 1
        turnover = len(set(top) ^ prev) / max(top_n, 1)
        equity *= (1 + seg_ret) * (1 - cost * turnover)
        prev = set(top)
        curve.append((month_end, equity))
        holdings.append(top)
    curve = pd.Series(dict(curve))
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    cagr = (curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1
    dd = ((curve - curve.cummax()) / curve.cummax()).min()
    mret = curve.pct_change().dropna()
    sharpe = mret.mean() / mret.std() * np.sqrt(12) if mret.std() > 0 else np.nan
    return cagr * 100, dd * 100, sharpe


def main():
    data = {t: fetch(t) for t in WATCHLIST}
    spy = fetch("SPY").loc[START:END]
    spy_bh = buy_and_hold_stats(spy)

    families = {"SMA 20/50": SmaCross2050, "Golden 50/200": GoldenCross,
                "Donchian 20/10": Donchian, "RSI-2 meanrev": Rsi2MeanRev}
    print(f"{'family':<16}{'avg CAGR':>9}{'avg maxDD':>10}{'avg Sharpe':>11}{'beats BH':>9}")
    bh_cagrs = {}
    for t in WATCHLIST:
        bh_cagrs[t] = buy_and_hold_stats(data[t].loc[START:END])["CAGR [%]"]

    for name, cls in families.items():
        rows = []
        for t in WATCHLIST:
            df = data[t].loc[START:END]
            # Golden cross needs 200d warmup: extend lookback
            if cls in (GoldenCross, Rsi2MeanRev):
                df = data[t].loc["2018-01-01":END]
            stats = Backtest(df, cls, cash=CASH, commission=COMM, exclusive_orders=True).run()
            rows.append({"t": t, "cagr": cagr_pct(df, stats["Return [%]"]),
                         "dd": stats["Max. Drawdown [%]"], "sharpe": stats["Sharpe Ratio"]})
        beats = sum(r["cagr"] > bh_cagrs[r["t"]] for r in rows)
        print(f"{name:<16}{np.mean([r['cagr'] for r in rows]):>8.1f}%"
              f"{np.mean([r['dd'] for r in rows]):>9.1f}%"
              f"{np.mean([r['sharpe'] for r in rows]):>11.2f}{beats:>7}/10")

    closes = pd.DataFrame({t: data[t]["Close"] for t in WATCHLIST}).loc["2017-06-01":END]
    mc, mdd, msh = momentum_rotation(closes)
    print(f"{'Momentum top-3':<16}{mc:>8.1f}%{mdd:>9.1f}%{msh:>11.2f}{'n/a':>10}")
    print(f"{'Avg buy&hold':<16}{np.mean(list(bh_cagrs.values())):>8.1f}%")
    print(f"{'SPY buy&hold':<16}{spy_bh['CAGR [%]']:>8.1f}%{spy_bh['Max. Drawdown [%]']:>9.1f}%"
          f"{spy_bh['Sharpe Ratio']:>11.2f}")


if __name__ == "__main__":
    main()
