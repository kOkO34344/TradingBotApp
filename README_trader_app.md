# Trader App — how to run it on your terminal

## One-time setup

Open Terminal in the `TradingBotApp` folder, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run it

```bash
python3 trader_app.py
```

First launch downloads ~16 years of daily data for 11 tickers (takes ~30 seconds, then it's cached in a `price_data/` folder next to the script).

## What you can do in it

| Menu | What it does |
|---|---|
| 1 | SMA backtest, out-of-sample (2019→now) — the honest test, full results table + verdict |
| 2 | SMA backtest, in-sample (2010–2018) |
| 3 | SMA backtest, full history |
| 4 | Deep dive one ticker: full stats, every individual trade, equity curve chart drawn in the terminal |
| 5 | **Momentum rotation backtest** — portfolio-level: each month hold the top-N tickers by trailing 12-month return. The only strategy family that beat SPY in testing (~18.5% CAGR vs 16% for SPY, 2019→now, with a third less drawdown). Shows holdings by month + equity curve vs SPY |
| 6 | Settings: tickers, SMA windows, per-trade cost, starting cash, date ranges, **risk engine** (for SMA: 200-day trend filter + 2×ATR trailing stop + fixed % risk per trade; for momentum: dual-momentum cash filter — go to cash instead of holding negative-momentum names), momentum top-N/lookback, IBKR port/client id — saved to `trader_settings.json` |
| 7 | Force re-download of price data |
| 8 | **IBKR paper account** — connects through `ibkr_service.py` to TWS/IB Gateway (must be running with API enabled): account summary, open positions, live 15-min bars for any stock/forex/future/crypto symbol with a session chart. **Read-only by design** — the app cannot place orders; that stays locked until a strategy + approval loop exist. The settings menu refuses live ports (7496/4001) outright. |
| 9 | Quit |

## Notes

- **This app places no orders and touches no money.** It's the Phase 2 (backtesting) layer of the plan — the thing you validate strategies on before any broker connection exists.
- Green/red coloring in tables = positive/negative. The "Verdict" panel border is green only if the strategy beats SPY in most tickers — with the default 20/50 SMA settings it will be red, because the strategy genuinely loses to buy-and-hold. That's the finding, not a bug.
- Play with menu 5: try different SMA windows (e.g., 10/30, 50/200), different tickers, higher/lower costs — then re-run menu 1 and watch how fragile the results are. That's the fastest way to build intuition for why parameter-tweaking until a backtest looks good is a trap.
- Tested end-to-end: all 8 menu paths, invalid inputs (fast SMA ≥ slow, unknown tickers, short date windows) handled without crashing.
- Momentum caveat to keep in mind: the ~18.5% CAGR is on a hand-picked 10-mega-cap watchlist, which flatters the result (these are companies we already know survived and thrived). A fair version would use a broad universe (e.g., S&P 500 constituents as of each date). Treat the number as "momentum is worth pursuing," not "momentum earns 18.5%."

## IBKR connection layer (`ibkr_service.py`)

Foundation for live/paper trading through Interactive Brokers — one API for stocks, forex, futures (incl. commodities like Micro Gold `MGC`), and crypto.

- Requires TWS or IB Gateway running locally with the API enabled (`Edit → Global Configuration → API → Settings`). Paper ports: 7497 (TWS) / 4002 (Gateway).
- `python3 ibkr_service.py` smoke-tests the connection: pulls 15-min bars for AAPL, EURUSD, and BTC from your paper account.
- Safety: `connect()` refuses live ports (7496/4001) unless you explicitly pass `allow_live=True`. Keep it that way until months of paper evidence exist.
- What it contains: contract builders (`stock`, `forex_pair`, `future`, `crypto` — commodities are just futures, e.g. `future("MGC", "202612", "COMEX")` for Micro Gold), 15-min bar pull (`get_15min_bars`) and live streaming (`stream_15min_bars`), and a generic `place_market_order`. It has **no strategy logic** — nothing trades until a signal engine is wired in and you approve the design.
- Asset-class data quirk handled in code: forex history uses MIDPOINT, crypto uses AGGTRADES, everything else TRADES.
- Futures expire — contracts like `MGC 202612` must be rolled to the next month periodically. Not yet automated; flagged for the strategy loop.
