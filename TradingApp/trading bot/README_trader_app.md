---
tags: [trading-bot, how-to]
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-19
---

# Trader App — how to run it on this Mac

The project folder is `/Users/kaloyanivanov/TradingBotApp` (outside the vault). This note is the accurate, current guide to what's in it and how to run it — updated from the actual code, not just the original README shipped with it.

## One-time setup

```bash
cd /Users/kaloyanivanov/TradingBotApp
python3 -m venv .venv        # already done — .venv/ exists
source .venv/bin/activate
pip install -r requirements.txt   # yfinance, backtesting, pandas, numpy, rich, plotext, ib_async
```

`ib_async` (IBKR connection layer) is now in `requirements.txt` alongside the backtesting stack — added when `ibkr_service.py` was built.

`trading_agent_service.py` (see below) needs a **separate** one-time setup — it's not in `requirements.txt` because it depends on cloning a third-party repo, not a pip package.

## Run it

```bash
cd /Users/kaloyanivanov/TradingBotApp
source .venv/bin/activate
python3 trader_app.py
```

First launch downloads ~16 years of daily data for 11 tickers (~30 seconds), cached in `price_data/` next to the script (already populated: AAPL, AMZN, DIS, GOOGL, JNJ, JPM, KO, MSFT, PG, SPY, XOM).

## Current menu (9 options)

| # | What it does |
|---|---|
| 1 | SMA backtest — out-of-sample (2019 → now) — **the number that matters** |
| 2 | SMA backtest — in-sample (2010 → 2018) |
| 3 | SMA backtest — full history |
| 4 | Ticker deep dive: full stats, every individual trade, equity curve chart in the terminal |
| 5 | **Momentum rotation backtest (portfolio-level)** — the strategy that actually earned its keep, see [[trading bot/Backtest Results & Findings]] |
| 6 | Settings: tickers, SMA windows, per-trade cost, starting cash, date ranges, **risk engine**, momentum parameters, **IBKR port/client id** — saved to `trader_settings.json` |
| 7 | Refresh price data (force re-download) |
| 8 | **IBKR paper account** — connect, account summary, positions, live 15-min bars for any stock/forex/future/crypto symbol |
| 9 | Quit |

Settings submenu (via option 6) now also includes:
- **Risk engine toggle** — when on, the SMA strategy adds a 200-day trend filter + 2×ATR trailing stop + fixed-fractional position sizing (you set risk % per trade); the momentum strategy switches to "dual momentum" (falls back to cash instead of a losing ticker in bear markets). Still **off by default**.
- **Momentum settings** — how many top tickers to hold (default 3) and the lookback window in months (default 12).
- **IBKR socket port / client id** — port defaults to 7497 (TWS paper). The prompt itself refuses live ports (7496/4001) — you cannot set a live port from this menu.

## Other scripts in the folder (not run through the menu)

| Script | What it does |
|---|---|
| `sma_crossover_backtest.py` | The core SMA 20/50 backtest engine — imported by `trader_app.py` and the other scripts below |
| `strategy_shootout.py` | Runs all 5 strategy families (SMA trend, golden cross, Donchian breakout, RSI-2 mean reversion, momentum rotation) plus buy-and-hold/SPY, out-of-sample, one command. Produces the comparison table in [[trading bot/Backtest Results & Findings]]. |
| `variant_experiments.py` | Tests risk-management variants of the SMA crossover (baseline / trend filter / ATR stop / fully risk-sized) against the same out-of-sample period |
| `orb_backtest.py` | Opening Range Breakout (Zarattini & Aziz 2023 rules) on 5-minute QQQ bars. Free data only covers ~60 days, so this is a mechanics smoke test, not a real validation — writes `orb_trades.csv` |
| `ibkr_service.py` | IBKR connection layer — see dedicated section below. Called from menu option 8; also importable standalone. |
| `trading_agent_service.py` | Wrapper around the third-party `TradingAgents` multi-agent research library — see dedicated section below. **Not called from the menu** — standalone, run/imported manually. |

## Output/data files

- `price_data/*.csv` — cached daily OHLCV per ticker
- `trader_settings.json` — persisted settings (tickers, SMA windows, costs, risk engine, momentum params, IBKR port/client id)
- `backtest_results.csv` — raw SMA backtest output, all tickers, all periods
- `backtest_report.md` — the write-up of those results (source for [[trading bot/Backtest Results & Findings]])
- `orb_trades.csv` — trade log from the ORB smoke test
- `day_trader_research.md` — the day-trading landscape research (source for [[trading bot/Backtest Results & Findings]])

## IBKR connection layer (`ibkr_service.py`)

Foundation for live/paper trading through Interactive Brokers — one API for stocks, forex, futures (incl. commodities like Micro Gold `MGC`), and crypto.

- Requires TWS or IB Gateway running locally with the API enabled (`Edit → Global Configuration → API → Settings`). Paper ports: 7497 (TWS) / 4002 (Gateway).
- `python3 ibkr_service.py` smoke-tests the connection: pulls 15-min bars for AAPL, EURUSD, and BTC from your paper account.
- Safety: `connect()` refuses live ports (7496/4001) unless you explicitly pass `allow_live=True`. Keep it that way until months of paper evidence exist. The `trader_app.py` settings menu also refuses live ports outright — two independent guards.
- What it contains: contract builders (`stock`, `forex_pair`, `future`, `crypto`), a 15-min bar pull (`get_15min_bars`) and live streaming (`stream_15min_bars`), and a generic `place_market_order`. Menu option 8 in the app only exercises the **read-only** parts (account summary, positions, bars) — `place_market_order` exists in the module but is **not wired to anything**. Nothing can place a real or paper order through the app today.
- Asset-class data quirk handled in code: forex history uses MIDPOINT, crypto uses AGGTRADES, everything else TRADES.
- Futures expire — contracts like `MGC 202612` must be rolled to the next month periodically. Not yet automated; flagged for whenever a strategy loop actually trades futures.

## TradingAgents research wrapper (`trading_agent_service.py`) — new, untested

Thin wrapper around the third-party open-source project [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents), which runs a multi-agent LLM debate (bull analyst vs. bear analyst vs. risk manager vs. portfolio manager) over a ticker and returns a buy/hold/sell call with the reasoning trail.

- **Status: added to the folder but not yet run or verified.** No output logs exist yet — treat any output as unvalidated until it's actually been exercised and the reasoning spot-checked against known facts about the ticker.
- **Separate setup required**, not in `requirements.txt`:
  ```bash
  git clone https://github.com/TauricResearch/TradingAgents.git
  cd TradingAgents && python3 -m venv .venv && source .venv/bin/activate && pip install .
  export ANTHROPIC_API_KEY="sk-ant-..."
  ```
- **Costs real Anthropic API money per call** — roughly a few normal Claude conversations' worth of tokens per ticker, depending on model choice (currently configured for `claude-sonnet-5` deep-thinking / `claude-haiku-4-5-20251001` quick-thinking) and debate rounds. Test on one ticker before ever looping it over a watchlist or a schedule.
- **Produces a simulated decision only** — same as everything else in this app, it does not touch a broker or place a trade. If this were to feed real orders, that integration doesn't exist yet.
- Relevance to [[trading bot/Plan]]: this is a plausible shortcut for **Phase 1 (research agent)**, which otherwise hasn't been started — using a maintained third-party multi-agent library instead of building the research/thesis agent from scratch. It has **not** been evaluated for output quality yet, so it doesn't count as Phase 1 being "done" — just as an unstarted-but-available option.

## Notes

- **This app places no orders and touches no money.** It's the Phase 2 (backtesting) layer of [[trading bot/Plan]] — the thing you validate strategies on before any broker connection exists. The IBKR layer (menu 8) is read-only for the same reason.
- Green/red coloring in tables = positive/negative. On menu 1 (SMA, defaults), expect the verdict panel to be **red** — the strategy genuinely loses to buy-and-hold with the default 20/50 settings. That's the finding, not a bug.
- Menu 5 (momentum rotation) is the strategy actually worth exploring further — it's the one that came close to SPY with meaningfully lower drawdown in the shootout.
- Play with menu 6 settings (different SMA windows, tickers, costs) then re-run menu 1 — watch how fragile the SMA results are. Fastest way to build intuition for why tweaking parameters until a backtest looks good is a trap, not a strategy.
- Tested end-to-end: all 9 menu paths, invalid inputs (fast SMA ≥ slow, unknown tickers, short date windows) handled without crashing.
