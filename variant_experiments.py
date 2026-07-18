"""Test risk-management variants of the SMA crossover, out-of-sample (2019-present).

Variants:
  V1 baseline    : SMA 20/50 cross, all-in, exit on cross-under.
  V2 trend filter: same, but only enter when Close > SMA200.
  V3 ATR stop    : baseline + 3*ATR(14) trailing stop.
  V4 risk-sized  : trend filter + 2*ATR stop, position sized so a stop-out
                   loses ~2% of equity (classic fixed-fractional risk).
All variants pay the same 0.1%/trade cost. Same data, same period, same harness.
"""
import warnings, sys
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
sys.path.insert(0, ".")
from sma_crossover_backtest import fetch, buy_and_hold_stats, WATCHLIST

START, END = "2019-01-01", "2026-07-18"
CASH, COMM = 10_000, 0.001


def sma(x, n): return pd.Series(x).rolling(n).mean()


def atr_series(df, n=14):
    h, l, c = df.High, df.Low, df.Close
    prev_c = pd.Series(c).shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


class V1(Strategy):
    def init(self):
        self.s1 = self.I(sma, self.data.Close, 20)
        self.s2 = self.I(sma, self.data.Close, 50)
    def next(self):
        if crossover(self.s1, self.s2): self.buy()
        elif crossover(self.s2, self.s1): self.position.close()


class V2(Strategy):
    def init(self):
        self.s1 = self.I(sma, self.data.Close, 20)
        self.s2 = self.I(sma, self.data.Close, 50)
        self.s200 = self.I(sma, self.data.Close, 200)
    def next(self):
        if crossover(self.s1, self.s2) and self.data.Close[-1] > self.s200[-1]:
            self.buy()
        elif crossover(self.s2, self.s1):
            self.position.close()


class V3(Strategy):
    def init(self):
        self.s1 = self.I(sma, self.data.Close, 20)
        self.s2 = self.I(sma, self.data.Close, 50)
        df = pd.DataFrame({"High": self.data.High, "Low": self.data.Low, "Close": self.data.Close})
        self.atr = self.I(lambda _: atr_series(df).values, self.data.Close, name="ATR")
        self.trail = None
    def next(self):
        price = self.data.Close[-1]
        if self.position:
            self.trail = max(self.trail, price - 3 * self.atr[-1])
            if price < self.trail or crossover(self.s2, self.s1):
                self.position.close(); self.trail = None
        elif crossover(self.s1, self.s2):
            self.buy(); self.trail = price - 3 * self.atr[-1]


class V4(Strategy):
    risk_frac = 0.02
    def init(self):
        self.s1 = self.I(sma, self.data.Close, 20)
        self.s2 = self.I(sma, self.data.Close, 50)
        self.s200 = self.I(sma, self.data.Close, 200)
        df = pd.DataFrame({"High": self.data.High, "Low": self.data.Low, "Close": self.data.Close})
        self.atr = self.I(lambda _: atr_series(df).values, self.data.Close, name="ATR")
        self.trail = None
    def next(self):
        price = self.data.Close[-1]
        if self.position:
            self.trail = max(self.trail, price - 2 * self.atr[-1])
            if price < self.trail or crossover(self.s2, self.s1):
                self.position.close(); self.trail = None
        elif crossover(self.s1, self.s2) and price > self.s200[-1]:
            stop_dist = 2 * self.atr[-1]
            if stop_dist <= 0 or np.isnan(stop_dist): return
            units = int((self.equity * self.risk_frac) / stop_dist)
            max_units = int(self.equity * 0.99 / price)
            units = min(units, max_units)
            if units >= 1:
                self.buy(size=units); self.trail = price - stop_dist


def cagr_pct(df, total_ret_pct):
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    return (((1 + total_ret_pct / 100) ** (1 / yrs)) - 1) * 100


def main():
    variants = {"V1 baseline": V1, "V2 +trend filter": V2, "V3 +ATR stop": V3, "V4 risk-sized": V4}
    agg = {name: [] for name in variants}
    bh_list, per_ticker = [], []

    for t in WATCHLIST:
        df = fetch(t).loc[START:END]
        bh = buy_and_hold_stats(df)
        bh_list.append(bh["CAGR [%]"])
        row = {"ticker": t, "BH": bh["CAGR [%]"]}
        for name, cls in variants.items():
            stats = Backtest(df, cls, cash=CASH, commission=COMM, exclusive_orders=True).run()
            c = cagr_pct(df, stats["Return [%]"])
            agg[name].append({"cagr": c, "dd": stats["Max. Drawdown [%]"],
                              "sharpe": stats["Sharpe Ratio"], "trades": stats["# Trades"]})
            row[name] = c
        per_ticker.append(row)

    pt = pd.DataFrame(per_ticker).set_index("ticker").round(1)
    print("Per-ticker CAGR % (2019-present, after costs):")
    print(pt.to_string())
    print(f"\n{'variant':<18}{'avg CAGR':>9}{'avg maxDD':>10}{'avg Sharpe':>11}{'avg trades':>11}{'beats BH':>9}")
    bh_avg = np.mean(bh_list)
    for name in variants:
        rows = agg[name]
        beats = sum(r["cagr"] > b for r, b in zip(rows, bh_list))
        print(f"{name:<18}{np.mean([r['cagr'] for r in rows]):>8.1f}%"
              f"{np.mean([r['dd'] for r in rows]):>9.1f}%"
              f"{np.mean([r['sharpe'] for r in rows]):>11.2f}"
              f"{np.mean([r['trades'] for r in rows]):>11.1f}"
              f"{beats:>7}/10")
    print(f"{'Buy & hold':<18}{bh_avg:>8.1f}%   (avg maxDD -42.4%)   SPY CAGR: 17.3%")


if __name__ == "__main__":
    main()
