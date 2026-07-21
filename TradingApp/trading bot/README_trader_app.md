---
tags: [trading-bot, how-to]
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-21
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

`trading_agent_service.py` (see below) needs a **separate** one-time setup — it's not in `requirements.txt` because it depends on cloning a third-party repo, not a pip package.

## Run it

```bash
cd /Users/kaloyanivanov/TradingBotApp
source .venv/bin/activate
python3 trader_app.py
```

First launch downloads ~16 years of daily data for the watchlist (~30 seconds), cached in `price_data/` next to the script.

## Current menu (10 options — reshuffled since last sync)

| # | What it does |
|---|---|
| 1 | SMA backtest — out-of-sample (2019 → now) — **the number that matters** |
| 2 | SMA backtest — in-sample (2010 → 2018) |
| 3 | SMA backtest — full history |
| 4 | Ticker deep dive: full stats, every individual trade, equity curve chart in the terminal |
| 5 | **Chart view (new)** — candlestick charts (daily 120 bars or 15-min 5 days) with selectable indicator sets: trend (SMA overlays + MACD/RSI panels), volatility (Bollinger bands), volume (bars + session VWAP on 15m), structure (computed swing support/resistance). Ends with a numeric readout — **the exact same numbers `research_agent.py` reasons over**, both pulled from `indicators.py` (see below) |
| 6 | Momentum rotation backtest (portfolio-level) — the strategy that actually earned its keep, see [[trading bot/Backtest Results & Findings]]. **(This was menu 5 before the chart view was inserted.)** |
| 7 | Settings: tickers, SMA windows, per-trade cost, starting cash, date ranges, risk engine, momentum parameters, IBKR port/client id — saved to `trader_settings.json` |
| 8 | Refresh price data (force re-download) |
| 9 | IBKR paper account — connect, account summary, positions, live 15-min bars for any stock/forex/future/crypto symbol |
| 10 | Quit |

Settings submenu (via option 7) also includes:
- **Risk engine toggle** — SMA: 200-day trend filter + 2×ATR trailing stop + fixed-fractional sizing; momentum: dual-momentum cash filter. Still **off by default**.
- **Momentum settings** — top-N (default 3) and lookback months (default 12).
- **IBKR socket port / client id** — the settings menu refuses live ports (7496/4001) outright.

## Other scripts in the folder (not run through the menu)

| Script | What it does |
|---|---|
| `sma_crossover_backtest.py` | Core SMA 20/50 backtest engine — imported by `trader_app.py` and the scripts below |
| `strategy_shootout.py` | Runs all 5 strategy families + buy-and-hold/SPY, out-of-sample, one command. Source of the comparison table in [[trading bot/Backtest Results & Findings]] |
| `variant_experiments.py` | SMA crossover risk-management variants (baseline / trend filter / ATR stop / fully risk-sized) |
| `orb_backtest.py` | Opening Range Breakout (Zarattini & Aziz 2023 rules) on 5-min QQQ bars — mechanics smoke test, not a real validation (only ~60 days of free data) |
| `indicators.py` | **New — shared indicator math.** See dedicated section below. |
| `ibkr_service.py` | IBKR connection + **now hardened** execution layer. See dedicated section below. Called from menu option 9; also importable standalone. |
| `paper_trader.py` | **New — Phase 3 paper-trading loop, not part of the menu app.** Momentum signal → diff vs. live positions → proposed rebalance → y/n approval → `place_bracket_order` execution. Run for real 2026-07-21. See [[IBKR Integration]]. |
| `trading_agent_service.py` | Wrapper around the third-party `TradingAgents` multi-agent research library. **Still never run.** See dedicated section below. |
| `research_agent.py` | This project's own Phase 1 research agent. **Now has 12 real runs behind it.** See dedicated section below. |
| `grade_calls.py` | Grades `research_agent.py`'s notes against what the price actually did. **Not yet re-run against the 12 real notes** — see below. |
| `diagnose_ibkr.sh`, `wait_and_test_ibkr.sh` | Connection troubleshooting scripts written during the IBKR paper-account setup saga (see IBKR section below). Still useful if the connection ever needs re-diagnosing. |

## Output/data files

- `price_data/*.csv` — cached daily OHLCV per ticker
- `trader_settings.json` — persisted settings, now including a 12-ticker watchlist (NVDA and PLTR added) and `ibkr_port: 4002` (switched from TWS's 7497 to Gateway's 4002 — see IBKR section)
- `backtest_results.csv`, `backtest_report.md` — SMA backtest raw output + write-up (source for [[trading bot/Backtest Results & Findings]])
- `orb_trades.csv`, `day_trader_research.md` — ORB smoke-test log + day-trading landscape research (source for [[trading bot/Backtest Results & Findings]])
- `research_log/*.md` — **12 real research notes now**, one per full-watchlist ticker (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR), dated 2026-07-20/21. The two old synthetic placeholder files are gone. See the research agent section below for what these actually say.
- `graded_calls.csv` — **stale.** Still reflects only the old synthetic-note run from 2026-07-19; `grade_calls.py` has not been re-run against the 12 real notes yet. The most valuable single next action in this whole project is running `python3 grade_calls.py --csv` once the notes are old enough to have forward returns (the 5-day horizon on the 07-20/21 notes should be gradable very soon).
- `knowledge/*.md` — curated, verified-sources-only library injected into every `research_agent.py` prompt (unchanged since last sync)
- `risk_limits.json`, `trade_journal.csv` — **exist now, with real content.** `paper_trader.py`'s first rebalance (2026-07-21) exercised `place_bracket_order` for real: `trade_journal.csv` has actual SUBMIT/RESULT/BLOCKED rows (GOOGL/AAPL/JNJ fills, a JNJ notional block, and the same-day GTC re-protect entries), and `risk_limits.json` holds the live limits ($5,000 max order notional, 5 max positions, $300 daily-loss circuit breaker, stop required).

## Shared indicators module (`indicators.py`) — new

The single source of truth for technical math, now used by **both** the chart view (menu 5, what the owner sees) and `research_agent.py` (what the AI reasons over) — so the agent's numbers are guaranteed to match what's on screen, not a separate parallel calculation.

- Covers: SMA/EMA/MACD (trend), RSI (momentum), ATR/Bollinger/Keltner incl. squeeze detection (volatility), VWAP (session-aware)/OBV (volume), swing support/resistance, 52-week range, opening range (structure).
- `python3 indicators.py --selftest` — reference-math checks (e.g. SMA vs pandas rolling mean, MACD = EMA12−EMA26) plus invariant checks (RSI∈[0,100], ATR>0, bands don't invert). All passing as of this sync.
- Per this project's `CLAUDE.md`: **never reimplement indicators elsewhere, including any future web dashboard** — this module is meant to be the one place technical math lives.

## IBKR connection layer (`ibkr_service.py`) — now hardened, and connection verified live

Foundation for live/paper trading through Interactive Brokers — one API for stocks, forex, futures (incl. commodities), and crypto.

**The connection itself: resolved.** IBKR had been holding the paper account in address-verification review; that cleared 2026-07-21. A connected smoke test then **passed for real** against IB Gateway on the paper port (4002), account `DUQ903866`: `verify_paper_account()` succeeded and the script pulled 45 rows of real AAPL 15-min bars. `trader_settings.json.ibkr_port` was updated from 7497 to 4002 to match. Two benign `ib_async` warnings appear on connect ("open orders request timed out", "completed orders request timed out") — expected on a fresh account with no order history, not a bug. `diagnose_ibkr.sh` / `wait_and_test_ibkr.sh` are still in the folder in case the connection ever needs re-diagnosing.

**The module itself got substantially more serious since last sync — three enforcement layers now sit in front of every order path, in code, not just as documentation:**

1. **Paper verification** — `verify_paper_account()` checks the account id starts with `D` (IBKR's paper-account prefix) and refuses to proceed otherwise unless `allow_live=True` is passed explicitly.
2. **RiskGuard** — limits loaded from `risk_limits.json` (auto-created with defaults on first use: $5,000 max order notional, 5 max open positions, $300 daily-loss circuit breaker, and stop-required — bare stopless orders are refused unless deliberately overridden). A blocked order never reaches the broker and is journaled with the reason instead.
3. **Trade journal** — every order attempt, block, submission, and fill appends a row to `trade_journal.csv`. This matches the project rule verbatim: if it isn't in the journal, it didn't happen.

**New execution functions:**
- `place_bracket_order` — the **default** way to enter a position now: limit entry + stop-loss (+ optional take-profit) submitted as one atomic bracket, so a position can never exist without its stop attached.
- `place_market_order` — still exists, but is now **refused unless `allow_no_stop=True`** is passed deliberately. Bare stopless market orders are the exception path, not the default, per the project's "no order without a stop" rule.
- Both are still **never called from the app menu** — menu option 9 remains read-only (account summary, positions, bars). `place_bracket_order` has, however, been called for real from the separate `paper_trader.py` script (see below) — that's why `risk_limits.json` and `trade_journal.csv` now exist on disk with real content.

**Testing:** `python3 ibkr_service.py --selftest` runs 18 offline checks (contract builders, data-type routing per asset class, all four RiskGuard block/allow paths, journal roundtrip, bracket validation) with no TWS/Gateway needed — all passing. `python3 ibkr_service.py` (no flag) is the connected smoke test described above; it still places no orders.

- Asset-class data quirk: forex history uses MIDPOINT, crypto uses AGGTRADES, everything else TRADES.
- Futures expire — contracts like `MGC 202612` must be rolled periodically. Not yet automated; flagged for whenever a strategy loop actually trades futures.

## TradingAgents research wrapper (`trading_agent_service.py`) — still untested

Thin wrapper around the third-party [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent LLM debate (bull vs. bear vs. risk manager vs. portfolio manager) over a ticker.

- **Still never been run**, unchanged since last sync. Costs real Anthropic API tokens per call. Needs a separate `git clone` + `pip install .` setup, not in `requirements.txt`.
- Now clearly the **second-place** Phase 1 candidate — see below, `research_agent.py` has actually been exercised on the full watchlist and this hasn't been exercised at all.

## Phase 1 research agent (`research_agent.py` + `grade_calls.py`) — now has real output, not yet graded

**This is the significant change since last sync: the agent has actually been run, for real, on the entire watchlist.**

**`research_agent.py`** now pulls its indicator math from `indicators.py` (previously it had its own inline copies of RSI/ATR) — a real consolidation, not just a refactor: the agent's numbers are now guaranteed identical to what the chart view (menu 5) shows. The prompt's named frameworks also expanded to match: multi-timeframe trend alignment (now including MACD, not just SMA), momentum/mean-reversion context (RSI + MACD histogram + Bollinger position + squeeze), volume confirmation (OBV vs. price, VWAP), structure-aware levels (swing S/R, 52-week range, opening range), volatility-aware risk framing, and a valuation sanity check. Output format unchanged: Thesis, Direction & timeframe, Confidence (1–10), Key levels, Risks, What would change my mind.

**12 real notes now exist in `research_log/`** — the full watchlist (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR), generated 2026-07-20/21. Spot-checking a few (AAPL, NVDA) against the raw data they cite: the reasoning is genuinely grounded — no invented price levels, calibrated confidence (AAPL: 3/10 for "no fresh entry despite bullish trend" because RSI 71.7 at a 52-week high is a poor entry, not because the trend read is uncertain; NVDA: 3/10 for a real split-timeframe read, explicitly explaining why it's not lower or higher). Directionally, most of the 12 came back **no-edge** (AAPL, AMZN, DIS, GOOGL, JPM, NVDA, PG, PLTR, XOM), with a handful of long-leaning calls (JNJ, KO, MSFT) — no shorts. That's consistent with the knowledge base's own instruction that "no edge here" is a valid, creditable call, not a cop-out.

**What hasn't happened yet, correctly: grading.** `grade_calls.py` has **not been re-run** since these 12 real notes were written — `graded_calls.csv` on disk still only reflects the old synthetic-note test run. None of these 12 notes are old enough yet for the 21-day horizon, and even the 5-day horizon on the earliest (AAPL, 07-20) isn't there yet — as of 2026-07-21 it's only ~1 day old. Expect the 5-day horizon to start becoming gradable ~2026-07-25. **Do not treat the qualitative read above as calibration evidence** — it's a spot-check of reasoning quality, not the win-rate/confidence-calibration report that actually decides whether this agent is earning trust. Run `python3 grade_calls.py --csv` on a weekly cadence starting ~2026-07-25.

**`knowledge/` library** — unchanged since last sync: `01_evidence_based_principles.md` (distills [[trading bot/Backtest Results & Findings]] into the agent's working knowledge) + `README.md` (verified/distilled/cited rule).

## Notes

- **This terminal *app* (`trader_app.py`) still places no orders** — its IBKR menu (menu 9) stays read-only by design. But the execution machinery it shares with the rest of the project (`place_bracket_order`, RiskGuard, journal) is no longer just tested-but-unused: a separate script, `paper_trader.py`, now calls it for real — first rebalance executed 2026-07-21 (bought GOOGL/AAPL/JNJ on the paper account). See [[IBKR Integration]] for the full detail. Real (paper) orders exist in this project now; they just don't come from this particular terminal app.
- Green/red coloring in tables = positive/negative. On menu 1 (SMA, defaults), expect the verdict panel to be **red** — the strategy genuinely loses to buy-and-hold. That's the finding, not a bug.
- Menu 6 (momentum rotation, moved from 5) is the strategy actually worth exploring further.
- Menu numbers shifted by one from the last sync (chart view inserted at 5) — if any of your own notes/scripts reference "menu 8 = IBKR," it's now menu 9.
