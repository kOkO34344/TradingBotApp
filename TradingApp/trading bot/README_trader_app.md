---
tags: [trading-bot, how-to]
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-18
---

# Trader App — how to run it on this Mac

The project folder is `/Users/kaloyanivanov/TradingBotApp` (outside the vault). This note is the accurate, current guide to what's in it and how to run it — updated from the actual code, not just the original README shipped with it.

## One-time setup

```bash
cd /Users/kaloyanivanov/TradingBotApp
python3 -m venv .venv        # already done — .venv/ exists
source .venv/bin/activate
pip install yfinance backtesting pandas numpy rich plotext
```

## Run it

```bash
cd /Users/kaloyanivanov/TradingBotApp
source .venv/bin/activate
python3 trader_app.py
```

First launch downloads ~16 years of daily data for 11 tickers (~30 seconds), cached in `price_data/` next to the script (already populated: AAPL, AMZN, DIS, GOOGL, JNJ, JPM, KO, MSFT, PG, SPY, XOM).

## Current menu (8 options — this changed since the app was first built)

| # | What it does |
|---|---|
| 1 | SMA backtest — out-of-sample (2019 → now) — **the number that matters** |
| 2 | SMA backtest — in-sample (2010 → 2018) |
| 3 | SMA backtest — full history |
| 4 | Ticker deep dive: full stats, every individual trade, equity curve chart in the terminal |
| 5 | **Momentum rotation backtest (portfolio-level)** — the strategy that actually earned its keep, see [[trading bot/Backtest Results & Findings]] |
| 6 | Settings: tickers, SMA windows, per-trade cost, starting cash, date ranges, **risk engine**, momentum parameters — saved to `trader_settings.json` |
| 7 | Refresh price data (force re-download) |
| 8 | Quit |

Settings submenu (via option 6) now also includes:
- **Risk engine toggle** — when on, the SMA strategy adds a 200-day trend filter + 2×ATR trailing stop + fixed-fractional position sizing (you set risk % per trade); the momentum strategy switches to "dual momentum" (falls back to cash instead of a losing ticker in bear markets).
- **Momentum settings** — how many top tickers to hold (default 3) and the lookback window in months (default 12).

## Other scripts in the folder (not run through the menu)

| Script | What it does |
|---|---|
| `sma_crossover_backtest.py` | The core SMA 20/50 backtest engine — imported by `trader_app.py` and the other scripts below |
| `strategy_shootout.py` | Runs all 5 strategy families (SMA trend, golden cross, Donchian breakout, RSI-2 mean reversion, momentum rotation) plus buy-and-hold/SPY, out-of-sample, one command. Produces the comparison table in [[trading bot/Backtest Results & Findings]]. |
| `variant_experiments.py` | Tests risk-management variants of the SMA crossover (baseline / trend filter / ATR stop / fully risk-sized) against the same out-of-sample period |
| `orb_backtest.py` | Opening Range Breakout (Zarattini & Aziz 2023 rules) on 5-minute QQQ bars. Free data only covers ~60 days, so this is a mechanics smoke test, not a real validation — writes `orb_trades.csv` |

## Output/data files

- `price_data/*.csv` — cached daily OHLCV per ticker
- `trader_settings.json` — persisted settings (tickers, SMA windows, costs, risk engine, momentum params)
- `backtest_results.csv` — raw SMA backtest output, all tickers, all periods
- `backtest_report.md` — the write-up of those results (source for [[trading bot/Backtest Results & Findings]])
- `orb_trades.csv` — trade log from the ORB smoke test
- `day_trader_research.md` — the day-trading landscape research (source for [[trading bot/Backtest Results & Findings]])

## Notes

- **This app places no orders and touches no money.** It's the Phase 2 (backtesting) layer of [[trading bot/Plan]] — the thing you validate strategies on before any broker connection exists.
- Green/red coloring in tables = positive/negative. On menu 1 (SMA, defaults), expect the verdict panel to be **red** — the strategy genuinely loses to buy-and-hold with the default 20/50 settings. That's the finding, not a bug.
- Menu 5 (momentum rotation) is the strategy actually worth exploring further — it's the one that came close to SPY with meaningfully lower drawdown in the shootout.
- Play with menu 6 settings (different SMA windows, tickers, costs) then re-run menu 1 — watch how fragile the SMA results are. Fastest way to build intuition for why tweaking parameters until a backtest looks good is a trap, not a strategy.
- Tested end-to-end: all menu paths, invalid inputs (fast SMA ≥ slow, unknown tickers, short date windows) handled without crashing.
