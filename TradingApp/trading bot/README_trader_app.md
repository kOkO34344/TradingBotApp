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
./trader_app.sh
```

Use the launcher, not `python3 trader_app.py`. On this machine `python3` is
conda base, which has pandas/rich/yfinance/ib_async but **not** torch — so the
app starts and works normally right up until the Kronos menu, which then
reports `No module named 'torch'` even though torch is installed (in `.venv`).
The launcher pins `.venv/bin/python`. If you do launch it the other way, the
app now says so at startup instead of letting you find out three menus deep.

First launch downloads ~16 years of daily data for 11 tickers (takes ~30 seconds, then it's cached in a `price_data/` folder next to the script).

## What you can do in it

| Menu | What it does |
|---|---|
| 1 | SMA backtest, out-of-sample (2019→now) — the honest test, full results table + verdict |
| 2 | SMA backtest, in-sample (2010–2018) |
| 3 | SMA backtest, full history |
| 4 | Deep dive one ticker: full stats, every individual trade, equity curve chart drawn in the terminal |
| 5 | **Chart view** — candlestick charts (daily 120 bars or 15-min 5 days) with selectable indicator sets: trend (SMA overlays + MACD and RSI panels), volatility (Bollinger bands), volume (volume bars + session VWAP on 15m), structure (computed swing support/resistance lines). Ends with a numeric readout — the exact numbers the research agent sees, from the same `indicators.py` module |
| 6 | **Momentum rotation backtest** — portfolio-level: each month hold the top-N tickers by trailing 12-month return. The only strategy family that beat SPY in testing (~18.5% CAGR vs 16% for SPY, 2019→now, with a third less drawdown). Shows holdings by month + equity curve vs SPY |
| 7 | Settings: tickers, SMA windows, per-trade cost, starting cash, date ranges, **risk engine** (for SMA: 200-day trend filter + 2×ATR trailing stop + fixed % risk per trade; for momentum: dual-momentum cash filter — go to cash instead of holding negative-momentum names), momentum top-N/lookback, IBKR port/client id — saved to `trader_settings.json` |
| 8 | Force re-download of price data |
| 9 | **IBKR paper account** — connects through `ibkr_service.py` to TWS/IB Gateway (must be running with API enabled): account summary, open positions, live 15-min bars for any stock/forex/future/crypto symbol with a session chart. **Read-only by design** — the app cannot place orders; that stays locked until a strategy + approval loop exist. The settings menu refuses live ports (7496/4001) outright. |
| 10 | Quit |

## Shared indicators module (`indicators.py`)

One source of truth for all technical math, used by both the chart view (what you see) and `research_agent.py` (what the AI reasons over): SMA/EMA/MACD/RSI, ATR/Bollinger/Keltner (incl. squeeze detection), VWAP (session-aware)/OBV, swing support/resistance, 52-week range, opening range. `python3 indicators.py --selftest` verifies the math against reference implementations — 20 checks, all passing. The future web dashboard should import this same module.

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
- What it contains: contract builders (`stock`, `forex_pair`, `future`, `crypto` — commodities are just futures, e.g. `future("MGC", "202612", "COMEX")` for Micro Gold), 15-min bar pull (`get_15min_bars`) and live streaming (`stream_15min_bars`), and order placement (`place_bracket_order` preferred, `place_market_order` restricted).
- **Phase 2 hardening — every order path now goes through three layers, in code:**
  1. *Paper verification*: `verify_paper_account()` checks the account id looks like a paper account (starts with `D`) and refuses otherwise unless `allow_live=True`.
  2. *RiskGuard*: limits in `risk_limits.json` (auto-created with defaults: $5,000 max order notional, 5 max open positions, $300 daily-loss circuit breaker, stop required). Blocked orders never reach the broker and are journaled with the reason. Changing limits means editing the JSON — an explicit, visible act.
  3. *Trade journal*: every submit, block, and fill appends to `trade_journal.csv`. If it isn't in the journal, it didn't happen.
- `place_bracket_order` is the default entry mechanism: limit entry + stop (+ optional target) placed atomically, so no position can exist without its stop. Bare `place_market_order` is refused unless `allow_no_stop=True` is passed deliberately.
- `python3 ibkr_service.py --selftest` — 18 offline checks (contract builders, data-type routing, all RiskGuard block/allow paths, journal roundtrip, bracket validation), no TWS needed. All passing as delivered. The connected smoke test (`python3 ibkr_service.py`) still requires TWS/Gateway on your machine and places no orders. It has **no strategy logic** — nothing trades until a signal engine is wired in and you approve the design.
- Asset-class data quirk handled in code: forex history uses MIDPOINT, crypto uses AGGTRADES, everything else TRADES.
- Futures expire — contracts like `MGC 202612` must be rolled to the next month periodically. Not yet automated; flagged for the strategy loop.

## Research agent (`research_agent.py`) — Phase 1, no execution

Turns a ticker into a structured research note: multi-timeframe technicals (daily + 15-minute: SMA structure, RSI, ATR, opening range, VWAP), fundamentals, then a Claude-written thesis with direction, confidence (1-10), ATR-derived key levels, risks, and "what would change my mind". It is explicitly allowed to say "no edge here".

- `python3 research_agent.py AAPL` — full run. Auth: `export ANTHROPIC_API_KEY=...`, or be logged into Claude Code (then usage draws from your Claude plan's Agent SDK credit).
- `python3 research_agent.py AAPL --dry-run` — see exactly what data the agent gets, no API call.
- Every run is saved to `research_log/` with the thesis AND the data it saw — so calls can be graded later against what actually happened.
- **The autonomy gate:** this agent earns trust by accumulating graded calls in `research_log/`, not by sounding confident. It cannot place orders, and "executes on its own" remains locked behind months of paper-trading evidence per the plan. For 15-min-and-faster trading, the architecture stays: rules fire at machine speed, the agent reasons at research speed (daily/weekly notes, trade reviews) — an LLM is not in the intraday firing loop.
- `trading_agent_service.py` (third-party TradingAgents wrapper, from the other conversation) is the alternative multi-agent approach — still never run. Worth one paid run to compare its reasoning against this agent's on the same ticker, then keeping whichever writes better-grounded notes.

## Knowledge base (`knowledge/`) and call grading (`grade_calls.py`)

- Every `.md` in `knowledge/` is injected into the research agent's prompt on every run — its curated professional library. Seeded with `01_evidence_based_principles.md` (verified findings only: academic base rates, momentum literature, ORB paper + our replication, audited traders' shared risk rules, what has no evidence). Rules for adding material are in `knowledge/README.md`: verified, distilled, cited. Share links/articles/videos in a Claude session — Claude vets and distills them into notes here (videos via transcript; the agent reads, it can't watch).
- `python3 grade_calls.py` grades every research note against what price actually did at 5-day and 21-day horizons (long correct if > +0.5%, short if < -0.5%, no-edge if within ±2%), and prints calibration by confidence bucket. Healthy calibration = high-confidence calls beat low-confidence ones. Run weekly; this report is the agent's real track record — the thing that eventually earns or denies autonomy. `--csv` exports `graded_calls.csv`.
- `research_log/` currently contains two blanked synthetic test files — delete them (`rm research_log/AAPL_2026-05-15_0930.md research_log/MSFT_2026-06-01_1100.md`).
