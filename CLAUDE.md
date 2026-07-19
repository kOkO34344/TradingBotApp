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

- `trader_app.py` — interactive terminal app (rich/plotext). Menus: SMA backtests
  (in/out-of-sample), ticker deep dive, momentum rotation backtest (portfolio),
  settings (persisted in `trader_settings.json`), IBKR paper inspection
  (read-only: account, positions, live 15-min bars).
- `ibkr_service.py` — IBKR layer via ib_async: connect (paper-guarded), contract
  builders per asset class, 15-min bars (one-shot + streaming; MIDPOINT for FX,
  AGGTRADES for crypto), RiskGuard, bracket orders, trade journal.
  `python3 ibkr_service.py --selftest` = 18 offline checks, all passing.
- `research_agent.py` — Phase 1 research agent (claude-agent-sdk). Computes
  multi-timeframe indicators (daily + 15m), injects `knowledge/*.md`, writes
  structured thesis notes to `research_log/`. `--dry-run` shows the prompt.
- `grade_calls.py` — grades research notes vs actual forward returns (5d/21d),
  calibration by confidence bucket. This report IS the agent's track record.
- `knowledge/` — curated library, verified sources only (rules in its README).
- Analysis scripts: `sma_crossover_backtest.py`, `variant_experiments.py`,
  `strategy_shootout.py`, `orb_backtest.py`. Reports: `backtest_report.md`,
  `day_trader_research.md`, `trading_agent_plan.md` (the master plan).
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
- Phase 2 (infrastructure): hardened and self-tested. TWS not yet installed on
  the owner's Mac — first connected smoke test still pending.
- Phase 3 (paper trading with approval loop): NOT built. Next major milestone:
  signal engine (momentum rotation first) → proposed order → owner approval →
  paper execution via bracket orders → journal. An LLM is never in the intraday
  firing loop; rules fire at machine speed, the agent reasons at research speed.
- Phase 4 (tiny real capital): locked until months of Phase 3 evidence.

## Work queue for Claude Code (in order — finish the job)

1. **TWS smoke test.** Owner installs TWS (paper login, API enabled, port 7497).
   Then run `python3 ibkr_service.py` connected; fix whatever surfaces
   (contract qualification, market-data permissions, pacing). Commit.
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
