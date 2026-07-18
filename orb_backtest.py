"""Opening Range Breakout (Zarattini/Aziz 2023 rules) on QQQ 5-minute bars.

Rules from the paper:
  - Direction: if first 5-min bar closes up, go long at open of second bar;
    if down, go short. Doji (open==close) -> no trade.
  - Stop: low (long) / high (short) of the first 5-min bar.
  - Target: 10R (10x the entry-to-stop distance). Exit at close if neither hit.
  - Risk 1% of account per trade, commission-adjusted.

Data caveat: free 5-minute data only goes back ~60 days, so this is a smoke
test of the mechanics, NOT a validation of the strategy. The paper's own
result (2016-2023, annualized alpha ~33%) is the long-horizon evidence.
"""
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import yfinance as yf

RISK_PCT = 0.01
TARGET_R = 10
CASH = 10_000
COMMISSION_PER_SHARE = 0.0005  # Alpaca-ish / paper used similar
MAX_LEVERAGE = 4  # intraday margin


def run():
    df = yf.download("QQQ", period="60d", interval="5m", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df["date"] = df.index.date

    equity = CASH
    trades = []
    for date, day in df.groupby("date"):
        day = day.between_time("09:30", "15:55")
        if len(day) < 10:
            continue
        first = day.iloc[0]
        o, c = first["Open"], first["Close"]
        if c == o:
            continue
        direction = 1 if c > o else -1
        entry = day.iloc[1]["Open"]
        stop = first["Low"] if direction == 1 else first["High"]
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            continue
        shares = int((equity * RISK_PCT) / risk_per_share)
        max_shares = int(equity * MAX_LEVERAGE / entry)
        shares = min(shares, max_shares)
        if shares < 1:
            continue
        target = entry + direction * TARGET_R * risk_per_share

        exit_price, exit_reason = None, None
        for _, bar in day.iloc[1:].iterrows():
            if direction == 1:
                if bar["Low"] <= stop:
                    exit_price, exit_reason = stop, "stop"; break
                if bar["High"] >= target:
                    exit_price, exit_reason = target, "target"; break
            else:
                if bar["High"] >= stop:
                    exit_price, exit_reason = stop, "stop"; break
                if bar["Low"] <= target:
                    exit_price, exit_reason = target, "target"; break
        if exit_price is None:
            exit_price, exit_reason = day.iloc[-1]["Close"], "eod"

        pnl = direction * (exit_price - entry) * shares - 2 * COMMISSION_PER_SHARE * shares
        equity += pnl
        trades.append({"date": date, "dir": "L" if direction == 1 else "S",
                       "shares": shares, "entry": entry, "exit": exit_price,
                       "reason": exit_reason, "pnl": pnl, "equity": equity})

    t = pd.DataFrame(trades)
    print(f"Days traded: {len(t)}   Period: {t.date.iloc[0]} -> {t.date.iloc[-1]}")
    print(f"Final equity: ${equity:,.2f}  ({(equity/CASH-1)*100:+.2f}% over ~60 trading days)")
    print(f"Win rate: {(t.pnl > 0).mean()*100:.1f}%   Avg win: ${t[t.pnl>0].pnl.mean():.2f}   "
          f"Avg loss: ${t[t.pnl<=0].pnl.mean():.2f}")
    print(f"Exits: {t.reason.value_counts().to_dict()}")
    eq = t.equity
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    print(f"Max drawdown: {dd:.2f}%")
    print(f"Profit factor: {t[t.pnl>0].pnl.sum() / abs(t[t.pnl<=0].pnl.sum()):.2f}")
    t.to_csv("orb_trades.csv", index=False)
    print("Wrote orb_trades.csv")


if __name__ == "__main__":
    run()
