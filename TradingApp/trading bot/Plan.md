---
tags: [trading-bot, plan]
source: /Users/kaloyanivanov/TradingBotApp
last_synced: 2026-07-19
---

# Building your own investing/trading agent — a real plan

## Status against this plan (as of 2026-07-19)

- [x] **Phase 0 — Environment & basics** — done (venv, Python 3.13, dependencies installed)
- [ ] **Phase 1 — Research agent, no execution** — **built, but not exercised.** `research_agent.py` + `grade_calls.py` now exist, built exactly to this plan's original spec (Claude Agent SDK, multi-timeframe indicators computed from real data, structured logged thesis, weekly grading against forward returns) with a curated `knowledge/` library feeding every prompt. But `research_log/` holds only **two files, both explicitly marked `SYNTHETIC TEST NOTE — not a real call`** — zero real ticker analyses have been run, so the exit criteria below is not met yet. A second, competing candidate also exists unused: `trading_agent_service.py` wraps the third-party **TradingAgents** multi-agent library. Neither has been run for real. See [[trading bot/README_trader_app]].
- [x] **Phase 2 — One narrow strategy + rigorous backtesting** — done, and the honest result came back: the SMA 20/50 crossover **does not beat buy-and-hold**, in- or out-of-sample. Per this plan's own exit criteria, it does not advance to Phase 3 as-is. A follow-up shootout across 5 strategy families found only **momentum rotation** competitive. Full results: [[trading bot/Backtest Results & Findings]].
- [ ] **Phase 3 — Paper trading with human approval** — not started. A **read-only** IBKR connection layer (`ibkr_service.py`, menu option 8) now exists — account summary, positions, live bars — but its `place_market_order` function is not wired to anything. Per the plan below, this phase should not start until a strategy has actually cleared Phase 2's bar — momentum rotation is the current candidate, but it hasn't yet been through the same rigor (portfolio-level risk engine, walk-forward check) that the SMA strategy got before being rejected. Wiring `place_market_order` to a live signal is itself a Phase 3 action, not something to do early.
- [ ] **Phase 4 — Tiny real capital** — not applicable yet

**Bottom line right now:** don't skip to Phase 3, and don't count Phase 1 as done just because the code exists — the exit criteria is a real, sourced thesis someone would show another person, and none has been produced yet. The next legitimate step is either (a) delete the synthetic test notes, run `research_agent.py` for real on a handful of known tickers, and start the weekly `grade_calls.py` habit, (b) do the same evaluation for the `trading_agent_service.py` alternative and pick one, or (c) put momentum rotation through the same rigorous validation the SMA strategy got. See [[trading bot/Backtest Results & Findings]] for the backtest reasoning, and [[trading bot/The App]] for the fuller list of open side paths.

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
| Broker/data (paper first) | Alpaca (`alpaca-py`) — **originally recommended here, but actual build diverged to Interactive Brokers** (`ibkr_service.py`, via `ib_async`) instead, likely for its multi-asset coverage (stocks/forex/futures/crypto in one API). Worth a deliberate decision, not silent drift: IBKR's API/TWS setup is heavier than Alpaca's, and Alpaca was picked originally for its simplicity. If multi-asset isn't actually needed for the strategy that ends up going to Phase 3, Alpaca may still be the better fit. | Free paper trading account, real API, commission-free, good docs — industry-standard starting point |
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

## Phase 1 — Research agent, no execution (2–3 weeks) — built (`research_agent.py`), exit criteria not yet met

Goal: something that reads financials/filings/news and writes you a thesis. Zero trading logic yet.

- Write a script that pulls fundamentals (Alpaca or a free source like SEC EDGAR / `yfinance`) for a small watchlist (10–20 tickers you actually understand). ✅ done — `research_agent.py` pulls both daily (1y) and 15-minute (5d) price/volume via `yfinance`, plus best-effort fundamentals (market cap, P/E, margins, revenue growth, debt/equity).
- Feed that data plus a prompt grounded in specific frameworks (e.g., Graham's margin-of-safety checks, basic DCF, or whatever "fundamental works" you actually want it to apply — name the books/models explicitly in the prompt, don't rely on Claude's vague sense of "good investing") to Claude via the Agent SDK's `query()` and have it write a structured thesis: valuation, risks, thesis, confidence. ✅ done — the named frameworks are multi-timeframe trend alignment, momentum/mean-reversion context, volatility-aware risk framing, and a valuation sanity check; a curated `knowledge/` library (this project's own verified backtest findings, distilled) is injected into every prompt too, going beyond the original spec.
- Log every output to a file. Read it. Cross-check a few against your own judgment. This is where you calibrate whether the agent's "reasoning" is actually useful or just plausible-sounding. ⚠️ **partially done, and not for real** — logging to `research_log/<TICKER>_<date>.md` works, and `grade_calls.py` exists to do the calibration check automatically (win rate by direction and by confidence bucket). But the only two files in `research_log/` are explicitly labeled synthetic test data, not real output. Nobody has actually read a real note from this agent yet.

**Exit criteria:** a script you can run on-demand that turns a ticker into a written, sourced investment thesis you'd be willing to show someone. **The script exists and is capable of this — the criterion is still unmet** because it hasn't actually produced one. This is a real, meaningful distinction, not pedantry: per this project's own rule ("autonomy is earned by graded evidence, never by adding capability"), building the capability is not the same as earning the check mark.

## Phase 2 — One narrow strategy + rigorous backtesting (3–4 weeks) ✅ done — strategy rejected, replacement identified

- Pick **one** simple, explicit strategy — not "the agent decides." Example used: "buy when 20-day SMA crosses above 50-day SMA on liquid large-caps, sell on cross-under, max 5 positions." Simplicity is a feature here, not a compromise.
- Implemented in `backtesting.py` (`sma_crossover_backtest.py`), with realistic commission assumptions (0.1%/trade).
- Validated properly: in-sample (2010–2018) vs. out-of-sample (2019–present) split, benchmarked against SPY and against holding each stock outright.
- **Result: it does not beat buy-and-hold** — 0/10 tickers beat SPY out-of-sample, 1/10 beat their own buy-and-hold (DIS, only because DIS itself lost money). Per this phase's own exit criteria below, the strategy does **not** advance to Phase 3.
- A broader shootout (`strategy_shootout.py`) then tested 5 strategy families the same way. Only **monthly momentum rotation** was competitive with SPY.

**Full numbers and the "why it lost" explanation:** [[trading bot/Backtest Results & Findings]]

**Exit criteria (as originally written):** *"If it doesn't beat buy-and-hold after costs on out-of-sample data, it's not ready — iterate or pick a different strategy, don't skip to live."* — met: the strategy didn't beat buy-and-hold, so per the plan itself, iterate (momentum rotation) rather than proceed to Phase 3 with the SMA rule.

## Phase 3 — Paper trading with human approval (min. 2–3 months) — not started

This is the core of "execute trades most of the time, after my approval."

- Wrap the order-placement call as a custom tool in the Agent SDK — originally scoped as Alpaca, but `place_market_order` in `ibkr_service.py` is the function actually sitting in the folder today (unwired, read-only layer otherwise — see [[trading bot/README_trader_app]]). Whichever broker is used, this wrapping-as-a-tool-with-a-PreToolUse-hook step is what actually matters, not the specific broker.
- Use the SDK's **hooks** (`PreToolUse`) to intercept every order-placement call before it fires: the hook sends a Telegram message with the proposed trade (ticker, side, size, reasoning) and blocks execution until you reply approve/reject. This hook mechanism is the SDK feature that maps directly onto "execute trades most of the time, after my approval" — it's not a workaround, it's what it's designed for.
- Run this against the **paper account only** for a minimum of 2–3 months. Track every trade in a log: what was proposed, what you approved/rejected, and why in hindsight you were right or wrong to.
- Set hard risk rules in code (not just in the prompt): max position size, max daily trades, max daily loss that halts the bot. Enforce these in Python, never rely on the model to self-regulate risk. (A first version of this — trend filter + ATR stop + fixed-fractional sizing — already exists as the app's optional "risk engine," see [[trading bot/README_trader_app]].)

**Exit criteria:** 2–3 months of paper trading logs showing the strategy beats its benchmark after simulated costs, and an approval log showing you're not just rubber-stamping everything.

**Do not enter this phase with the SMA crossover as-is** — it hasn't earned it. If entering this phase, momentum rotation is the current best candidate, but it should get the same walk-forward/portfolio-level scrutiny first.

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

Given where things actually stand (Phase 0 and 2 done, Phase 1's infrastructure built but unused, Phase 2's original strategy rejected):

1. **Use Phase 1, don't just admire it:** delete the two synthetic notes in `research_log/`, run `research_agent.py` for real on a handful of tickers you know well, read the output critically, and start running `grade_calls.py` on a weekly cadence. Also do a real evaluation run of the competing `trading_agent_service.py` (TradingAgents wrapper) and decide which one earns a place in the workflow — maintaining two unused Phase 1 candidates indefinitely isn't progress.
2. Put **momentum rotation** through the same rigor the SMA strategy got: portfolio-level simulation with the "max N positions" constraint, walk-forward validation, and the risk engine's drawdown circuit breaker wired in.
3. Don't start Phase 3 (paper trading) until one of those produces a strategy that's actually earned it per the Phase 2 exit criteria. The read-only `ibkr_service.py` layer (menu option 8) is fine to leave as-is until then — it needs nothing further for Phase 2 or Phase 1 work.
