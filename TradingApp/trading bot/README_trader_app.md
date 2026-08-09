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
| 7 | Settings: tickers, SMA windows, per-trade cost, starting cash, date ranges, **risk engine** (for SMA: 200-day trend filter + 2×ATR trailing stop + fixed % risk per trade; for momentum: dual-momentum cash filter — go to cash instead of holding negative-momentum names), momentum top-N/lookback — saved to `trader_settings.json` |
| 8 | Force re-download of price data |
| 9 | **FTMO venue** — reads the live account through `api/ftmo_api.py`: balance, equity including floating P&L, the rule engine's verdict against all three limits, and open positions with their stops. **Read-only by design** — this app places nothing. `ftmo_runner.py` is the only order path, and menu 8 arms or disarms it. |
| 10 | Quit |

## Shared indicators module (`indicators.py`)

One source of truth for all technical math, used by both the chart view (what you see) and `research_agent.py` (what the AI reasons over): SMA/EMA/MACD/RSI, ATR/Bollinger/Keltner (incl. squeeze detection), VWAP (session-aware)/OBV, swing support/resistance, 52-week range, opening range. `python3 indicators.py --selftest` verifies the math against reference implementations — 20 checks, all passing. The future web dashboard should import this same module.

## Notes

- **This app places no orders and touches no money.** It's the Phase 2 (backtesting) layer of the plan — the thing you validate strategies on before any broker connection exists.
- Green/red coloring in tables = positive/negative. The "Verdict" panel border is green only if the strategy beats SPY in most tickers — with the default 20/50 SMA settings it will be red, because the strategy genuinely loses to buy-and-hold. That's the finding, not a bug.
- Play with menu 5: try different SMA windows (e.g., 10/30, 50/200), different tickers, higher/lower costs — then re-run menu 1 and watch how fragile the results are. That's the fastest way to build intuition for why parameter-tweaking until a backtest looks good is a trap.
- Tested end-to-end: all 8 menu paths, invalid inputs (fast SMA ≥ slow, unknown tickers, short date windows) handled without crashing.
- Momentum caveat to keep in mind: the ~18.5% CAGR is on a hand-picked 10-mega-cap watchlist, which flatters the result (these are companies we already know survived and thrived). A fair version would use a broad universe (e.g., S&P 500 constituents as of each date). Treat the number as "momentum is worth pursuing," not "momentum earns 18.5%."

## Trading venue (`ftmo_*.py`)

IBKR was the venue until 2026-08-02 and its code was removed on 2026-08-09.
FTMO, via the cTrader Open API, is the only one now — ten modules at the repo
root carrying **579 offline checks** between them, none of which need
credentials or a connection.

- `ftmo_rules.py` decides (limits, three thresholds, both products),
  `ftmo_sizing.py` sizes, `ftmo_session.py` transports, `ftmo_audit.py` records
  why, `ftmo_closes.py` detects positions that closed on their own.
- **Every limit is measured on equity INCLUDING floating P&L**, so the account
  can fail with no order placed. That is why this venue has a continuous
  monitor rather than a pre-trade gate.
- **Every entry carries a stop on the same request as the entry**, applied from
  the actual fill so slippage cannot widen real risk, and since 2026-08-08 a
  take-profit too. Stops are verified by reading the venue back — never
  inferred from an order having been sent.
- `python3 ftmo_service.py --probe` checks connectivity read-only.
  `python3 ftmo_runner.py --force --dry-run` runs the whole pipeline and places
  nothing.

Read the `ftmo` skill before touching any of it.

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
