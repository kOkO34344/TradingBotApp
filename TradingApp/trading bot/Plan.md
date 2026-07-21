---
tags: [trading-bot, plan]
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-21
---

# Building your own investing/trading agent — a real plan

## Status against this plan (as of 2026-07-21)

**A note on phase numbering before this list:** the project's own `CLAUDE.md` (its live working memory) has started using "Phase 2" to mean the IBKR/broker-infrastructure hardening work, and treats the SMA/momentum backtesting as evidence already banked rather than a numbered phase of its own. This note keeps this **original** document's numbering (Phase 2 = one-strategy backtesting) for internal consistency, since that's what this document is. Just don't be surprised if `CLAUDE.md` or a future session uses "Phase 2" to mean something else — check which document is being referenced.

- [x] **Phase 0 — Environment & basics** — done (venv, Python 3.13, dependencies installed)
- [ ] **Phase 1 — Research agent, no execution** — **real output exists now, grading not due yet.** `research_agent.py` has been run for real on the **full 12-ticker watchlist** (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR) — the old synthetic placeholder notes are gone. Spot-checking a couple of the real notes shows grounded, calibrated reasoning (no invented levels, mostly honest "no-edge" calls rather than forced directional ones). **`grade_calls.py` correctly hasn't been re-run yet** — the earliest note is only ~1 day old as of 2026-07-21, short of even the 5-day forward-return horizon; running it now would just show "pending." Start weekly grading ~2026-07-25+. A second, competing candidate still sits unused: `trading_agent_service.py` (third-party TradingAgents library) — never run. See [[trading bot/README_trader_app]].
- [x] **Phase 2 — One narrow strategy + rigorous backtesting** — done, and the honest result came back: the SMA 20/50 crossover **does not beat buy-and-hold**, in- or out-of-sample. Per this plan's own exit criteria, it does not advance to Phase 3 as-is. A follow-up shootout across 5 strategy families found only **momentum rotation** competitive. Full results: [[trading bot/Backtest Results & Findings]].
- [x] **Phase 3 — Paper trading with human approval** — **built and executed for real, 2026-07-21.** `paper_trader.py` wires the momentum-rotation signal → diff against live IBKR positions → printed rebalance → explicit y/n approval → `place_bracket_order` execution (sized from RiskGuard's risk budget) → `trade_journal.csv`. First real rebalance: bought GOOGL (14), AAPL (15), JNJ (19) on paper account `DUQ903866`. Two real bugs were found and fixed the same day: (1) `place_market_order` mis-checked RiskGuard's position-count limit on closes, (2) the bracket stop leg defaulted to TIF=DAY and silently expired at end of session — all three positions were briefly unprotected until caught and re-protected with GTC stops. Momentum rotation still hasn't been through portfolio-level walk-forward validation, which is worth doing but doesn't block the paper-trading clock that's now running toward this phase's 2-3 month exit criteria.
- [ ] **Phase 4 — Tiny real capital** — not applicable yet; clock on the Phase 3 evidence window started 2026-07-21.

**Bottom line right now:** Phase 3 is live — don't treat it as a future step anymore. Grading (Phase 1) and momentum's walk-forward validation are both worth doing but run on their own timelines; neither blocks the paper-trading loop that's already executing, since that loop is rules-based (RiskGuard, bracket orders), not agent-based. The next legitimate steps are (a) start `grade_calls.py --csv` once notes are ≥5 days old (~2026-07-25+) and keep it weekly, (b) keep running `paper_trader.py` monthly/on-demand and verify positions + stops stay healthy (specifically: GTC, not DAY — see the bug above), and (c) put momentum rotation through the same rigorous validation the SMA strategy got, in parallel with (b) rather than before it. See [[trading bot/Backtest Results & Findings]] for the backtest reasoning, and [[trading bot/The App]] for the fuller current-state summary.

---

## The no-bullshit part first

Three things to settle before writing code, because they determine whether this project succeeds or quietly wastes six months.

**"Investing" and "day trading" are different problems.** Fundamental investing (Graham, Buffett, reading filings, valuing a business) is slow, low-frequency, and forgiving of noise. Day trading is fast, high-frequency, and dominated by noise, spreads, and other automated players. An agent that's good at one is not automatically good at the other. Pick a primary lane. Recommendation given "basic coding, basic AI knowledge": build a **research/evaluation agent** first (reads filings, financials, news, produces a written thesis) and a **separate, narrow, rules-based execution system** for any short-term trading, rather than one system trying to do both. Trying to build "an agent that predicts the market" as a first project is how most solo attempts die.

**Full autonomy is not the realistic v1.** "Execute trades most of the time, after my approval" is the right instinct, and it's exactly what's buildable: a human-in-the-loop system where the agent proposes, you approve, code executes. Full unattended autonomy on real money should be a v3 or v4, if ever, after months of evidence.

**Base rates**: most retail day traders lose money net of costs; most backtested strategies look great and die in live trading (overfitting); if you have less than $25k in a US margin account, the Pattern Day Trader rule limits you to 3 day trades per 5 rolling days — this alone kills naive day-trading plans and pushes toward swing trading (multi-day holds) unless you fund the account above $25k or use a cash account. Build for swing/position trading unless you've specifically solved for PDT. (This base-rate warning has since been confirmed by direct research — see [[trading bot/Backtest Results & Findings]]: ~97% of persistent day traders lose money per the academic studies, and even the one day-trading strategy with published, audited-style evidence (ORB) lost money in a 60-day live smoke test.)

None of this is a reason not to build the project — it's a reason to build it in the order below, with paper money for a long time before real money, and with tiny size when you go live.

---

## Recommended stack

| Layer | Tool | Why |
|---|---|---|
| Language | Python 3.11+ | Everything below assumes it (currently running 3.13) |
| Broker/data (paper first) | Alpaca (`alpaca-py`) — **originally recommended here, but actual build diverged to Interactive Brokers** (`ibkr_service.py`, via `ib_async`) instead, likely for its multi-asset coverage (stocks/forex/futures/crypto in one API). The IBKR path is now a sunk cost worth keeping: the paper connection is verified live (2026-07-21, account `DUQ903866`, port 4002) after IBKR's address-verification review cleared, and the execution layer is already hardened (RiskGuard, journal, bracket orders). Switching to Alpaca now would mean redoing that work for a broker that was never actually a blocker — the setup friction that made IBKR "heavier" already happened and is behind us. | Free paper trading account, real API, commission-free, good docs — industry-standard starting point |
| Backtesting | `backtesting.py` to start, `backtrader` once you need realism | `backtesting.py` is the simplest to learn; `backtrader` models slippage/order types and has a live-trading path so your backtest code and live code aren't two different worlds |
| Agent reasoning | Claude Agent SDK (Python) | Gives you the tool-calling loop, permission hooks, and memory for free instead of hand-rolling one |
| Approval channel | Telegram bot (`python-telegram-bot`) | Free, instant push notifications to your phone, two-way (approve/reject with a tap), simpler than building a UI |
| Scheduling | `cron` or Python `APScheduler` | Runs your data pulls and checks on a schedule without you babysitting it |
| Storage/logging | SQLite + plain log files | No need for a real database at this scale |

Everything here runs **locally on your machine**, not inside Cowork. Cowork's computer-use tools are explicitly barred from executing trades or moving money on your behalf — that's a hard restriction, not a config option. Cowork is useful for the research/monitoring side of this project (dashboards, digests) but the actual order-placing code needs to be a script you own and run, talking directly to the broker's API with your own keys.

---

## Phase 0 — Environment & basics (1–2 weeks) ✅ done

- Install Python, git, VS Code (or your editor of choice), and Claude Code (`npm install -g @anthropic-ai/claude-code` or see code.claude.com for the current install command).
- Open the Alpaca paper trading account (free, instant, no real money at risk) and generate API keys. Do this on day one — everything after this phase should run against it.
- Do the official Claude Code quickstart (interactive tutorial in the docs) so the CLI, `CLAUDE.md`, and basic prompting loop are muscle memory.
- Skim, don't master: pandas basics (DataFrames, `.rolling()`, `.merge()`) — you'll live in pandas for backtesting.

**Exit criteria:** you can pull a stock's price history from Alpaca in a Python script and print it, and you've had Claude Code fix a deliberately broken script for you once.

## Phase 1 — Research agent, no execution (2–3 weeks) — real output exists, grading not due yet

Goal: something that reads financials/filings/news and writes you a thesis. Zero trading logic yet.

- Write a script that pulls fundamentals (Alpaca or a free source like SEC EDGAR / `yfinance`) for a small watchlist (10–20 tickers you actually understand). ✅ done — `research_agent.py` pulls both daily (1y) and 15-minute (5d) price/volume via `yfinance`, plus best-effort fundamentals (market cap, P/E, margins, revenue growth, debt/equity). Indicator math now comes from the shared `indicators.py` module (also used by `trader_app.py`'s new chart view), so the agent reasons over exactly what the owner sees on screen.
- Feed that data plus a prompt grounded in specific frameworks (e.g., Graham's margin-of-safety checks, basic DCF, or whatever "fundamental works" you actually want it to apply — name the books/models explicitly in the prompt, don't rely on Claude's vague sense of "good investing") to Claude via the Agent SDK's `query()` and have it write a structured thesis: valuation, risks, thesis, confidence. ✅ done — frameworks: multi-timeframe trend alignment (SMA/EMA/MACD), momentum/mean-reversion context (RSI, MACD histogram, Bollinger position, squeeze), volume confirmation (OBV, VWAP), structure-aware levels (swing S/R, 52-week range, opening range), volatility-aware risk framing, and a valuation sanity check; the curated `knowledge/` library is injected into every prompt too.
- Log every output to a file. Read it. Cross-check a few against your own judgment. This is where you calibrate whether the agent's "reasoning" is actually useful or just plausible-sounding. ✅ **logging is real now** — `research_log/` holds 12 real notes, one per full-watchlist ticker, generated 2026-07-20/21. Spot-checking AAPL and NVDA: grounded reasoning, no invented levels, appropriately calibrated confidence, mostly honest "no-edge" verdicts. ❌ **the automated half is not done** — `grade_calls.py` has not been re-run since these real notes were written; the only `graded_calls.csv` on disk is still the old synthetic-data test. A qualitative spot-check is not calibration evidence.

**Exit criteria:** a script you can run on-demand that turns a ticker into a written, sourced investment thesis you'd be willing to show someone. **Met, for the "produces a thesis" half** — 12 real, readable notes exist. **Not yet met for the calibration half** — the plan's own broader point (below) is that grading, not enthusiasm, is what earns trust, and no grading of real output has happened. Run `grade_calls.py --csv` before treating this phase as done.

## Phase 2 — One narrow strategy + rigorous backtesting (3–4 weeks) ✅ done — strategy rejected, replacement identified

- Pick **one** simple, explicit strategy — not "the agent decides." Example used: "buy when 20-day SMA crosses above 50-day SMA on liquid large-caps, sell on cross-under, max 5 positions." Simplicity is a feature here, not a compromise.
- Implemented in `backtesting.py` (`sma_crossover_backtest.py`), with realistic commission assumptions (0.1%/trade).
- Validated properly: in-sample (2010–2018) vs. out-of-sample (2019–present) split, benchmarked against SPY and against holding each stock outright.
- **Result: it does not beat buy-and-hold** — 0/10 tickers beat SPY out-of-sample, 1/10 beat their own buy-and-hold (DIS, only because DIS itself lost money). Per this phase's own exit criteria below, the strategy does **not** advance to Phase 3.
- A broader shootout (`strategy_shootout.py`) then tested 5 strategy families the same way. Only **monthly momentum rotation** was competitive with SPY.

**Full numbers and the "why it lost" explanation:** [[trading bot/Backtest Results & Findings]]

**Exit criteria (as originally written):** *"If it doesn't beat buy-and-hold after costs on out-of-sample data, it's not ready — iterate or pick a different strategy, don't skip to live."* — met: the strategy didn't beat buy-and-hold, so per the plan itself, iterate (momentum rotation) rather than proceed to Phase 3 with the SMA rule.

## Phase 3 — Paper trading with human approval (min. 2–3 months) — v1 built and LIVE since 2026-07-21

This is the core of "execute trades most of the time, after my approval." **The v1 built is a terminal-based proposal + y/n loop (`paper_trader.py`), not the Agent-SDK-tool-with-PreToolUse-hook version originally scoped below — that's a reasonable v2, not what got built first.**

- `ibkr_service.py`'s order-placement side: `place_bracket_order` (limit entry + stop, atomic — the intended default) and a stop-guarded `place_market_order`, both sitting behind `verify_paper_account()` and a `RiskGuard` with code-enforced limits (order notional, position count, daily-loss circuit breaker). **This has now been called against the real account** — `paper_trader.py` uses it directly (not wrapped as an Agent SDK tool; it's a plain Python script with a terminal `input()` gate). See [[trading bot/README_trader_app]] and [[IBKR Integration]] for the full detail.
- The originally-scoped **Agent SDK hooks (`PreToolUse`) + Telegram approval** version is **still not built** — genuinely a v2 idea, not something the terminal y/n loop pretends to be. Worth doing once the simpler loop has a track record.
- Running against the **paper account only**, started 2026-07-21, target 2–3 months minimum. Every trade is logged: what was proposed, what was approved, what executed — `trade_journal.csv` now has real entries (first rebalance: bought GOOGL, AAPL, JNJ), not just the empty schema.
- Set hard risk rules in code (not just in the prompt): max position size, max daily trades, max daily loss that halts the bot. Enforce these in Python, never rely on the model to self-regulate risk. ✅ **done, in `ibkr_service.py`'s `RiskGuard`** (separate from and in addition to the SMA/momentum backtester's optional "risk engine" mentioned below) — $5,000 max order notional, 5 max open positions, $300 daily-loss circuit breaker, stop required, all overridable only by editing `risk_limits.json` explicitly. **One gap found and closed 2026-07-21:** the stop-required check only verifies a stop is submitted at entry, not that it stays alive — the stop leg defaulted to a DAY time-in-force and silently expired end-of-session; fixed to GTC, see [[Risk Management System]].

**Exit criteria:** 2–3 months of paper trading logs showing the strategy beats its benchmark after simulated costs, and an approval log showing you're not just rubber-stamping everything. **Clock started 2026-07-21.**

**Momentum rotation is running without portfolio-level walk-forward validation yet** — worth doing in parallel with the paper-trading window, not as a prerequisite to it; the trading loop itself is rules-based (RiskGuard, bracket orders), so it doesn't need the research agent or the walk-forward test to be "done" first.

## Phase 4 — Tiny real capital (only after Phase 3 earns it) — not applicable yet

- Switch the API endpoint from paper to live, starting with capital you can fully afford to lose — think tens to low hundreds of dollars, not your savings.
- Keep the human-approval gate on. Don't remove it just because paper trading went well; live markets and live psychology are different from paper.
- Keep the daily-loss circuit breaker and position limits from Phase 3, enforced in code.
- Re-evaluate monthly against the benchmark. If it underperforms buy-and-hold for two straight months net of costs, stop and go back to Phase 2.

Only after sustained, multi-month live evidence would loosening the approval requirement (e.g., auto-approve only for trades under some tiny size) even be worth considering. Full autonomy on meaningful capital is not a near-term goal for a first project — treat any plan that promises that as marketing, including your own excitement about it.

---

## Claude feature education plan, mapped to phases

Don't front-load all of this — learn each piece when the phase needs it.

**Before Phase 0 is done:** Claude Code CLI basics — running it in a project folder, `CLAUDE.md` for project memory, basic prompting for code changes. This is the tool you'll use to actually write and debug all the Python in Phases 1–3.

**During Phase 1:** Claude Agent SDK fundamentals (Python) — install `claude-agent-sdk`, understand `query()`, `ClaudeAgentOptions`, and built-in tools (Read/Write/Bash). This is the library form of Claude Code you'll embed inside your own agent process, as opposed to using the CLI interactively. ✅ done in `research_agent.py` — though it calls `query()` with `allowed_tools=[]` (data is precomputed in Python and handed to the prompt as text, not fetched by the model via tool calls), so it's the simpler "no tools" pattern, not yet exercising custom tools (below).

**During Phase 1–2:** Custom tools in the Agent SDK — wrapping your own Python functions (pull price data, compute an indicator, call Alpaca) as tools the agent can call. This is how "the agent" stops being just a chat window and starts being able to act on your data. Not yet done — `research_agent.py` precomputes everything and hands it over as one prompt rather than giving the model callable tools.

**During Phase 3 (the critical one):** Agent SDK **hooks**, specifically `PreToolUse`. This is the exact mechanism for the approval requirement — a hook can inspect a proposed tool call (an order), pause execution, notify you, and only proceed on approval. Read the hooks docs and the permissions docs together; that pairing is the whole safety architecture for this project.

**Optional, later:** MCP servers, if you want the agent talking to Telegram/Slack for approvals through a standard protocol instead of a bespoke bot script, or if you want it pulling data from a source that already has an MCP server built. Not required for v1 — a plain Telegram bot script is simpler and you understand every line of it.

**Cowork's role:** useful as the dashboard/monitoring layer — a scheduled task or artifact that shows open positions, recent trades, and performance vs. benchmark, refreshed automatically. Not the layer that places trades — keep that as a script you run yourself, for the reasons above.

---

## Realistic timeline

Given basic coding and AI experience, working part-time: **3–5 months** to a validated paper-trading system (end of Phase 3), and that's the honest earliest point at which risking real money is defensible. If that timeline is discouraging, that's useful information about the actual difficulty of this project, not a sign of doing it wrong.

## What to build next

Given where things actually stand (Phase 0 done, Phase 1 has real output with grading due ~2026-07-25, Phase 2's original strategy rejected and momentum rotation identified as the replacement, and Phase 3 built and executing real paper trades since 2026-07-21):

1. **Keep the ongoing operational loop running**, not a one-time build anymore: re-run `research_agent.py` weekly (next ~2026-07-28), re-run `paper_trader.py` monthly/on-demand for rebalances, and periodically verify open positions still have working **GTC** stops (not DAY — see the bug in [[Risk Management System]]).
2. **Grade the 12 real notes** once they're old enough (~2026-07-25+): `python3 grade_calls.py --csv`, then a weekly habit. Not overdue — just not due yet.
3. Put **momentum rotation** through the same rigor the SMA strategy got: portfolio-level simulation with the "max N positions" constraint, walk-forward validation, and the risk engine's drawdown circuit breaker wired in. Worth doing in parallel with the live paper-trading window now underway, not as a gate before it (that ship sailed 2026-07-21).
4. Decide between `research_agent.py` and `trading_agent_service.py` rather than letting the second one sit unused indefinitely — lower priority than 1–3, but still an open loop.
5. Web UI (`TraderAppFullStack.txt`, found in the folder — a FastAPI + React spec) is now legitimately unblocked — real fills exist in `trade_journal.csv`. Still lower priority than 1–3, which is where the actual evidence this project is gated on comes from.
