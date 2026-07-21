# TradingBotApp — project memory

Multi-asset trading system (stocks/ETFs, forex, futures incl. commodities, crypto)
built incrementally with strict evidence gates. Owner: Koko. Broker: Interactive
Brokers (paper account first, always).

## Non-negotiable rules

1. **Paper before real money.** `connect()` and `verify_paper_account()` in
   `ibkr_service.py` refuse live ports/accounts unless `allow_live=True` is
   passed explicitly. Do not weaken these guards; do not pass allow_live
   anywhere without the owner explicitly asking in that session.
2. **No order without a stop.** `place_bracket_order` is the default entry path.
   Bare market orders require `allow_no_stop=True` deliberately.
3. **RiskGuard limits live in `risk_limits.json`** (order notional, max positions,
   daily-loss circuit breaker). Enforced in code, never in prompts. Changing
   limits is an explicit edit, not a side effect.
4. **Honest backtesting.** In/out-of-sample split, after costs, vs buy-and-hold
   SPY. Never tune parameters until a backtest looks good and call it validated.
   Negative results get reported, not massaged.
5. **Autonomy is earned by graded evidence** (`research_log/` + `grade_calls.py`
   calibration + months of paper trading), never by adding capability.
6. Every order attempt/block/fill goes to `trade_journal.csv`. If it's not in
   the journal, it didn't happen.

## Architecture

File purposes are documented in each script's own module docstring
(`trader_app.py`, `ibkr_service.py`, `research_agent.py`, `grade_calls.py`,
`indicators.py`) — read those rather than duplicating them here.

- `indicators.py` is the SINGLE SOURCE OF TRUTH for technical math, shared by
  trader_app charts and research_agent prompts (human and AI see identical
  numbers). It has `--selftest`. Never reimplement indicators elsewhere —
  including in any future web dashboard.
- `trading_agent_service.py` — third-party TradingAgents wrapper. NEVER RUN yet;
  daily-granularity only, candidate for one evaluation run vs research_agent.

## Empirical findings so far (do not re-litigate without new evidence)

- Out-of-sample 2019-2026, 10 large caps, after costs: SMA 20/50, golden cross,
  Donchian, RSI-2 ALL lost to buy-and-hold. Risk overlays cut drawdown but not
  the performance gap.
- Momentum rotation (top-3 of 10 by 12-mo return, monthly) ≈18.5% CAGR vs SPY 16%,
  max DD -21.7% vs -33.7% — the only strategy family that earned Phase 3.
  Caveat: hand-picked mega-cap universe flatters it; broad-universe test pending.
- ORB (Zarattini/Aziz rules) on recent 60 days of 5m QQQ: -12.6%. Regime-dependent.
- ICT/SMC influencer methods: no verifiable evidence (see knowledge/01).

## Current phase status

- Phase 1 (research agent): built, needs real runs + graded calls accumulating.
- Phase 2 (infrastructure): hardened and self-tested. IBKR's address-verification
  review cleared 2026-07-21 — connected smoke test PASSED against IB Gateway
  paper (port 4002, account DUQ903866): `verify_paper_account` succeeded, pulled
  45 rows of AAPL 15-min bars. `trader_settings.json.ibkr_port` updated to 4002
  to match. `diagnose_ibkr.sh` / `wait_and_test_ibkr.sh` still there if the
  connection ever needs re-diagnosing.
- Phase 3 (paper trading with approval loop): BUILT and executed once for
  real, 2026-07-21 — `paper_trader.py`. First rebalance: bought GOOGL (14),
  AAPL (15), JNJ (19) on the paper account. Note: the paper account is
  EUR-denominated with no live market-data subscription — `paper_trader.py`
  converts NetLiquidation to USD via the EURUSD rate and requests delayed
  data (`reqMarketDataType(3)`). No scheduler yet — owner runs it manually.
  An LLM is never in the intraday firing loop; rules fire at machine speed,
  the agent reasons at research speed.
  **SAFETY BUG found + fixed same day:** `place_bracket_order`'s stop leg had
  no explicit TIF, so IBKR defaulted it to DAY — the stop silently expired
  at end of session, leaving all three positions completely unprotected for
  a period with no one aware. Fixed in `ibkr_service.py` (stop, and target if
  used, now explicitly `tif="GTC"`). All three positions were manually
  re-protected with fresh GTC stops the same day (see `trade_journal.csv`
  "re-protect" entries, ~23:34 UTC). **Lesson: after ANY bracket order,
  verify the stop is GTC and still open — don't trust "PreSubmitted" checked
  minutes after placement to mean it stays protected hours or days later.**
- Phase 4 (tiny real capital): locked until months of Phase 3 evidence.

## Work queue for Claude Code (in order — finish the job)

1. ~~TWS smoke test~~ — DONE 2026-07-21. Connected to IB Gateway paper (port
   4002), account DUQ903866 verified, pulled real 15-min bars. Note: two
   benign `ib_async` warnings on connect ("open orders request timed out",
   "completed orders request timed out") — harmless on a fresh account with
   no order history, not a code bug.
2. ~~Phase 3 paper-trading loop~~ (`paper_trader.py`) — DONE 2026-07-21 and
   run for real once. Signal: fresh (force-refetched) momentum ranking, top-N
   of watchlist. Sizing: `qty = floor((NetLiq_usd * risk_pct_per_trade%) /
   (2*ATR))`, clamped to `risk_limits.json`'s max order notional using the
   *buffered* entry price (not raw market price — a real bug hit and fixed
   during the first run). Exits cancel the open stop leg and confirm the
   cancel before flattening; exits run before entries so RiskGuard's
   max_open_positions headroom is freed first. `place_market_order` gained
   an `opening: bool` param so closes don't get checked as new exposure.
   `--dry-run` connects read-only and prints the proposal without asking.
   No scheduler yet — owner runs it manually. Next: a few more manual
   monthly cycles before even considering cron/launchd.
3. **Research agent — ongoing, in progress.** All 12 watchlist tickers got
   real notes 2026-07-20/21 (see `research_log/`). `grade_calls.py` isn't
   meaningful yet — notes are too fresh for even the 5-day forward-return
   horizon (earliest useful grading: ~2026-07-25+). Next actions:
   - Re-run `python3 research_agent.py <TICKER>` on the watchlist weekly
     (next due ~2026-07-28).
   - Once notes are ≥5 days old, start running `python3 grade_calls.py`
     and actually read the calibration report — don't just run it, look at it.
4. **Paper trading — operational, not a build task.** `paper_trader.py` holds
   real open positions (GOOGL/AAPL/JNJ, opened 2026-07-21, stops re-fixed to
   GTC same day after the DAY-TIF bug above). Going forward:
   - Re-run monthly (or on-demand) for the next rebalance; check `--dry-run`
     first if unsure what it'll propose.
   - **Every position check must verify stops are GTC, not just "present."**
     Query `ib.trades()` (not just `ib.openTrades()` right after placing) and
     check `order.tif == "GTC"` — a DAY stop will look fine for hours and
     then silently vanish at end of session. This is now fixed at the source
     (`place_bracket_order`), but any positions opened before this fix, or
     opened by other tools, should be checked.
   - Periodically sanity-check the account is healthy: positions still have
     working stops, no daily-loss circuit breaker trips, journal matches
     what's actually on IBKR. Don't just assume the last run is still current.
5. **Web UI (`TraderAppFullStack.txt`) — now legitimately unblocked.** Items
   1-2 are done AND real fills now exist in `trade_journal.csv`, so the
   original "a dashboard before fills exist would display zeros" objection
   no longer applies. Still backend-first: FastAPI wrapper around
   ibkr_service + journal reader, then a Vite frontend (NOT create-react-app
   — deprecated). Reasonable to start when the owner wants it, but weigh it
   against #3/#4 above — more research/trading cycles is more of the
   evidence this project is actually gated on; a dashboard is not.

## Known environment gotchas

- Owner's zsh doesn't allow `#` comments interactively — don't hand the owner
  paste-blocks containing comment lines (or tell them `setopt interactive_comments`).
- Node/npm may not be installed yet — check before any frontend work.
- **A daily launchd job runs `daily_vault_sync.sh`** (`~/Library/LaunchAgents/
  com.tradingbotapp.vaultsync.plist`, 22:00 local) — headless `claude -p`,
  scoped via `--allowedTools` to only Read/Edit/git-commit files under
  `TradingApp/trading bot/` (the Obsidian vault). It keeps the vault's
  living-status notes in sync with this file and auto-commits its own
  changes as "Daily vault sync: ...". It never touches code. Log:
  `vault_sync.log` / `vault_sync_launchd.log`. To disable:
  `launchctl unload ~/Library/LaunchAgents/com.tradingbotapp.vaultsync.plist`.
- `(base)` conda is always in the prompt; the project venv must ALSO show
  `(.venv)`. If imports fail, that's the first thing to check.

## Practical

- Python env: `.venv` in this folder; `pip install -r requirements.txt`.
- Owner's shell shows `(base)` conda AND `(.venv)` — make sure `.venv` is active.
- IBKR: TWS paper port 7497, Gateway 4002. Live ports are refused in code.
- Commit style: plain descriptive messages, commit after each working increment.
