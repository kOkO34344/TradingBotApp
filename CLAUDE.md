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
- Phase 2 (infrastructure): hardened and self-tested. IB Gateway 10.45 IS
  installed on the owner's Mac, but the connected smoke test is BLOCKED —
  the account has an address-verification issue in manual review at IBKR.
  Nothing to fix here until IBKR clears it. Full diagnostic history, root
  cause, and the exact steps to resume once cleared: `ibkr_setup_report.md`.
  Two ready-to-use scripts are waiting: `diagnose_ibkr.sh` (health check)
  and `wait_and_test_ibkr.sh` (polls for the paper API port + auto-runs the
  connected smoke test — just run it, no need to babysit the login).
- Phase 3 (paper trading with approval loop): NOT built. Next major milestone:
  signal engine (momentum rotation first) → proposed order → owner approval →
  paper execution via bracket orders → journal. An LLM is never in the intraday
  firing loop; rules fire at machine speed, the agent reasons at research speed.
- Phase 4 (tiny real capital): locked until months of Phase 3 evidence.

## Work queue for Claude Code (in order — finish the job)

1. **TWS smoke test — BLOCKED on IBKR, not on us.** Gateway is installed;
   the account is stuck in IBKR's address-verification manual review (see
   `ibkr_setup_report.md`). Once the owner confirms IBKR cleared it AND the
   paper account is provisioned (Client Portal → Settings → Account
   Configuration → Paper Trading Account, ~24h turnaround): run
   `./wait_and_test_ibkr.sh`, fix whatever surfaces (contract qualification,
   market-data permissions, pacing), commit. Don't re-diagnose the login
   step from scratch — read the report first.
2. **Phase 3 paper-trading loop** (`paper_trader.py`, new file):
   - Signal: momentum rotation (top-3 of watchlist by 12-mo return, monthly),
     reusing the logic in `trader_app.py:momentum_backtest`.
   - Proposal: print/log intended rebalance (what to sell, what to buy, sizes
     from RiskGuard limits) → require explicit owner y/n approval in terminal.
   - Execution: approved orders go through `place_bracket_order` (paper account,
     ATR-based stops). Everything journals to `trade_journal.csv`.
   - No scheduler yet: owner runs it manually first. Cron/launchd comes after
     a few clean manual cycles.
3. **Research agent live runs.** `python3 research_agent.py <TICKER>` on the
   watchlist weekly; `python3 grade_calls.py` to grade. Keep `research_log/`
   accumulating — this is the evidence that eventually loosens approval.
4. **Web UI (`TraderAppFullStack.txt`) — ONLY after 1-2 work.** Backend-first:
   FastAPI wrapper around ibkr_service + journal reader. Frontend with Vite
   (NOT create-react-app — deprecated). A dashboard before fills exist would
   display zeros; do not start here.

## Known environment gotchas

- Owner's zsh doesn't allow `#` comments interactively — don't hand the owner
  paste-blocks containing comment lines (or tell them `setopt interactive_comments`).
- Node/npm may not be installed yet — check before any frontend work.
- `(base)` conda is always in the prompt; the project venv must ALSO show
  `(.venv)`. If imports fail, that's the first thing to check.

## Practical

- Python env: `.venv` in this folder; `pip install -r requirements.txt`.
- Owner's shell shows `(base)` conda AND `(.venv)` — make sure `.venv` is active.
- IBKR: TWS paper port 7497, Gateway 4002. Live ports are refused in code.
- Commit style: plain descriptive messages, commit after each working increment.
