# Building your own investing/trading agent — a real plan

## The no-bullshit part first

Three things to settle before writing code, because they determine whether this project succeeds or quietly wastes six months.

**"Investing" and "day trading" are different problems.** Fundamental investing (Graham, Buffett, reading filings, valuing a business) is slow, low-frequency, and forgiving of noise. Day trading is fast, high-frequency, and dominated by noise, spreads, and other automated players. An agent that's good at one is not automatically good at the other. Pick a primary lane. My recommendation given "basic coding, basic AI knowledge": build a **research/evaluation agent** first (reads filings, financials, news, produces a written thesis) and a **separate, narrow, rules-based execution system** for any short-term trading, rather than one system trying to do both. Trying to build "an agent that predicts the market" as a first project is how most solo attempts die.

**Full autonomy is not the realistic v1.** You said "execute trades most of the time, after my approval" — that's the right instinct, and it's exactly what's buildable: a human-in-the-loop system where the agent proposes, you approve, code executes. Full unattended autonomy on real money should be a v3 or v4, if ever, after months of evidence.

**Base rates**: most retail day traders lose money net of costs; most backtested strategies look great and die in live trading (overfitting); if you have less than $25k in a US margin account, the Pattern Day Trader rule limits you to 3 day trades per 5 rolling days — this alone kills naive day-trading plans and pushes toward swing trading (multi-day holds) unless you fund the account above $25k or use a cash account. Build for swing/position trading unless you've specifically solved for PDT.

None of this is a reason not to build the project — it's a reason to build it in the order below, with paper money for a long time before real money, and with tiny size when you go live.

---

## Recommended stack

| Layer | Tool | Why |
|---|---|---|
| Language | Python 3.11+ | Everything below assumes it |
| Broker/data (paper first) | Alpaca (`alpaca-py`) | Free paper trading account, real API, commission-free, good docs — industry-standard starting point |
| Backtesting | `backtesting.py` to start, `backtrader` once you need realism | `backtesting.py` is the simplest to learn; `backtrader` models slippage/order types and has a live-trading path so your backtest code and live code aren't two different worlds |
| Agent reasoning | Claude Agent SDK (Python) | Gives you the tool-calling loop, permission hooks, and memory for free instead of hand-rolling one |
| Approval channel | Telegram bot (`python-telegram-bot`) | Free, instant push notifications to your phone, two-way (approve/reject with a tap), simpler than building a UI |
| Scheduling | `cron` or Python `APScheduler` | Runs your data pulls and checks on a schedule without you babysitting it |
| Storage/logging | SQLite + plain log files | No need for a real database at this scale |

Everything here runs **locally on your machine**, not inside Cowork. Cowork's computer-use tools are explicitly barred from executing trades or moving money on your behalf — that's a hard restriction, not a config option. Cowork is useful for the research/monitoring side of this project (dashboards, digests) but the actual order-placing code needs to be a script you own and run, talking directly to the broker's API with your own keys.

---

## Phase 0 — Environment & basics (1–2 weeks)

- Install Python, git, VS Code (or your editor of choice), and Claude Code (`npm install -g @anthropic-ai/claude-code` or see code.claude.com for the current install command).
- Open the Alpaca paper trading account (free, instant, no real money at risk) and generate API keys. Do this on day one — everything after this phase should run against it.
- Do the official Claude Code quickstart (interactive tutorial in the docs) so the CLI, `CLAUDE.md`, and basic prompting loop are muscle memory.
- Skim, don't master: pandas basics (DataFrames, `.rolling()`, `.merge()`) — you'll live in pandas for backtesting.

**Exit criteria:** you can pull a stock's price history from Alpaca in a Python script and print it, and you've had Claude Code fix a deliberately broken script for you once.

## Phase 1 — Research agent, no execution (2–3 weeks)

Goal: something that reads financials/filings/news and writes you a thesis. Zero trading logic yet.

- Write a script that pulls fundamentals (Alpaca or a free source like SEC EDGAR / `yfinance`) for a small watchlist (10–20 tickers you actually understand).
- Feed that data plus a prompt grounded in specific frameworks (e.g., Graham's margin-of-safety checks, basic DCF, or whatever "fundamental works" you actually want it to apply — name the books/models explicitly in the prompt, don't rely on Claude's vague sense of "good investing") to Claude via the Agent SDK's `query()` and have it write a structured thesis: valuation, risks, thesis, confidence.
- Log every output to a file. Read it. Cross-check a few against your own judgment. This is where you calibrate whether the agent's "reasoning" is actually useful or just plausible-sounding.

**Exit criteria:** a script you can run on-demand that turns a ticker into a written, sourced investment thesis you'd be willing to show someone.

## Phase 2 — One narrow strategy + rigorous backtesting (3–4 weeks)

- Pick **one** simple, explicit strategy — not "the agent decides." Example: "buy when 20-day SMA crosses above 50-day SMA on liquid large-caps, sell on cross-under, max 5 positions." Simplicity is a feature here, not a compromise.
- Implement it in `backtesting.py`, then port to `backtrader` once it works, including realistic commission and slippage assumptions (Alpaca's are close to zero for stocks, but slippage on fast-moving names is real).
- Validate properly: split data into train/test periods, walk-forward test, and compare against just holding SPY over the same period. If it doesn't beat buy-and-hold after costs on out-of-sample data, it's not ready — iterate or pick a different strategy, don't skip to live.
- Use Claude Code here as a pair programmer for the backtest code itself — this is exactly the "fix bugs, write tests, iterate on a codebase" use case it's built for.

**Exit criteria:** a strategy with a backtest report (returns, drawdown, win rate, vs. benchmark) you trust because you tried to break it, not because it looked good on the first run.

## Phase 3 — Paper trading with human approval (min. 2–3 months)

This is the core of what you asked for: agent proposes, you approve, code executes.

- Wrap your Alpaca order-placement call as a custom tool in the Agent SDK.
- Use the SDK's **hooks** (`PreToolUse`) to intercept every order-placement call before it fires: the hook sends you a Telegram message with the proposed trade (ticker, side, size, reasoning) and blocks execution until you reply approve/reject. This hook mechanism is the SDK feature that maps directly onto "execute trades most of the time, after my approval" — it's not a workaround, it's what it's designed for.
- Run this against the **paper account only** for a minimum of 2–3 months. Track every trade in a log: what was proposed, what you approved/rejected, and why in hindsight you were right or wrong to.
- Set hard risk rules in code (not just in the prompt): max position size, max daily trades, max daily loss that halts the bot. Enforce these in Python, never rely on the model to self-regulate risk.

**Exit criteria:** 2–3 months of paper trading logs showing the strategy beats its benchmark after simulated costs, and an approval log showing you're not just rubber-stamping everything.

## Phase 4 — Tiny real capital (only after Phase 3 earns it)

- Switch the API endpoint from paper to live, starting with capital you can fully afford to lose — think tens to low hundreds of dollars, not your savings.
- Keep the human-approval gate on. Don't remove it just because paper trading went well; live markets and live psychology are different from paper.
- Keep the daily-loss circuit breaker and position limits from Phase 3, enforced in code.
- Re-evaluate monthly against the benchmark. If it underperforms buy-and-hold for two straight months net of costs, stop and go back to Phase 2.

Only after sustained, multi-month live evidence would loosening the approval requirement (e.g., auto-approve only for trades under some tiny size) even be worth considering. Full autonomy on meaningful capital is not a near-term goal for a first project — treat any plan that promises that as marketing, including your own excitement about it.

---

## Claude feature education plan, mapped to phases

Don't front-load all of this — learn each piece when the phase needs it.

**Before Phase 0 is done:** Claude Code CLI basics — running it in a project folder, `CLAUDE.md` for project memory, basic prompting for code changes. This is the tool you'll use to actually write and debug all the Python in Phases 1–3.

**During Phase 1:** Claude Agent SDK fundamentals (Python) — install `claude-agent-sdk`, understand `query()`, `ClaudeAgentOptions`, and built-in tools (Read/Write/Bash). This is the library form of Claude Code you'll embed inside your own agent process, as opposed to using the CLI interactively.

**During Phase 1–2:** Custom tools in the Agent SDK — wrapping your own Python functions (pull price data, compute an indicator, call Alpaca) as tools the agent can call. This is how "the agent" stops being just a chat window and starts being able to act on your data.

**During Phase 3 (the critical one):** Agent SDK **hooks**, specifically `PreToolUse`. This is the exact mechanism for your approval requirement — a hook can inspect a proposed tool call (an order), pause execution, notify you, and only proceed on approval. Read the hooks docs and the permissions docs together; that pairing is the whole safety architecture for this project.

**Optional, later:** MCP servers, if you want the agent talking to Telegram/Slack for approvals through a standard protocol instead of a bespoke bot script, or if you want it pulling data from a source that already has an MCP server built. Not required for v1 — a plain Telegram bot script is simpler and you understand every line of it.

**Cowork's role:** useful as the dashboard/monitoring layer — a scheduled task or artifact that shows you open positions, recent trades, and performance vs. benchmark, refreshed automatically. Not the layer that places trades — keep that as a script you run yourself, for the reasons above.

---

## Realistic timeline

Given basic coding and AI experience, working part-time: **3–5 months** to a validated paper-trading system (end of Phase 3), and that's the honest earliest point at which risking real money is defensible. If that timeline is discouraging, that's useful information about the actual difficulty of this project, not a sign you're doing it wrong.

## What to build first, literally tomorrow

1. Open the Alpaca paper account and get API keys.
2. Install Claude Code and `claude-agent-sdk`.
3. Write a 20-line script that pulls one year of daily bars for one ticker and prints the last 5 rows.

That's it. Everything above builds on that one script.
