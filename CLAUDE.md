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
   **Every exposure limit is gated on `opening` and never blocks an exit.**
   A limit caps NEW exposure; blocking a close raises risk, which is the
   opposite of the job. Learned the hard way 2026-07-27: the then-$5,000
   notional cap blocked the exits for BOTH open positions — AAPL (bought
   15 × 328.04 = $4,921) and JNJ (19 × 249.98 = $4,750) were under the cap
   at entry, appreciated to $5,007/$5,005, and became un-exitable, so the
   rebalance silently held instead of rotating. The cap trapped *winners*
   specifically. `opening=False` had been passed correctly all along —
   only `max_open_positions` honoured it. Limits raised the same day to
   50000 / 2000 / 8. `require_stop_attached` stays ungated (rule 2).
4. **Honest backtesting.** In/out-of-sample split, after costs, vs buy-and-hold
   SPY. Never tune parameters until a backtest looks good and call it validated.
   Negative results get reported, not massaged.
5. **Autonomy is earned by graded evidence** (`research_log/` + `grade_calls.py`
   calibration + months of paper trading), never by adding capability.
6. Every order attempt/block/fill goes to `trade_journal.csv`. If it's not in
   the journal, it didn't happen.
7. **`autotrade_runner.py` is the one documented exception to rule 5 — flag it
   as such, don't treat it as precedent for anything else.** Built 2026-07-24
   at the owner's explicit, twice-confirmed request, despite BOTH signals it
   can run (momentum-hourly, Kronos-hourly) showing no measurable edge at
   that cadence (see Empirical findings). It removes the human approval
   prompt — RiskGuard stays fully enforced regardless. Off by default
   (`trader_settings.json`'s `autotrade.enabled`). Read the `autotrade` skill
   before touching this.
8. **Kronos is the project's main signal; momentum is DISABLED.** Owner
   instruction, 2026-07-28: momentum does not run again until Koko explicitly
   asks for it in that session. Enforced in code by `signal_policy.py`, not by
   convention — `paper_trader.compute_signal()` and
   `autotrade_signals.compute_live_momentum_hourly()` raise `SignalDisabled`
   unless a caller passes `allow_momentum=True`, and every
   `.get("signal", ...)` fallback now defaults to `kronos` so config drift
   can't resurrect it. `autotrade_runner.py` **refuses to fire** on a disabled
   signal (logs + texts, places nothing) rather than substituting a different
   one. Same opt-in pattern as rules 1 and 2 — don't pass `allow_momentum`
   without the owner asking in that session.
   **This runs against the project's own evidence, deliberately and with the
   owner's knowledge — record it that way, don't rationalize it.** Momentum
   rotation is still the only strategy family that ever earned Phase 3
   (~18.5% CAGR vs SPY 16%); Kronos measured Spearman IC 0.036 / 50.0% hit
   rate daily and IC -0.081 / 46.4% hourly, i.e. the enabled signal scored
   *worse* than the disabled one on the only head-to-head screen. Kronos being
   the focus is a research direction, not a validated edge. Rules 4 and 5 are
   unchanged: paper only, graded evidence, negative results reported. Backtest
   and research scripts (`strategy_shootout.py`, `broad_universe_momentum.py`)
   are NOT gated — they place no orders, and gating evidence-generation would
   defeat rule 4.
9. **FTMO is the trading venue; IBKR is retired in place.** Owner decision,
   2026-08-02. IBKR places no new orders but keeps monitoring — the three open
   positions (JNJ, DIS, AMZN) stay managed by `reflect_on_trades.py` and their
   GTC stops until they close naturally. Do NOT delete the IBKR code or unload
   its launchd jobs while a position is open: this project has documented what
   happens when code stops managing a position that still exists — the stop
   survives, but nothing will ever exit or record it.
   **The FTMO path runs FULLY UNATTENDED, and that is a second deliberate
   exception to rule 5 — flag it as such, exactly like rule 7, and do not treat
   it as precedent.** Requested explicitly on 2026-08-02 with the evidence
   position stated first: 0 graded calls, Kronos IC ~0 on the only screens run,
   and no IC screen at all yet for indices, FX or commodities. The rule engine,
   monitor and sizer are enforced regardless of autonomy — autonomy removes the
   human approval step, never a limit.
   **The IC-screen condition has been OVERRIDDEN, and this rule must say so
   rather than describe a gate that is no longer holding.** The original
   condition (owner's own, 2026-08-02) was that Kronos may only trade an asset
   class that has passed its own IC screen. All four classes were screened on
   2026-08-03 and **all four failed**. On 2026-08-05 the owner instructed the
   path to run anyway, with that evidence stated first, and it was **armed on
   2026-08-06** — `ftmo.autotrade.enabled` true, launchd firing 01:15 daily.
   **That is a THIRD deliberate exception to rule 5.** Flag it exactly like
   rules 7 and 9's second paragraph; it is not precedent.
   Two things to keep straight when reading this later. The gate was NOT
   re-run with different tickers or sample counts until something passed —
   that is the parameter-tuning rule 4 forbids and it did not happen. It was
   overridden knowingly, once, in the open. And the override is about the
   SIGNAL's evidence, not about the limits: the rule engine, the per-trade and
   portfolio risk caps, the server-side stop attached at entry, and the
   never-gated FLATTEN path all still apply unchanged.
   Do not quietly restore the old wording to make this rule read more
   comfortably, and do not cite the override as licence to skip a screen
   elsewhere.
   **The Challenge account is simulated, so this does not breach rule 1** — the
   real exposure is the entry fee, not trading capital. Phase 4 (real capital,
   IBKR) stays locked.

## Architecture

File purposes are documented in each script's own module docstring
(`trader_app.py`, `ibkr_service.py`, `research_agent.py`, `grade_calls.py`,
`indicators.py`) — read those rather than duplicating them here.

- `signal_policy.py` is the SINGLE SOURCE OF TRUTH for which signal may run
  and which is the default (rule 8). Every live signal path imports it;
  nothing decides this locally. Has a `python3 signal_policy.py` offline
  selftest. To change the project's focus signal, change `DEFAULT_SIGNAL` /
  `DISABLED_SIGNALS` there — not in five `.get()` fallbacks.

- **FTMO venue — five modules, all with offline `--selftest` (294 checks).**
  `ftmo_rules.py` decides (limits, three thresholds, both products),
  `ftmo_monitor.py` watches equity continuously, `ftmo_sizing.py` sizes,
  `ftmo_audit.py` records why, `ftmo_service.py` talks to cTrader.
  Three more since 2026-08-05: `ftmo_session.py` is the LONG-LIVED connection
  (streaming quotes, trendbars, orders — `ftmo_service` is one-shot and cannot
  trade), `ftmo_signal.py` turns a Kronos ranking into sized stop-protected
  orders, and `ftmo_smoke_order.py` proves the order path with one tiny trade.
  **`ftmo_runner.py` (2026-08-06) is the unattended runner** — the FTMO
  counterpart to `autotrade_runner.py`, armed by its own separate toggle.
  `ftmo_closes.py` (2026-08-08) detects positions that closed WITHOUT the
  runner — see the close-detection section below; it is the FTMO counterpart
  to `reflect_on_trades.py`.
  **579 offline checks, measured 2026-08-08** across the ten modules that
  carry a `--selftest`: `ftmo_runner` 100, `ftmo_sizing` 81, `ftmo_rules` 70,
  `ftmo_session` 70, `ftmo_monitor` 63, `ftmo_audit` 48, `ftmo_service` 43,
  `ftmo_closes` 43, `ftmo_signal` 35, `trade_journal` 26.
  (`ftmo_smoke_order.py` has no
  `--selftest` — it is a live one-trade proof, and its dry-run is the check.)
  Note `ftmo_audit`'s selftest deliberately prints `AUDIT WRITE FAILED` to
  stderr while testing an unwritable path; that is a passing test, not a
  failure — do not let a grep for "FAILED" convince you otherwise.
  **Read the `ftmo` skill before touching any of them** — it carries the agreed
  configuration, the derived dollar limits, and the invariants that are easy to
  break. The one to know without opening anything: every FTMO limit is measured
  on **equity including floating P&L**, so the account can fail with no order
  placed. That is why this venue gets a continuous monitor and not a pre-trade
  gate like `RiskGuard`, which structurally cannot see it.
- **`secrets_store.py` is the SINGLE SOURCE OF TRUTH for where credentials
  live.** All of them sit in `secrets/` (mode 700), one file per provider:
  `secrets/ctrader.env` (FTMO venue) and `secrets/telegram.env` (every phone
  alert). Contents are gitignored; `secrets/README.md` and the `*.example`
  templates are tracked. The `.gitignore` rule is `secrets/*` and NOT
  `secrets*` — an ignored *directory* is never descended into, so the
  template negations would silently do nothing.
  `resolve()` prefers the new path and **falls back to the legacy
  `./.env` / `TelegramBot/.env`**, deliberately: both consumers are on
  unattended paths, so a half-applied migration must degrade to "still works",
  never to "no notifications and nobody notices". When both exist the file in
  `secrets/` wins, so a forgotten copy cannot shadow the real one. The legacy
  paths currently exist as symlinks into `secrets/`, which is why an
  unmigrated checkout on this machine still runs.
  `python3 secrets_store.py --describe` reports what this machine holds
  without printing a value. Audited 2026-08-05 against the full git history:
  **no credential has ever been committed**, and this move was reorganisation,
  not leak cleanup. If one ever does leak, rotate it at the provider — moving
  or rewriting history does not recall a value that reached a commit.
- **`trade_journal.py` is the SINGLE SOURCE OF TRUTH for the journal's column
  set** (rule 6), including the `venue` column and its self-healing migration.
  Extracted from `ibkr_service` on 2026-08-06 because an FTMO order has to be
  journalled too, and importing the IBKR adapter — and `ib_async` with it — to
  record a trade on a broker it never talks to is the wrong dependency.
  `ibkr_service.journal()` keeps its signature and its Telegram alerting and
  delegates the write, so there is still exactly one writer. Has a
  `python3 trade_journal.py --selftest` offline check and a `--describe`.

- `indicators.py` is the SINGLE SOURCE OF TRUTH for technical math, shared by
  trader_app charts and research_agent prompts (human and AI see identical
  numbers). It has `--selftest`. Never reimplement indicators elsewhere —
  including in any future web dashboard.
- `trading_agent_service.py` — third-party TradingAgents wrapper. NEVER RUN yet;
  daily-granularity only, candidate for one evaluation run vs research_agent.
- `watchlist.py` — the watchlist is stored as NAMED GROUPS
  (`trader_settings.json`'s `watchlist_groups`), with `tickers` DERIVED as the
  deduped union and regenerated on every save. Groups are the source of truth;
  `tickers` stays the contract every consumer reads (paper_trader,
  autotrade_runner, research agent, Kronos, trader_app), so nothing downstream
  changed. Edit via `trader_app.py` menu 9 ONLY — the old raw ticker edit in
  Settings was removed deliberately, because writing `tickers` directly would
  desync it from the groups and be silently reverted on the next group save.
  Groups mirror how the owner organizes IBKR watchlists but are **not synced
  from IBKR**: the TWS API exposes no watchlist endpoint (verified against
  ib_async 2.1.0 — watchlists are a client-side TWS UI feature). Auto-sync
  would require IBKR's separate Client Portal Web API (second gateway, browser
  login, session keepalive) — considered 2026-07-25 and deliberately not built.
  Symbols are validated on entry against yfinance AND against what the order
  path can actually trade (US stocks): foreign listings (`9988.HK`), FX
  (`EUR.USD`), crypto (`BTC-USD`) and futures (`ES=F`) are dropped and
  reported, never silently discarded. Has a `--selftest`-style
  `python3 watchlist.py` offline check.
  `--group <name>` / `--list-groups` work on `run_research_agent_watchlist.py`,
  which is tracked (as are `run_notify.sh`, `daily_digest.py` and
  `TelegramBot/` — the old "untracked external set" note was stale and is
  resolved).
  **Removing a ticker you hold a position in is guarded**, and this is the
  reason why: `paper_trader.py` filters holdings with `if sym in tickers`, so
  a removed symbol's position goes invisible to it — the GTC stop survives but
  nothing will ever manage or exit that position again.

## Empirical findings so far (do not re-litigate without new evidence)

- Out-of-sample 2019-2026, 10 large caps, after costs: SMA 20/50, golden cross,
  Donchian, RSI-2 ALL lost to buy-and-hold. Risk overlays cut drawdown but not
  the performance gap.
- Momentum rotation (top-3 of 10 by 12-mo return, monthly) ≈18.5% CAGR vs SPY 16%,
  max DD -21.7% vs -33.7% — the only strategy family that earned Phase 3.
  Caveat: hand-picked mega-cap universe flatters it; broad-universe test pending.
- ORB (Zarattini/Aziz rules) on recent 60 days of 5m QQQ: -12.6%. Regime-dependent.
- ICT/SMC influencer methods: no verifiable evidence (see knowledge/01).
- Kronos (foundation-model forecaster, `KronosAI/`) walk-forward backtested
  2026-07-23, window bounded by its own pretraining cutoff (paper states
  training data ends June 2024, test period begins July 2024 — so July
  2024→now is the only honest evaluation window, ~24 monthly rebalances):
  Spearman IC 0.036, directional hit rate 50.0% on predicted-vs-realized
  20-day return (304 pooled date×ticker pairs) — no measurable forecasting
  skill detected. Portfolio sim happened to beat SPY (20.99% CAGR / -9.30%
  DD vs SPY 17.92% / -18.76%) but that's noise from a 24-decision sample
  given the flat IC, not edge — and it lost badly to momentum rotation
  (59.07% CAGR / -15.60% DD) on the identical dates/costs. Single seed
  (42)/sample_count=10 draw. See `KronosAI/kronos_backtest.py` and
  KronosAI/KronosVault's Integration Log for full methodology.
- Hourly-cadence IC screen (`KronosAI/kronos_ic_hourly.py`, 2026-07-24, run
  before building `autotrade_runner.py`): momentum-style ranking (trailing
  400-bar return) IC -0.037 / 48.5% hit rate; Kronos (same LOOKBACK/PRED_LEN
  bar counts, hourly data) IC -0.081 / 46.4% hit rate. 336 pooled pairs, both
  indistinguishable from noise — no edge at hourly cadence either. Built and
  running anyway per explicit owner request (see rule 7) — a deliberate live
  paper experiment, not a validated strategy.

- **Per-asset-class IC screens — the rule 9 gate**
  (`KronosAI/kronos_ic_assetclass.py`, 2026-08-03). Daily bars, same
  LOOKBACK=400 / PRED_LEN=20 bar counts, same 2024-07-01 pretraining cutoff and
  same matched momentum baseline as the daily stock test, so these sit on the
  same scale as the existing stock evidence.

  | class | Kronos date-wise IC | t | hit | momentum IC | verdict |
  |---|---|---|---|---|---|
  | stocks | +0.036 (pooled, 2026-07-23) | — | 50.0% | — | no skill |
  | indices | +0.068 | +0.89 | 39.6% | -0.103 | **FAILED** |
  | FX (CME futures) | -0.138 | -1.55 | 48.4% | -0.002 | **FAILED** |
  | commodities | -0.053 | -0.63 | 49.6% | +0.070 | **FAILED** |
  | crypto | +0.103 | +1.34 | 50.4% | -0.013 | **FAILED** |

  **ALL FOUR CLASSES ARE SCREENED AND ALL FOUR FAILED. Nothing may be enabled;
  the FTMO path is cleared to trade nothing at all.** No |t| exceeded 1.55 in
  either direction — this is not "close", it is four independent nulls. The
  matched momentum baseline failed all four as well, so this is not Kronos
  losing to a better alternative: nothing works on any of these classes at this
  cadence. Combined with stocks (IC 0.036 daily, -0.081 hourly), Kronos now has
  **no demonstrated edge on any asset class this project has ever measured.**

  That is the rule 9 gate doing its job, before an order was placed or the
  venue was even reachable. Do not read a failed screen as "needs a better
  configuration" — re-running it with different tickers or sample counts until
  one passes is exactly the parameter-tuning rule 4 forbids. A class gets
  re-screened when there is a NEW reason to (different cadence, different data
  source, a fixed defect in the input), and the re-run is recorded either way.

  **Judge a class on the DATE-WISE IC and its t-stat, never on pooled IC.**
  Pooled date x ticker pairs share a market move, so pooled n wildly overstates
  independence. This is not theoretical: indices pooled at **+0.181**, which
  reported the way this project reported IC before would have read as the first
  positive signal it ever found — the date-wise t is +0.89, i.e. noise. FX is
  starker still: pooled **+0.042** vs date-wise **-0.138**, disagreeing in
  SIGN. If the two disagree, believe the date series.

  **FX is screened on CME futures (6E/6B/6J/6A/6C/6S/6N/6M), not spot pairs.**
  yfinance reports volume identically ZERO for all ten spot FX pairs (verified
  2026-08-03) and Kronos conditions on volume, so a spot screen would score the
  model on a dead input and return a confident artifact. Any class whose volume
  is dead is flagged UNRELIABLE by the screen and must not be read as a
  negative result — it is an unscreened class.

## Open hypotheses (NOT findings — do not cite these as evidence)

Kept separate from Empirical Findings on purpose: these are single
observations that have not met this project's evidence bar. Promote one only
after testing it properly.

- **Kronos may carry a systematic LONG bias on indices.** The 2026-08-03
  indices screen returned a positive date-wise IC (+0.068) alongside a
  directional hit rate of **39.6%** — below a coin flip. Those are not
  contradictory: IC scores cross-sectional RANKING, hit rate scores SIGN. Being
  mildly right about the ordering while being wrong about direction 60% of the
  time is what a systematic directional bias looks like — plausibly forecasting
  positive returns across a period that fell. **One screen, 24 dates, one asset
  class — nowhere near enough.** Proper test: check whether mean predicted
  return is positive while mean realized is negative, per class, and whether
  the same gap shows up on the classes that failed differently. If it holds it
  matters beyond the screen, because a rotation strategy ranks rather than
  predicts sign, so a biased-but-ordered forecast would be less useless than a
  39.6% hit rate suggests.

- **Kronos may be an expensive momentum proxy.** Its 2026-07-27 daily forecast
  ranking correlated **Spearman 0.916** (Pearson 0.825) with the hourly
  momentum ranking from `autotrade_runner.log` two days earlier — bottom six
  (KO, JPM, XOM, AAPL, JNJ, ASML) in *identical* order, 2 of 3 top names
  shared. If it holds, Kronos costs ~81s of GPU inference to land where a
  trailing-return sort already is, and it scored *worse* than that sort on the
  hourly IC screen (-0.081 vs -0.037). **One snapshot, n=14, two different
  cadences two days apart — that is nowhere near enough.** Proper test:
  compute the rank correlation across the ~24 rebalance dates
  `kronos_backtest.py` already covers.
- **Kronos per-ticker output is noisier than `sample_count=10` suggests, and
  the noise DOES reach top-N membership.** Three consecutive runs on identical
  data put GOOGL at +2.69% / -3.72% / +4.38% — an 8-point spread.
  **Correction 2026-07-28: the earlier claim here that "top-3 was stable, so
  top-N rotation is unaffected" is wrong.** Two `paper_trader.py --dry-run`
  runs ~30 minutes apart, same closed-market data, same `sample_count`,
  produced different top-3s:
  `[AMZN, MSFT, GOOGL]` then `[AMZN, MSFT, DIS]`. GOOGL and DIS are separated
  by ~1 point of predicted return and simply swapped ranks 3/4 (GOOGL +1.71 →
  +0.89, DIS +1.59 → +2.26). 6 of 14 tickers changed rank between the two runs.
  The consequence is not cosmetic: run 1 proposed BUY MSFT + BUY GOOGL (~$50k)
  and SELL DIS; run 2 proposed BUY MSFT only and HOLD DIS. **Which trades get
  placed depends on which sampling draw you happened to run.** Top-N is only
  stable when the N/N+1 boundary gap is wide relative to the sampling spread,
  and near a cluster it is a coin flip. Before approving a Kronos rebalance,
  check the gap between rank N and N+1; if it's ~1 point, re-run and see
  whether the same names come back. Proper fix would be averaging more
  samples, or requiring a margin before rotating.
  **Mitigated 2026-08-02 by the margin route** (`paper_trader.py`):
  `apply_rotation_margin()` gives an incumbent holding hysteresis — it keeps
  its slot unless a challenger beats it by more than `rotation_margin_pct`
  (default **1.0** point, calibrated to the observed spread above, not to
  theory; 0 restores strict ranking). Applied inside `execute_rebalance` —
  the only place that knows what is held — so the human, autotrade and
  browser paths cannot diverge. `rank_boundary_gap()` now prints the rank
  N/N+1 gap with every proposal, so "check the gap before approving" is
  on screen rather than a thing to remember. Both are pure functions with
  offline coverage in `paper_trader.py --selftest`, which replays the actual
  2026-07-28 pair of runs and asserts they collapse to the same decision.
  **This suppresses churn; it does not create edge.** The IC is still ~0 —
  the margin only stops us paying spread to act on noise, and a genuinely
  beaten incumbent is still dropped. First live check 2026-08-02: gap was
  1.62 pt (wider than the margin), so the proposal was unchanged.
  **Confirmed again on the FTMO universe, 2026-08-06.** Two runs ~10 minutes
  apart on the same 14 symbols moved individual predictions materially —
  SOLUSD +17.64% → +25.15%, LTCUSD +16.12% → +12.05%, NATGAS +24.94% →
  +25.71% — and reordered ranks 2/3. The selected top-4 SET was identical
  both times, but only because the rank-4/5 gap was 8.4 then 11.9 points,
  far wider than the movement. That is the documented behaviour holding on a
  new asset universe: **top-N is stable when the boundary gap is wide
  relative to the sampling spread, and a coin flip when it is not.** Read the
  gap, not the ranking.

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
  **GOOGL closed 2026-07-23 and NOTHING recorded it — found 2026-07-25.**
  Its GTC stop (326.06) gapped through: 07-23 opened 321.13, so the fill was
  at the open, not the stop. Est. -$422 on 14 shares, ~$69 of it pure gap
  slippage. No journal row, no reflection, no alert. Root cause and fix are in
  the close-detection section below; the row was backfilled as
  `CLOSE_RECONSTRUCTED`. It also exposed that nothing journaled autonomous
  stop fills at all — `paper_trader.py` only journals exits it places itself.
  **The `max_daily_loss_usd: 300` breaker did not fire on this $422 loss, and
  journaling closes does NOT fix that** — `daily_realized_pnl()` reads IBKR's
  own `RealizedPnL` account value, not the journal, and `check_order()` only
  consults it when an order is being placed. Nothing tried to place one on
  07-23, so the breaker was simply never evaluated. It is a pre-trade gate,
  not a monitor: it cannot stop a loss that arrives from a stop firing on its
  own, only refuse the NEXT order after one. Worth knowing before trusting it
  as a safety net under unattended autotrade.
  **Half-fixed 2026-08-02, and be precise about which half.** ENFORCEMENT is
  unchanged — it is still a pre-trade gate and still cannot stop a loss that
  arrives on its own. What changed is VISIBILITY: the trip condition was
  extracted into `ibkr_service.daily_loss_breaker_status()` (pure, selftested)
  and `reflect_on_trades.py` now evaluates it on every 30-min cycle,
  journalling `BREAKER_TRIPPED` and texting once per day when the limit is
  already breached. So a GOOGL-style overnight stop-out surfaces within half
  an hour instead of at the next order attempt. It flattens nothing and
  disables nothing — auto-remediation is deliberately a separate decision,
  same reasoning as `verify_stop_protection()` not placing a replacement stop.
  A `None` RealizedPnL reports UNKNOWN, never "safe".
  Remaining open positions verified 2026-07-25: AAPL 15 @ 328.04 and JNJ 19 @
  249.98, both with live **GTC** stops covering the full quantity (309.10 /
  237.61), and no orphaned GOOGL stop left behind. **Re-verified directly
  against IBKR 2026-07-27** — still open, still `tif=GTC`, `PreSubmitted`.
  **The 2026-07-27 Kronos rebalance HALF-EXECUTED and the journal recorded it
  as a total failure**, for a full day. Approved AMZN/MSFT/DIS in, AAPL/JNJ
  out. The journal said: exits blocked, all entries `Cancelled`, account
  unchanged. Verified read-only against IBKR on 2026-07-28, what actually
  happened: both exits WERE blocked by the notional cap applying to closes
  (true, fixed same day — rule 3), but **AMZN 21 @ 232.73 and DIS 52 @ 95.39
  FILLED** with full-quantity GTC stops (217.74 / 90.83). Only MSFT did not.
  The account went from 2 positions to **4** while every record said it was
  unchanged. Both root causes — the wrong 10349 diagnosis and the one-second
  status snapshot — have their own entries under Known environment gotchas.
  Corrections appended as `RESULT_CORRECTED` / `NOTE` rows, originals left in
  place and annotated.
- Phase 4 (tiny real capital): locked until months of Phase 3 evidence.

## Close detection is two-tier (`reflect_on_trades.py`)

**Do not "simplify" this back to one tier, and do not replace tier 2's
`fetch_positions_confirmed()` with a bare `ib.positions()`.** Both
prohibitions exist because the simpler version provably lost events in
production — a silent GOOGL close that went unrecorded for two days, and a
phantom full liquidation journalled on a Saturday against two positions that
were open the whole time. The mechanism, both incidents and the reasoning are
in the **`ibkr` skill**; the module docstring in `reflect_on_trades.py` carries
the implementation detail.

**Detection time is not event time** for tier 2 — a weekend close is journalled
Monday and the row says so. Tier 2 journals and texts but writes NO reflection
(no realized P&L to build the prompt from), so the research feedback loop has a
hole exactly where the unattended closes are.

## Work queue for Claude Code (in order — finish the job)

1. ~~TWS smoke test~~ — DONE 2026-07-21 (Gateway paper port 4002, account
   DUQ903866). The two `ib_async` connect warnings ("open orders/completed
   orders request timed out") are benign on a fresh account — but see the
   close-detection section before generalising "that warning is harmless".
2. ~~Phase 3 paper-trading loop~~ (`paper_trader.py`) — DONE 2026-07-21, run
   for real, now retired-in-place per rule 9. Signal is Kronos top-N of the
   watchlist (rule 8; older journal rows and reflections say `momentum`
   because it was, until 2026-07-28). Sizing, exit ordering and the
   `opening:` flag are documented in the module docstring — read that rather
   than a copy here. The design points that are NOT in the code: exits run
   before entries so `max_open_positions` headroom is freed first, and sizing
   clamps to the notional cap using the **buffered** entry price, not the raw
   market price (a real bug, hit and fixed during the first live run).
3. **Research agent — ongoing, in progress.** Re-run 2026-07-25 on the full
   14-ticker watchlist (grown from the original 12 via `AVGO`/`ASML`) — fresh
   notes for all 14 in `research_log/`. Re-run `grade_calls.py --csv`
   2026-07-28: **still 0 graded, 76 pending — verified genuinely pending, not
   a fetch bug** (checked the underlying yfinance data directly rather than
   trusting the "pending" label). `forward_return()` needs `days + 1` bars, so
   the 5d horizon needs **6** sessions from the note date, not 5.
   **The project has ZERO real graded calls. It never had any.** The 4 grades
   that `graded_calls.csv` carried until 2026-07-28 came from two SYNTHETIC
   TEST NOTES (`AAPL_2026-05-15`, `MSFT_2026-06-01`, each literally headed
   "SYNTHETIC TEST NOTE — not a real call") that were deleted in `bdee3c8`
   when real runs started. The CSV kept their grades, and `daily_digest.py`
   read that file and cheerfully reported "4 graded, 0 pending" every morning
   for days — fabricated calibration evidence, presented as a track record,
   in the one file that exists to gate autonomy. The `--csv` re-run overwrote
   it. Don't reintroduce synthetic notes into `research_log/`; if you need
   them for testing, write them to a temp dir.
   Note also **2026-07-24 (Friday) has no bar in yfinance for any ticker**
   (verified across SPY/MSFT/AAPL) — trading-day math over this window is off
   by one if you assume a normal week.
   **FIRST REAL GRADES LANDED 2026-08-03: 38 graded at 5d, and the answer is
   no detectable skill.** 26% correct against a chance base rate of **39%** —
   an edge of -13pt at exact binomial **p=0.13**, i.e. indistinguishable from
   guessing in EITHER direction. By direction: long 33% (n=6, chance 35%),
   no-edge 29% (n=28, chance 42%), short 0% (n=4, chance 25%).
   **Read the sample honestly before concluding anything: all 38 calls share
   one market week, so they are heavily correlated — this is much closer to
   ONE observation than to 38.** Not evidence the agent is bad; evidence that
   we still have almost no evidence. 38 calls remain pending at 21d.
   Calibration points the healthy way (low 1-4: 22%, mid 5-6: 40%, nothing
   ever rated above 6/10), on n=27/10 — too small to bank.
   **A win rate without its null is not evidence.** `grade_calls.py` now
   prints the chance base rate, the edge over it, and a binomial p-value on
   every line. Never quote a win rate from this project without the number it
   is being compared against.
   - Re-run `python3 run_research_agent_watchlist.py` weekly. Every note in
     `research_log/` is dated 2026-07-25, so this is overdue.
     `--group <name>` / `--list-groups` also work if only part of the
     watchlist needs a refresh — see the Watchlist section below.
   - Re-run `python3 grade_calls.py --csv` and actually read the calibration
     report — don't just run it, look at it. Treat any report claiming grades
     from notes not in `research_log/` as corrupt.
   - **Grades are struck once and cached in `grading_cache.json` (tracked).**
     `forward_return()` re-downloaded from yfinance every run and yfinance
     returns slightly different bars run to run — three consecutive runs on
     identical notes scored the same book 37% / 34% / 37%. A file the autonomy
     bar is read from cannot change when you re-read it. `--refresh`
     re-strikes deliberately. This buys reproducibility, NOT accuracy.
   - **CORRECTION 2026-08-03 — the old note here said a mostly-no-edge book is
     "cheap to be right about" under the ±2% flat band. That was exactly
     backwards, and it mattered.** Measured from 2y of price history alone
     (independent of any grade): a no-edge call landed inside ±2% only ~42% of
     the time BY CHANCE at 5d and ~21% at 21d. One fixed band across horizons
     differing 4x in length would have printed ~21% on the pending 21d book
     and read as catastrophic failure while measuring nothing. And 5d sigma
     across the watchlist runs 2.4% (KO) to 9.2% (PLTR), so the same call at
     the same confidence was graded on which ticker it was handed. The band is
     now **0.5x that ticker's realized sigma at that horizon**, measured
     strictly before the note date; the legacy fixed-band grade is printed
     alongside so the change stays auditable. Changing a metric after seeing a
     bad result is the shape of what rule 4 forbids — it was allowed here only
     because the flaw is provable from price history without reference to any
     grade. Hold any future metric change to that same test.
   - Book shape (unchanged): **74% no-edge** (28/38), 16% long, 11% short,
     confidence clustered low (18 calls at 3/10, none above 6/10).
4. **IBKR paper trading — RETIRED IN PLACE 2026-08-02 (rule 9), monitoring
   only.** No new orders on this venue. Three positions remain open and are
   still managed: **JNJ 19 @ 249.98, DIS 52 @ 95.39, AMZN 21 @ 232.73**, all
   verified read-only on 2026-08-02 as protected by full-quantity `tif=GTC`
   stops in `PreSubmitted` (217.74 / 90.83 / 237.61). `reflect_on_trades.py`
   and its launchd job keep running until these close naturally.
   **Every position check must verify stops are GTC, not merely present** — a
   DAY stop looks fine for hours and then silently vanishes at the session
   close. History (the GOOGL and AAPL closes, the half-executed rebalance, the
   Gateway wedges) and the operational runbook are in the **`ibkr` skill**.
5. **Web UI — BUILT 2026-08-01.** `api/` (FastAPI) + `web/` (Next.js 16,
   shadcn/Base UI, lightweight-charts). Start with `./run_web.sh`, open
   http://localhost:3000. **Local only — never deploy it and never bind
   0.0.0.0:** it holds a live Gateway connection and can place orders, so
   there is no auth layer because there is no network exposure. Full
   rationale in `web/README.md` (architecture, screen list, write-action
   mechanics); UI-specific rules in `web/CLAUDE.md`.
   - **The backend is a thin wrapper on purpose.** Order placement, sizing,
     RiskGuard, journalling and indicator math stay in `ibkr_service.py` /
     `paper_trader.py` / `indicators.py`. The browser path and the terminal
     path cannot diverge in risk handling — same reasoning as sharing
     `execute_rebalance` between the human and autotrade paths.
   - **Order placement runs on its own thread** (`api/trader_worker.py`,
     clientId 16) because ibkr_service's order functions are synchronous and
     `ib.sleep()` → `IB.run()` → `run_until_complete()` cannot run inside the
     server's event loop. The read hub is clientId 15 (rotating 17-20 if
     Gateway still holds one — see the gotcha below).
   - Every write is preview → execute(token); the execute reads its
     parameters from the stored preview, so the UI cannot show one order and
     send another. Entries are bracket-only.
   - Still true, and still worth weighing: more research/trading cycles is
     the evidence this project is gated on; a dashboard is not.
   - The UI is IBKR-only and stays that way for now — FTMO has no browser
     surface yet. Deciding whether it gets one is a later call, not an
     assumed requirement.
6. **FTMO venue — IN PROGRESS. CONNECTED 2026-08-05.** New trading venue per
   rule 9. Five modules, selftested offline (**294 checks**, no credentials
   needed): `ftmo_rules.py`, `ftmo_monitor.py`, `ftmo_sizing.py`,
   `ftmo_audit.py`, `ftmo_service.py`. **Read the `ftmo` skill** for the agreed
   configuration, derived limits and invariants.
   **The cTrader app went Active and the venue is now reachable end to end.**
   OAuth completed, tokens in `.env` (access token ~30 days — `--refresh`
   before it lapses). Verified read-only by `--probe` on 2026-08-05:

   ```
   ctidTraderAccountId 48137229   login 17166058   broker ftmo
   balance 25,000.00 USD   leverage 100x   HEDGED   FULL_ACCESS
   0 open positions   0 pending orders   202 tradeable symbols
   ```

   **The account is live-TYPE and that is not a rule 1 problem.** cTrader
   routes by endpoint, so `CTRADER_HOST=live` is required; FTMO issues
   Challenge and Free Trial accounts on its LIVE cTrader server with SIMULATED
   capital. `isLive` is a routing flag, not a statement about money — the
   $25,000 balance with zero positions is the agreed Free Trial. See the
   `CANT_ROUTE_REQUEST` gotcha before "fixing" the host back to demo.
   Next actions, in order:
   - ~~`--authorize` then `--probe`~~ — DONE 2026-08-05, both pass.
   - ~~Bind real `SymbolSpec` values~~ — DONE 2026-08-05.
     `python3 ftmo_service.py --symbols` captures all 202 real specs to the
     TRACKED `ftmo_symbol_specs.json`, and `ftmo_sizing.spec_from_capture()`
     reads them. **Every invented spec had been wrong, some by 1000x**
     (EURUSD min/step 100 → 100,000; XAUUSD 1 → 100; US30.cash 10 → 1 and
     digits 1 → 2), so the sizer's risk maths had been validated against
     fiction. The sizer now sweeps all 202 real symbols asserting no accepted
     size ever exceeds the budget. Re-capture only when the venue's universe
     changes; the file is tracked so the selftest stays offline.
   - ~~Confirm server-side stops attach as assumed~~ — **DONE 2026-08-05,
     PASSED.** One real minimum-size trade on the live trial (BTCUSD, position
     9822997, 0.01 units, risk $29.72): placed, read back with
     `stopLoss 61,784.01 protected: True`, closed, account flat again.
     `ftmo_smoke_order.py --confirm` reruns it. The whole FTMO risk model
     rested on this and it is no longer an assumption.
   - ~~Migrate `trade_journal.csv` to add the `venue` column~~ — DONE
     2026-08-06, in the writer (`trade_journal.py`) rather than as a one-shot
     script, so a half-applied migration cannot silently misalign the audit
     trail. See the gotcha for the mechanism.
   - **IC screens: DONE for indices and FX, both FAILED (2026-08-03).** See the
     screen table under Empirical findings. Neither class may be enabled — and
     note this is now a measured refusal, not a missing-evidence one, so
     "we never screened it" is no longer an argument for any of them. Stock
     CFDs inherit the existing evidence, which is IC ~0. **ALL FOUR CLASSES
     FAILED, so there is nothing the FTMO path is cleared to trade** — that is
     the gate working, not a blocker to route around. The signal→order path
     below can still be BUILT, but it has no class it may fire on, and that
     gap must be closed with evidence rather than with a config change.
     Re-run with `./run_notify.sh KronosAI/kronos_ic_assetclass.py`.
   - ~~Build the signal→order path, then an integration pass driving all five
     modules together~~ — DONE 2026-08-06. `ftmo_runner.py` drives the whole
     spine and was verified end to end against the live venue in dry-run:
     14/14 symbols passed the bar/quote scaling cross-check, Kronos forecast in
     ~90s, the rule engine returned OPEN OK, and the sizer produced four
     entries totalling **$994.71** of risk — inside the $1,000 daily soft
     limit, which is the portfolio cap working. **Nothing was placed.**
     Still true that integration is where the real bugs surface: the 1000x
     trendbar scaling bug came from a run like this, not from unit tests.
   - **ARMED 2026-08-06.** `ftmo.autotrade.enabled` is true and
     `com.tradingbotapp.ftmo` is loaded, firing 01:15 daily. The live journal
     was migrated to 12 columns the same day (46 rows, lossless, verified).
     **Nothing in this project has ever placed an order unattended before**, so
     the first firings want watching: `ftmo_launchd.log`, the Telegram
     messages, and the `venue=ftmo` rows in `trade_journal.csv`. Confirm the
     stops attached by reading them back from the venue.

## Autotrade (experimental, unattended) — `autotrade_runner.py`

Unattended hourly rebalancing, off by default. Rule 7 above governs it.
Operational detail (toggle, schedule, signal, execution, notifications,
how to disable) lives in the **`autotrade` skill** — read it before touching
`autotrade_runner.py` or `autotrade_signals.py`.

## FTMO autotrade (unattended) — `ftmo_runner.py`

Kronos deciding and executing on FTMO with no approval step. Rule 9 governs it,
and it is the **third deliberate exception to rule 5** — flag it that way.

**Armed by its own toggle, deliberately separate from IBKR's.**
`trader_settings.json` → `ftmo.autotrade.enabled`, or the arm/disarm control on
the `/ftmo` screen. A missing key reads as OFF, and IBKR's `autotrade.enabled`
CANNOT arm it — there is a selftest asserting exactly that. The two venues have
different brokers and different limit models; one switch covering both would
mean you could not reason about FTMO without also reasoning about a retired
venue. The FTMO switch is also **not gated on IB Gateway's health**, unlike the
header kill switch, because a dead Gateway has nothing to do with FTMO and a
switch you cannot reach when things are going wrong is not a switch.

The cycle, once per invocation: connect → positions and balance → rule engine →
FTMO's own daily bars → Kronos → rank → plan → **exits, then entries** → verify
every stop by reading the venue back → journal + audit + text.

Four properties not to regress:

1. **FLATTEN is decided before any forecast runs**, and `flatten_all()` has no
   rule engine, sizer or limit in front of it. Rule 3: a limit caps NEW
   exposure, and blocking an exit raises risk. Each close is attempted
   independently so one failure cannot strand the rest.
2. **An equity it cannot fully price is not an equity it trades on.** Any
   position without a quote blocks new entries rather than being marked flat.
3. **Stops are verified by reading the venue back**, never from the fact that
   an order was sent. A rejected cTrader order arrives as an event, not an
   error response — the first live FTMO order was refused while the code
   reported `{'sent': True}`.
4. **torch is imported only after the enabled check**, so a disarmed firing is
   a cheap settings read rather than a 2 GB model load.

**Every entry carries a TAKE-PROFIT as well as a stop, and the target is
Kronos's own predicted return** (owner decision, 2026-08-08). TP = entry x
(1 + predicted_return_pct/100), computed by
`ftmo_sizing.take_profit_from_prediction()`. Chosen over an R multiple
deliberately: the target is the strategy's own thesis, so a position exits when
the forecast is realised rather than at a level picked independently of it.

`relativeTakeProfit` rides the SAME request, the SAME 1e5 wire scale and the
SAME precision grid as `relativeStopLoss` — `quantize_relative_take_profit()`
delegates to the stop's quantiser rather than reimplementing it, so the
2026-08-07 rejection cannot recur on one of them but not the other. The target
is atomic with the entry for the same reason the stop is.

**A candidate whose forecast points the WRONG way is now DROPPED, not traded,
and that is a live behaviour change — flag it, don't discover it.** A negative
prediction has no take-profit on the profitable side: `entry x (1 - 0.0015)`
sits BELOW entry on a long, which the venue would read as an immediate exit at
a loss. `plan_orders` therefore skips the entry and records why. This is not
hypothetical — it would have blocked the 2026-08-07 21:32 EURUSD entry
(predicted **-0.15%**), the same trade the inverted rotation margin caused.
Before 2026-08-08 that candidate produced an order.

One consequence to keep in view:

- **The target inherits the forecast's noise.** CLAUDE.md already records the
  same symbol re-forecast ten minutes later moving +17.64% -> +25.15%. The stop
  is ATR-derived and stable; the target is not, so two runs can size a position
  identically and target it differently. That is a property of the chosen rule,
  not a defect. The TP is fixed at fill, so it does not drift mid-position.

## FTMO close detection — `ftmo_closes.py`

**Closed 2026-08-08.** Until then the FTMO runner journalled only the exits it
PLACED, so a stop or take-profit firing between firings left no journal row, no
alert and no reflection — rule 6 broken on the venue that trades unattended.
Adding a take-profit the same day made it worse, because a target fires on
exactly the outcomes most worth recording.

Tier 1 is the live `ProtoOAExecutionEvent` stream (nearly free, catches almost
nothing — the runner's session lives ~2 minutes an hour). **Tier 2 does the
work**: `ftmo_runner_state.json` now carries `open_positions`, and each run
diffs what it remembers against what the venue reports. Anything remembered but
absent closed on its own.

Better positioned than its IBKR sibling in one specific way: cTrader returns
the actual closing DEAL via `ProtoOADealListByPositionIdReq`, so a detected
close carries the venue's own price, gross profit, swap and commission.
`reflect_on_trades.py` tier 2 writes no reflection because it has no realized
P&L to build one from; here there is one.

Five properties not to regress:

1. **A read that FAILED is not an account that is flat.** Every "vanished"
   conclusion needs a successful read — `reconcile()` propagates the exception
   rather than returning an empty result. And a diff that would close
   EVERYTHING is re-read before anything is written, because that is the shape
   of the 2026-07-25 phantom liquidation. One position vanishing is ordinary
   and is not double-checked.
2. **Journal FIRST, then forget.** The state file is advanced only after the
   row is written. A crash in between costs a duplicate row on the next run —
   visible and fixable. The other order loses the event permanently, and rule 6
   says a fill not in the journal did not happen.
3. **Detection time is not event time.** The row is stamped with the DEAL's
   `executionTimestamp`; when we noticed goes in the detail. The runner fires
   only in-window, so a Sunday close really is discovered on Monday.
4. **A close we cannot price is recorded as a close with an UNKNOWN price**,
   never at zero and never at the entry price. Status stays exactly `closed`
   so `api/journal_api.py`'s `FILLED_STATUSES` still matches it; `_num()`
   already reads `UNKNOWN` as None.
5. **`classify_close()` compares SIDES, not distances.** A stop that gaps
   through fills BEYOND its level — GOOGL's 326.06 stop filled at the 321.13
   open, 1.5% away — so nearest-level matching calls the single most important
   case "neither". A long closing at or below its stop is a stop-out at any
   distance. The percentage tolerance applies only BETWEEN the two levels.
   The classification is a GUESS and is labelled one everywhere: cTrader tells
   you the price, not the intent.

`python3 ftmo_runner.py --reconcile` runs detection alone — no orders, no
torch, and it **deliberately ignores both the arm toggle and the trading
window**, because recording what the account did is not trading. Same reasoning
as rule 9 keeping IBKR monitoring after retirement. **It has no launchd job
yet**, so between runner firings nothing is watching; wiring one is the
remaining step.

`amend_stop()` takes the SL/TP pair, so amending a stop while omitting the
target would silently CLEAR it. It now reads the existing target back and
re-sends it. Nothing calls it yet — it was written that way now because the
failure would be invisible: the amend succeeds, the stop is right, the target
is just gone.

**`ftmo_runner_state.json` is why the daily limit works at all.** The FTMO
daily limit is measured against the balance at 00:00 CE(S)T and the 1-Step
trailing floor moves off a completed day's CLOSING balance; a one-shot script
cannot know either without remembering. Without this file the limit would
evaluate against the current balance every run — a daily loss of 0.00 forever,
i.e. a limit that can never trip. It is gitignored: it belongs to THIS
machine's account, and a checkout elsewhere inheriting a day-start balance from
an account it is not connected to is exactly how the limit would evaluate
against a fiction. The first run seeds day-start from the **live balance**, not
from `initial_capital`, which would invent a loss on an account that had
already traded and could block trading on the spot.

**ARMED AND SCHEDULED since 2026-08-06.** `ftmo.autotrade.enabled` is `true`
and `com.tradingbotapp.ftmo` is loaded (`ftmo_launchd.log`).

**The schedule and the trading window are two different things — do not
conflate them.** launchd wakes the runner **hourly at :30, all 24 hours, every
day**. That is a deliberate SUPERSET. The actual window is **16:30 to 11:30 the
next morning, every day except Sunday, Europe/Sofia** (owner decision
2026-08-06, revised from a single 01:15 firing), and it is enforced by
`ftmo_runner.within_trading_window()`, which is authoritative.

The rule is not encoded in the plist because it wraps midnight and excludes one
weekday — 6 weekdays x 20 hours = 120 `StartCalendarInterval` entries nobody
will re-read correctly — and because a plist expresses whatever the host's
local timezone happens to be, while the runner resolves `Europe/Sofia` through
`zoneinfo` and stays right across the EEST/EET switch. `autotrade_runner.py`
splits NYSE hours the same way and for the same reason.

Two properties that make this safe at ~20 firings a day:

- **The window is checked before the audit log opens and long before torch is
  imported**, so an out-of-window wakeup costs one settings read. Selftested.
- **The window WRAPS midnight, so it is a union, not a range.** `OPEN <= t <=
  CLOSE` would be empty for every `t` — the obvious way to get this silently
  wrong. "Except Sunday" applies to the CALENDAR day in Sofia, so Saturday's
  evening leg runs and Sunday's morning leg does not.

Firing hourly on a 20-day forecast is against the cadence this project
documented, and it was chosen anyway with that stated: the rotation margin
suppresses churn between runs, but ~20 re-decisions a day is ~30 min of GPU
and more chances to pay spread on sampling noise. The daily-rebalance rationale
has not been disproved — it was overridden.

01:00 Sofia is the Europe/Prague day boundary, so the **01:30 firing is the
first of each new FTMO day** and `advance_state()` rolls there, off settled
numbers.

To disarm: the control on `/ftmo`, or set `ftmo.autotrade.enabled` false. To
stop the schedule:
`launchctl unload ~/Library/LaunchAgents/com.tradingbotapp.ftmo.plist`.

**`trader_settings.json` carries the armed flag and is tracked in git.** If it
is left modified-but-uncommitted, a stray `git checkout .` silently DISARMS the
runner. That direction fails safe, but it fails *quietly* — check the flag
before concluding the bot is running.

Preview without arming anything: the "Preview plan" button on `/ftmo`, or
`python3 ftmo_runner.py --force --dry-run`. Both run the identical pipeline and
place nothing.

## Phone notifications (TelegramBot/) — use this for anything long-running

Default to running backtests and other long one-shot scripts through
`./run_notify.sh <script> [args]` rather than calling them directly.
Scripts that already notify from inside themselves must **NOT** be wrapped
(`reflect_on_trades.py`, `run_research_agent_watchlist.sh`, `daily_digest.py`,
`paper_trader.py`, `autotrade_runner.py`, `ibkr_service.py`'s `journal()`) —
wrapping them double-notifies or spams a no-op poller. Full detail, including
what each of those texts on and how to wire up a new one, is in the
**`notify-on-long-runs` skill**.

Note both digests quote `CLAUDE.md`'s Work Queue and Empirical Findings
sections close to verbatim, so keep those reasonably current.

## Known environment gotchas

- **Never size orders off a live FX quote.** `paper_trader.get_net_liquidation_usd`
  converts the EUR-denominated account to USD using IBKR's own `ExchangeRate`
  account value, NOT `market_price(forex_pair("EURUSD"))`. The old way needed a
  market-data line and died with error 10197 "No market data during competing
  live session" (hit 2026-07-25 — it took down `--dry-run` and would have taken
  down every hourly autotrade firing). `ExchangeRate` for currency C is the
  value of 1 C in BASE, so USD = BASE / rate_usd. That direction is verified at
  runtime against an independent yfinance `{BASE}USD=X` quote and RAISES on
  mismatch — inverting it misstates equity by ~29% (1.137 vs 0.879) and would
  silently mis-size every order. IBKR's own cash-balance identity was tried as
  the check first and rejected: an inverted rate still reconciled within 0.26%.
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
- **Launch the interactive app with `./trader_app.sh`, never
  `python3 trader_app.py`.** `python3` resolves to `/opt/anaconda3/bin/python3`
  (conda base), which is a PARTIAL match for this project's needs: it has
  pandas, rich, yfinance and ib_async but not torch/einops/safetensors/
  huggingface_hub. So the app launches, loads data and backtests perfectly,
  and only the Kronos menu fails — with `Kronos dependencies not installed:
  No module named 'torch'`, which reads like a missing package but is really
  the wrong interpreter. Hit 2026-07-28. The old error message made it worse
  by advising `pip install torch ...`, which either installs a second ~2 GB
  copy into conda base or, if `.venv` happens to be active, reports
  "Requirement already satisfied" and looks like it did nothing. Fixed three
  ways: `trader_app.sh` pins `.venv/bin/python` (matching every other launcher
  in the project), `trader_app.py` warns at startup when `sys.prefix` isn't
  the project venv, and the Kronos import error now distinguishes "wrong
  interpreter" from "genuinely missing" and prints `sys.executable`. Every
  automated script already pinned `.venv/bin/python`; the interactive app was
  the only entry point without a launcher, which is why it was the one that
  broke.
- **IBKR error 10349 was OUR BUG, not a Gateway preset needing a GUI fix.**
  RESOLVED 2026-07-28 by direct probe against the paper account — do not
  reopen this as a Gateway issue. `place_bracket_order` built the parent
  `LimitOrder` with **no explicit `tif`**. The Order Preset filled in the
  blank with DAY and *announced* it: "Order TIF was set to DAY based on order
  preset". Three things the probe established that the 07-27 guess got wrong:
  1. The error's `reqId` is always the **parent's**, never the stop's.
  2. The **stop leg was never affected** — it always carried an explicit
     `tif="GTC"` and IBKR held it as GTC throughout, confirmed via
     `reqAllOpenOrders` (not the local order object, which proves nothing).
  3. It is a **warning, not a rejection**. Both legs stayed `PreSubmitted`.
     `ib_async` logs a scary `Canceled order: Trade(...status='Cancelled')`
     line that does NOT match IBKR's authoritative view — which is why AMZN
     and DIS filled on 07-27 while the journal claimed everything cancelled.
  Fix: `parent = LimitOrder(action, quantity, entry_limit, tif="DAY")`. Re-probe
  returned LMT `tif=DAY` / STP `tif=GTC`, both PreSubmitted, **no 10349 at
  all**. DAY is correct for the parent — an entry limit priced off today's
  close should expire with the session; it is the STOP that must outlive the
  day. **Never leave a TIF unset on any order**: an unset field is one the
  broker's config gets to fill in, and you will not necessarily be told what
  it chose.
- **A `Cancelled` RESULT row seconds after placement does not mean the order
  died.** Until 2026-07-28 `place_bracket_order` journalled the parent order's
  status after a fixed `ib.sleep(1)` — a snapshot, not an outcome. Two orders
  that filled were recorded `Cancelled` and the account silently ran two
  positions ahead of every record for a day. It now waits for a terminal
  status via `wait_for_status()` and then verifies a covering GTC stop is
  actually live, journalling `UNPROTECTED` + texting if one isn't. When
  auditing the journal, trust `RESULT_CORRECTED` rows over the original
  `RESULT` row for anything before 2026-07-28.
- **Never read an empty `ib.positions()` as "the account is flat."**
  `IB.connect()` fetches positions as a best-effort startup request;
  `connectAsync` gathers it under `asyncio.wait_for(..., timeout=4)` with
  `return_exceptions=True` and, unless `raiseSyncErrors=True`, **swallows a
  timeout** — returning a connected, healthy-looking `IB` with an empty
  position cache. Indistinguishable from a genuinely flat account unless you
  re-request and let the timeout raise. Caused a phantom full-liquidation in
  `reflect_on_trades.py` (see the close-detection section).
- **IB Gateway can stop answering new API connections** while still appearing
  up (the port stays open). Seen 2026-07-27 after a run of connects with
  distinct client_ids — subsequent read-only checks hung indefinitely. Kill
  stray python processes holding connections and restart Gateway.
  **Refined 2026-08-01: "Gateway is dead" and "that clientId is still held"
  look identical and are not the same thing.** After the web API was killed
  mid-request, every reconnect on clientId 15 failed with a bare
  `TimeoutError` for minutes — while `reflect_on_trades.py` connected fine on
  clientId 11 the whole time. A direct probe gave the real message:
  *"Peer closed connection. clientId 15 already in use?"*, and clientIds 16
  and 25 connected instantly. Gateway holds an id for a while after a client
  dies uncleanly, and **retrying the same id can never succeed** until it
  lets go. Before concluding Gateway needs a restart, try a different
  clientId — it costs one command and is usually the whole problem. The web
  API's hub now rotates 15 → 17-20 automatically after repeated failures.
- **shadcn now generates Base UI components, not Radix, and two of its
  differences do NOT fail typechecking.** Menu items fire `onClick`, not
  `onSelect` (an `onSelect` type-checks as a DOM handler and silently never
  runs), and `DropdownMenuLabel` throws at runtime unless wrapped in
  `DropdownMenuGroup`. Both shipped in the web UI and were only found by
  opening the menu. After touching any shadcn component, click it in the
  browser — `tsc` clean means very little here. Full list in `web/CLAUDE.md`.
- **The `venue` column migration is DONE (2026-08-06) and lives in the
  writer.** Kept here because the trap is the reusable part: `journal()` wrote
  the header only when the file did not exist, so extending `JOURNAL_COLUMNS`
  alone would have appended 12-value rows under an 11-column header, and every
  reader is a `csv.DictReader` — which drops the extra value into the `None`
  restkey and reports NO error. Silent corruption, in the one file rule 6
  makes the audit trail.
  `trade_journal.py` now owns the column set and migrates once, in place,
  before appending. It is self-healing rather than a script you must remember
  to run first, for the same reason `secrets_store.resolve()` falls back to the
  legacy paths: both writers are unattended, so a half-applied migration must
  degrade to "still works". It backs up, verifies the read-back field-by-field
  BEFORE replacing the original, refuses a header it does not recognise, and is
  idempotent. Verified against a copy of the real journal: 46 rows identical
  across all 11 original columns, all backfilled `venue=ibkr`, no restkey leak.
  `append()` REQUIRES an explicit venue — an unlabelled row cannot be
  reconciled against either broker later.
- **cTrader `CH_CLIENT_AUTH_FAILURE: OA client is not in active state` is not a
  credential typo.** It means the Open API application itself is still in
  `Submitted` state at openapi.ctrader.com/apps and has not been approved to
  `Active` yet. The Client ID/Secret are shown before activation, so they look
  usable and are not. Diagnosed 2026-08-02 on the first app-auth attempt.
  Everything below that error is already proven when you see it: the TLS
  handshake to `demo.ctraderapi.com:5035` succeeded, the protobuf request
  encoded and sent, and a real `ProtoOAErrorRes` came back — so do NOT go
  hunting for network, TLS or SDK problems. Wait for activation, then re-run
  `python3 ftmo_service.py --probe`. **RESOLVED 2026-08-05** — the app went
  Active and application auth now succeeds. Kept here because the diagnosis
  is the reusable part: an Open API error that fires at app-auth time is about
  the app's state, not your credentials.
- **A REJECTED cTrader ORDER ARRIVES AS AN EVENT, NOT A `Res`.** A refused
  order comes back as `ProtoOAOrderErrorEvent`, so an error check that only
  knows `ProtoOAErrorRes` / `ProtoErrorRes` treats a rejection as success.
  Hit on the first live FTMO order (2026-08-05): the venue refused it and the
  code reported `{'sent': True}`. Only the smoke test's own read-back caught
  it. Same class as the IBKR "`Cancelled` RESULT row seconds after placement"
  incident — never report an outcome you did not verify against the venue.
- **A cTrader MARKET order cannot carry an absolute stop.** The venue says so
  plainly: *"SL/TP in absolute values are allowed only for order types:
  [LIMIT, STOP, STOP_LIMIT]"*. Market orders need `relativeStopLoss`, an
  int64 DISTANCE in 1/100000 price units. The stop is still a field on the
  same request, so it stays atomic with the entry.
  This is the SAFER form and worth keeping deliberately: the stop is applied
  from the ACTUAL FILL, so the risk distance survives slippage. An absolute
  stop would widen real risk by exactly the slippage.
- **`relativeStopLoss` must ALSO land on the symbol's precision grid — a
  multiple of `10**(5 - digits)` — and the whole order dies if it doesn't.**
  `INVALID_REQUEST: Relative stop loss has invalid precision`. The 1e5 wire
  scale is necessary and not sufficient: EURUSD (digits 5) steps by 1, but
  NATGAS.cash (3) steps by 100 and every 2-digit symbol steps by 1,000.
  Found the hard way on 2026-08-07, on the **first four orders this project
  ever placed unattended**. All four were sized correctly and inside the
  budget; three were refused outright (SOLUSD, NATGAS.cash, LTCUSD) because
  an ATR-derived stop carries as many decimals as the arithmetic gives it.
  **ETHUSD was accepted only because its ATR happened to land on two
  decimals** — so a naive reading of that run says "the order path works, 1 of
  4 filled for market reasons", and it is worth being precise that the one
  success was luck rather than evidence.
  Fixed in `ftmo_session.quantize_relative_stop()`, the single choke point
  through which every order's stop passes. It rounds the distance **DOWN**,
  never to nearest: a shorter distance is a TIGHTER stop, which can only make
  the realised loss smaller than the sizer budgeted, whereas rounding up would
  widen real risk past a per-trade cap that was just proven to hold — a limit
  breached by the transport layer. A distance below one tick floors to zero
  and is REFUSED, never rounded up into a stop nobody asked for.
  The general lesson is the one this venue keeps teaching: **a number the
  sizer proved correct can still be unsendable**, and the venue reports that
  as a rejection event rather than a value it silently adjusts.
  **`relativeTakeProfit` rides the identical grid** and fails the identical
  way. Since 2026-08-08 every entry carries one, so this trap now has two
  fields to catch, not one — `quantize_relative_take_profit()` delegates to
  `quantize_relative_stop()` so a fix to either is a fix to both.
- **A streaming quote does NOT mean a tradeable market.** US30.cash and
  BTCUSD both quoted live and both rejected with `MARKET_CLOSED` at 23:55
  Moscow — FTMO's daily ten-minute maintenance window. `trading_mode:
  ENABLED` in the spec does not mean open *now* either. The symbol schedule
  is captured in `ftmo_symbol_specs.json` and read by
  `ftmo_session.market_open_now()`, which returns `None` for UNKNOWN rather
  than `False`, leaving the venue as the authority.
  **The schedule's timezone is the SYMBOL's (`Europe/Moscow` on this broker)
  and is NOT the `Europe/Prague` boundary `ftmo_rules` uses for the FTMO
  day.** Two different timezones in one system — conflating them puts the
  maintenance window hours off.
- **cTrader trendbar prices are scaled by a FIXED 1e5, never by the symbol's
  `digits`.** `digits` is a DISPLAY hint, not the wire scale. EURUSD hides
  this perfectly because its digits IS 5 — and it is the symbol anyone
  smoke-tests first. Everything else breaks by 1000x: XAUUSD priced at
  4,076,760 against a live bid of 4,248, an ATR of 1.49 million on BTC, and a
  NEGATIVE stop price on NATGAS.cash which the sizer then costed as $199 of
  "risk". `size_position` only asks whether the stop DISTANCE fits the
  budget; it has no opinion on whether the price is real, so
  `ftmo_signal.plan_orders` now validates the stop before sizing and
  `ftmo_session.assert_bars_match_quote()` cross-checks bars against the live
  quote and raises on an order-of-magnitude mismatch.
- **cTrader `CANT_ROUTE_REQUEST: Cannot route request` means the ENDPOINT is
  wrong for that account, not that anything is unauthorised.** cTrader routes
  by endpoint: a live-type account authenticates ONLY on
  `live.ctraderapi.com` and a demo-type account ONLY on `demo.ctraderapi.com`.
  Send `ProtoOAAccountAuthReq` to the wrong one and you get four words that
  name neither the account nor the endpoint, arriving immediately AFTER a
  successful application auth and a successful account list — which reads like
  a token-scope problem and sends you to the wrong place entirely. Hit
  2026-08-05 on the first probe after activation.
  **FTMO Challenge and Free Trial accounts are live-TYPE with SIMULATED
  capital**, so `CTRADER_HOST=live` is correct and is NOT a rule 1 breach —
  `isLive` is a cTrader routing flag, not a claim about real money. Do not
  "fix" this by switching back to demo; the account is simply not there.
  `ftmo_service.select_account()` now refuses the mismatch BEFORE sending
  account auth and names the exact `CTRADER_HOST` value to set, with offline
  tests covering both directions.
- **`ProtoOASymbolsListReq` does NOT return anything you can size an order
  with.** It returns `ProtoOALightSymbol` — id, name, category, asset ids, and
  nothing else. Every number the sizer needs (`minVolume`, `stepVolume`,
  `maxVolume`, `digits`, `pipPosition`, `lotSize`) lives on the full
  `ProtoOASymbol`, which only comes back from `ProtoOASymbolByIdReq`
  (`symbolId` is a repeated field, so batch it — 202 individual round trips
  will meet the rate limiter). The old work-queue note saying the order path
  "needs real `SymbolSpec` data from `ProtoOASymbolsListReq`" named the wrong
  request.
- **`brokerName` is on `ProtoOATrader`, not on `ProtoOACtidTraderAccount`.**
  The account-list response carries only `ctidTraderAccountId`, `isLive`,
  `traderLogin` and two timestamps, so a `getattr(a, "brokerName", "?")` there
  printed `broker=?` forever and looked like the venue withholding data.
- **`launchctl` from an agent's shell can report a DIFFERENT domain than the
  owner's login session, and the difference looks exactly like jobs having been
  unloaded.** Hit 2026-08-06 while installing `com.tradingbotapp.ftmo`.
  Symptoms: `launchctl list | grep tradingbot` returned all five jobs early in
  the session and only one or two later; `launchctl load` returned rc=0 and the
  job was visible in that same shell but gone from the next one;
  `launchctl print gui/501/<label>` said "Could not find service" for jobs whose
  logs were provably being written minutes earlier.
  The cause is the command sandbox, not launchd. Re-running the same commands
  with the sandbox disabled showed the real state. **Verify launchd changes
  unsandboxed, with `launchctl print gui/$(id -u)/<label>`, and treat a bare
  `launchctl list` from a sandboxed shell as unreliable.** Do not conclude a
  job is dead from it — cross-check whether its log file is still growing,
  which is the authoritative signal.
  `launchctl bootstrap` on an already-loaded job returns
  `Bootstrap failed: 5: Input/output error`, which means "already there", not
  a failure to install.
- **The Mac sleeping mid-run is the single biggest cause of FTMO runner
  failures, and it wears TWO different error messages.** Diagnosed 2026-08-08
  after 22 consecutive failures. `pmset -b sleep` is **1** — idle system sleep
  after one minute, on battery — so launchd fires the runner inside a
  ~2-second DarkWake, the process opens a socket, and the machine suspends
  underneath it. Proven by lining `pmset -g log` up against
  `ftmo_launchd.log`: at 16:37:36 DarkWake, 16:37:37 "trading window",
  16:37:38 Sleep, and the error at 17:07:59 — thirty minutes of wall clock in
  which the process barely ran.
  The two messages are the SAME event and differ only by which clock wins the
  race on wake. Twisted's timeouts run on the WALL clock
  (`reactor.seconds()` -> `time.time()`), so on wake the SDK's
  `responseTimeoutInSeconds=5` default has long since expired and fires
  instantly. `threading.Event.wait()` effectively counts AWAKE time only —
  this build has `HAVE_PTHREAD_CONDATTR_SETCLOCK=0` and
  `HAVE_SEM_TIMEDWAIT=0`, so CPython takes the condvar path whose deadline is
  re-derived from the monotonic clock, and macOS's monotonic clock does not
  tick during sleep. So: sleep lands BEFORE connect and nothing ever calls
  `_ready.set()` -> **"did not become ready within 45.0s"**; sleep lands AFTER
  connect during auth -> the timeouts fire on wake and you get **"failed to
  start: TimeoutError: (5, 'Deferred')"**. Neither names sleep, which is why
  the second one reads like a new bug.
  **Do not "fix" this in the session code.** The 3-attempt retry in
  `_on_connected` cannot help — on wake it burns attempts 2 and 3 against a
  TCP connection that died during suspend, which is why the error lands 8-15s
  after each wake. Longer timeouts make failures slower, not rarer. This is a
  power-management problem: a laptop on battery with a one-minute idle-sleep
  timer cannot host an unattended process.
  **MITIGATED 2026-08-08: `ftmo_runner.sh` now execs the runner under
  `caffeinate -i`**, which holds a `PreventUserIdleSystemSleep` assertion for
  the lifetime of the child only — the machine stays up for the ~3 minutes a
  firing takes and is free to sleep straight after. No system-wide `pmset`
  setting was changed. NOT `-s`: that only prevents system sleep on AC power
  and this machine runs on battery, so it would silently do nothing.
  It lives in the WRAPPER, not the plist, deliberately: the wrapper is tracked
  in git and re-read on every firing, so changing it needs no `launchctl`
  reload — worth more than tidiness given the sandbox gotcha above. It
  degrades to running bare if `caffeinate` is absent.
  **Verified only at the mechanism level, NOT under the real failure
  condition**, and the reason matters: **Claude Code's own session holds
  `caffeinate -i -t 300` assertions while it works**, so the Mac does not
  sleep during a session at all. That is very likely why the 18:30 firing on
  2026-08-08 succeeded after 22 failures — the machine was being held awake by
  the tooling, not by any fix. **Do not read a firing that succeeded during an
  agent session as evidence this is solved.** The real test is a firing with no
  session running.
  A residual race remains: launchd starts the job inside a ~2-second DarkWake,
  so if sleep lands in the ~1s before `caffeinate` asserts, the firing is still
  lost. The window is much smaller, not zero.
  The Telegram alert fails at the same moment for the same reason, so this
  failure mode is **silent** — 19 hours passed unnoticed.
- **GitHub: `gh` is installed manually at `~/.local/bin/gh`** — there is no
  Homebrew on this machine, so upgrades mean re-downloading the release zip
  and `install -m 755` over it. Auth token is in the macOS keyring, config in
  `~/.config/gh/`, and `gh auth setup-git` is configured so `git push` works.
  Remote is the private repo `kOkO34344/TradingBotApp`. A Pylint GitHub
  Actions workflow runs on PRs (`.github/workflows/pylint.yml`).

## Practical

- Owner's shell shows `(base)` conda AND `(.venv)` — make sure `.venv` is active.
- IBKR: TWS paper port 7497, Gateway 4002. Live ports are refused in code.
- Commit style: plain descriptive messages, commit after each working increment.
