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
pip install -r requirements.txt   # yfinance, backtesting, pandas, numpy, rich, plotext, ib_async, claude-agent-sdk
```

`ib_async` (IBKR connection layer) and `claude-agent-sdk` (research agent, see below) are both in `requirements.txt` now, alongside the backtesting stack.

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
| `research_agent.py` | **This project's own Phase 1 research agent**, built on the Claude Agent SDK — see dedicated section below. Also not called from the menu. |
| `grade_calls.py` | Grades `research_agent.py`'s past notes against what actually happened to the price afterward. See dedicated section below. |

## Output/data files

- `price_data/*.csv` — cached daily OHLCV per ticker
- `trader_settings.json` — persisted settings (tickers, SMA windows, costs, risk engine, momentum params, IBKR port/client id)
- `backtest_results.csv` — raw SMA backtest output, all tickers, all periods
- `backtest_report.md` — the write-up of those results (source for [[trading bot/Backtest Results & Findings]])
- `orb_trades.csv` — trade log from the ORB smoke test
- `day_trader_research.md` — the day-trading landscape research (source for [[trading bot/Backtest Results & Findings]])
- `research_log/*.md` — one file per `research_agent.py` run, `<TICKER>_<date>.md`. Currently holds **two synthetic placeholder files** (`AAPL_2026-05-15_0930.md`, `MSFT_2026-06-01_1100.md`), each explicitly labeled `SYNTHETIC TEST NOTE — not a real call. Safe to delete this file.` — these exist to exercise `grade_calls.py`'s parsing, not as real research. **Zero real agent runs exist yet.**
- `graded_calls.csv` — output of `grade_calls.py --csv`, currently graded **only from the two synthetic notes above** (1/4 graded rows correct — meaningless as evidence, it's test data, not a track record)
- `knowledge/*.md` — curated, verified-sources-only library injected into every `research_agent.py` prompt (see dedicated section below)

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
- Relevance to [[trading bot/Plan]]: this is a plausible shortcut for **Phase 1 (research agent)** — but see below, a second, purpose-built Phase 1 candidate now also exists in the folder, so this is one of two options rather than the only one.

## Phase 1 research agent (`research_agent.py` + `grade_calls.py`) — built, zero real runs yet

This is the project's **own** Phase 1 implementation — built to the original plan's spec (Claude Agent SDK, `query()`, structured logged thesis), and now the more developed of the two Phase 1 candidates in the folder.

**`research_agent.py`** — turns a ticker into a structured, logged thesis:
- Computes real indicators from real price data on **two timeframes** — daily (1y: SMA20/50/200, RSI-14, ATR-14, 52-week range, realized vol) and 15-minute (5d: SMA20, RSI-14, ATR-14, opening range, session VWAP) — and hands Claude numbers, never a description of "what a chart looks like."
- Best-effort fundamentals snapshot (market cap, P/E, margins, revenue growth, debt/equity) via `yfinance`.
- Injects the curated `knowledge/` library (see below) into every prompt.
- Forces a fixed note structure: Thesis, Direction & timeframe, Confidence (1–10), Key levels, Risks, What would change my mind.
- `python3 research_agent.py TICKER --dry-run` prints the assembled prompt with no API call — good for checking what the agent will see before spending tokens.
- Full run needs `ANTHROPIC_API_KEY` or a logged-in Claude Code session (draws Agent SDK credit from the Claude plan instead of separate billing).
- Every real run is logged to `research_log/<TICKER>_<date>.md`.

**`grade_calls.py`** — the accountability half. Reads every note in `research_log/`, extracts the direction call and confidence, pulls what the price actually did at 5-day and 21-day horizons, and grades it:
- long correct if forward return > +0.5%, short correct if < −0.5%, no-edge correct if |return| ≤ 2%.
- Notes younger than the horizon are marked pending, not force-graded.
- Prints a calibration report (win rate by direction, by confidence bucket) — the whole point being to catch a confidence-7 call that isn't actually righter than a confidence-4 call, per this project's calibration rule.
- `--csv` also writes `graded_calls.csv`.

**Current state, stated plainly: this has never produced a real call.** `research_log/` holds exactly two files, and both are explicitly marked `SYNTHETIC TEST NOTE — not a real call. Safe to delete this file.` — placeholders to exercise the parser, not agent output. `graded_calls.csv` currently shows 1/4 graded rows correct, which is **not a track record**, it's a grading-pipeline smoke test on fake data. Per [[trading bot/Plan]] and this project's own rule that autonomy is earned by graded evidence, none of that evidence exists yet — the infrastructure is what's done, not the calibration.

**`knowledge/` library** — `.md` files read (in filename order) and injected into every `research_agent.py` prompt, capped at ~40,000 characters. Current contents: `01_evidence_based_principles.md`, which distills exactly the verified findings in [[trading bot/Backtest Results & Findings]] (base rates, momentum's published edge, ORB's regime-dependence, ICT's lack of evidence, the audited day-traders' risk rules) into the agent's working knowledge, plus a `README.md` describing the folder's rule: verified sources only, distilled, cited, no influencer material.

## Notes

- **This app places no orders and touches no money.** It's the Phase 2 (backtesting) layer of [[trading bot/Plan]] — the thing you validate strategies on before any broker connection exists. The IBKR layer (menu 8) is read-only for the same reason.
- Green/red coloring in tables = positive/negative. On menu 1 (SMA, defaults), expect the verdict panel to be **red** — the strategy genuinely loses to buy-and-hold with the default 20/50 settings. That's the finding, not a bug.
- Menu 5 (momentum rotation) is the strategy actually worth exploring further — it's the one that came close to SPY with meaningfully lower drawdown in the shootout.
- Play with menu 6 settings (different SMA windows, tickers, costs) then re-run menu 1 — watch how fragile the SMA results are. Fastest way to build intuition for why tweaking parameters until a backtest looks good is a trap, not a strategy.
- Tested end-to-end: all 9 menu paths, invalid inputs (fast SMA ≥ slow, unknown tickers, short date windows) handled without crashing.
