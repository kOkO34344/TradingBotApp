# TradingBotApp — project memory

Multi-asset trading system (indices, forex, commodities, crypto — as CFDs)
built incrementally with strict evidence gates. Owner: Koko. Venue: FTMO via
the cTrader Open API, on a Challenge account with SIMULATED capital.

Interactive Brokers was the venue until 2026-08-02 and its code was removed on
2026-08-09. Where this file still names it, that is deliberate: the incidents
on that broker are where most of the rules below came from, and the lessons
outlived the adapter.

## Non-negotiable rules

**FTMO is the only venue (2026-08-09).** IBKR was retired in place on
2026-08-02 and removed entirely a week later, at the owner's explicit
instruction and with the rule-9 conflict stated first: `ibkr_service.py`,
`paper_trader.py`, `reflect_on_trades.py`, `autotrade_runner.py`, the six
`api/` modules behind them, the three web screens and both launchd jobs are
gone. Several rules below were written about that venue; they are kept, in
rewritten form, because the LESSONS are what earned them and every one of them
was paid for with a real failure. Nothing about their history is fictional.

1. **Simulated capital before real money.** The FTMO Challenge account is
   simulated — the real exposure is the entry fee, not trading capital. Phase 4
   (real capital) stays locked and is not reachable from any code path here.
   The old form of this rule guarded IBKR live ports and account ids; that
   guard went with its venue, and its replacement is that **nothing in this
   project can reach a real-money account at all.** If that ever changes, the
   port/account refusal comes back before the first order, not after.
2. **No order without a stop.** Every FTMO entry carries `relativeStopLoss` on
   the SAME request as the entry, so the stop is atomic with the fill and is
   applied from the ACTUAL fill price — slippage cannot widen real risk. Since
   2026-08-08 every entry also carries a take-profit. **Stops are verified by
   reading the venue back**, never inferred from an order having been sent: a
   rejected cTrader order arrives as an EVENT, not an error response, and the
   first live FTMO order was refused while the code reported `{'sent': True}`.
3. **Limits are enforced in code, never in prompts, and every exposure limit is
   gated on opening — a limit must never block an exit.** A cap on NEW exposure
   is risk control; blocking a close raises risk, which is the opposite of the
   job. Learned the hard way on 2026-07-27, when a $5,000 notional cap trapped
   both open positions: AAPL and JNJ were under the cap at entry, appreciated
   past it, and became un-exitable, so the rebalance silently held instead of
   rotating. **The cap trapped winners specifically.** On FTMO this is why
   `flatten_all()` has no rule engine, sizer or limit in front of it.
   FTMO's own limits live in `ftmo_rules.py` and are measured on **equity
   including floating P&L**, so the account can fail with no order placed —
   which is why this venue gets a continuous monitor rather than a pre-trade
   gate. A pre-trade gate structurally cannot see that failure: the 2026-07-23
   GOOGL stop-out moved the account $422 overnight and the breaker was simply
   never evaluated, because nothing tried to place an order.
4. **Honest backtesting.** In/out-of-sample split, after costs, vs buy-and-hold
   SPY. Never tune parameters until a backtest looks good and call it validated.
   Negative results get reported, not massaged.
5. **Autonomy is earned by graded evidence** (`research_log/` + `grade_calls.py`
   calibration + months of paper trading), never by adding capability.
6. Every order attempt/block/fill goes to `trade_journal.csv`. If it's not in
   the journal, it didn't happen. **The 46 `venue=ibkr` rows stay forever** —
   removing a venue removes its CODE, never the record of what it did. An audit
   trail you prune when a venue is retired is not an audit trail.
7. **Kronos is the project's signal; momentum is DISABLED.** Owner instruction,
   2026-07-28: momentum does not run again until Koko explicitly asks for it in
   that session. Enforced in code by `signal_policy.py`, not by convention —
   callers must pass `allow_momentum=True`, and every `.get("signal", ...)`
   fallback defaults to `kronos` so config drift cannot resurrect it.
   `ftmo_runner.py` **refuses to fire** on a disabled signal (logs + texts,
   places nothing) rather than substituting a different one. Same opt-in
   pattern as rule 2 — don't pass `allow_momentum` without the owner asking in
   that session.
   **This runs against the project's own evidence, deliberately and with the
   owner's knowledge — record it that way, don't rationalize it.** Momentum
   rotation is still the only strategy family that ever earned Phase 3
   (~18.5% CAGR vs SPY 16%); Kronos measured Spearman IC 0.036 / 50.0% hit
   rate daily and IC -0.081 / 46.4% hourly, i.e. the enabled signal scored
   *worse* than the disabled one on the only head-to-head screen. Kronos being
   the focus is a research direction, not a validated edge. Rules 4 and 5 are
   unchanged. Backtest and research scripts (`strategy_shootout.py`,
   `broad_universe_momentum.py`) are NOT gated — they place no orders, and
   gating evidence-generation would defeat rule 4.
8. **The FTMO path runs FULLY UNATTENDED, and that is a deliberate exception to
   rule 5 — flag it as such, and do not treat it as precedent.** Requested
   explicitly on 2026-08-02 with the evidence position stated first: 0 graded
   calls, Kronos IC ~0 on the only screens run, and no IC screen at all yet for
   indices, FX or commodities. The rule engine, monitor and sizer are enforced
   regardless — autonomy removes the human approval step, never a limit.
   **This is the SECOND such exception.** The first was `autotrade_runner.py`
   (2026-07-24, IBKR, now removed), built at the owner's twice-confirmed
   request despite both signals it could run showing no measurable edge at that
   cadence. It is named here because the PATTERN is the thing to notice — two
   unattended paths, both authorised over the project's own evidence — not
   because the code still exists.
9. **The IC-screen condition has been OVERRIDDEN, and this rule must say so
   rather than describe a gate that is no longer holding.** The original
   condition (owner's own, 2026-08-02) was that Kronos may only trade an asset
   class that has passed its own IC screen. All four classes were screened on
   2026-08-03 and **all four failed**; re-screened at a 5-day horizon on
   2026-08-08, **all four failed again**. On 2026-08-05 the owner instructed the
   path to run anyway, with that evidence stated first, and it was **armed on
   2026-08-06** — `ftmo.autotrade.enabled` true, launchd firing hourly at :30.
   **That is a THIRD deliberate exception to rule 5.** Flag it exactly like
   rule 8; it is not precedent.
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

## Architecture

File purposes are documented in each script's own module docstring
(`trader_app.py`, `ftmo_runner.py`, `research_agent.py`, `grade_calls.py`,
`indicators.py`) — read those rather than duplicating them here.

- `signal_policy.py` is the SINGLE SOURCE OF TRUTH for which signal may run
  and which is the default (rule 7). Every live signal path imports it;
  nothing decides this locally. Has a `python3 signal_policy.py` offline
  selftest. To change the project's focus signal, change `DEFAULT_SIGNAL` /
  `DISABLED_SIGNALS` there — not in five `.get()` fallbacks.

- **FTMO venue — the original five modules, all with offline `--selftest`.**
  `ftmo_rules.py` decides (limits, three thresholds, both products),
  `ftmo_monitor.py` watches equity continuously, `ftmo_sizing.py` sizes,
  `ftmo_audit.py` records why, `ftmo_service.py` talks to cTrader.
  Three more since 2026-08-05: `ftmo_session.py` is the LONG-LIVED connection
  (streaming quotes, trendbars, orders — `ftmo_service` is one-shot and cannot
  trade), `ftmo_signal.py` turns a Kronos ranking into sized stop-protected
  orders, and `ftmo_smoke_order.py` proves the order path with one tiny trade.
  **`ftmo_runner.py` (2026-08-06) is the unattended runner** and, since the
  IBKR removal, the ONLY thing in this project that places an order.
  `ftmo_closes.py` (2026-08-08) detects positions that closed WITHOUT the
  runner — see the close-detection section below.
  **670 offline checks, re-measured 2026-08-11** across the TWELVE modules
  that carry a `--selftest`: `ftmo_runner` 100, `ftmo_sizing` 90,
  `ftmo_signal` 85, `ftmo_rules` 70, `ftmo_session` 70, `ftmo_monitor` 63,
  `ftmo_audit` 48, `ftmo_closes` 43, `ftmo_service` 43, `trade_journal` 26,
  `indicators` 20, `secrets_store` 12.
  **The previous figure here — "579 across the ten modules" — did not
  reproduce, and had already drifted before the 2026-08-11 edit that
  prompted the recount** (`ftmo_signal` was recorded as 35 and was really 73;
  `ftmo_sizing` as 81 and was really 90; `indicators` and `secrets_store` were
  simply omitted). Recorded as a correction rather than silently overwritten,
  because a count nobody re-measures is the same class of thing as the CI
  workflow this file claimed for weeks and never had.
  Re-measure with, and note the two output formats:
  `for m in $(grep -l -- --selftest *.py); do .venv/bin/python3 $m --selftest
  | grep -cE '^\s+(ok|FAIL|PASS)'; done` — `indicators.py` prints `PASS`,
  everything else prints `ok`, so a sweep matching only `ok` silently scores
  it zero.
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
  gate — a gate consulted only when an order is being placed structurally
  cannot see a limit breached by a stop firing overnight.
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
  Extracted from the IBKR adapter on 2026-08-06 because an FTMO order has to be
  journalled too, and importing a broker adapter — and `ib_async` with it — to
  record a trade on a different broker is the wrong dependency. That extraction
  is why the audit trail did not have to move when the venue was removed three
  days later. Has a `python3 trade_journal.py --selftest` offline check and a
  `--describe`.

- `indicators.py` is the SINGLE SOURCE OF TRUTH for technical math, shared by
  trader_app charts and research_agent prompts (human and AI see identical
  numbers). It has `--selftest`. Never reimplement indicators elsewhere —
  including in any future web dashboard.
- `trading_agent_service.py` — third-party TradingAgents wrapper. NEVER RUN yet;
  daily-granularity only, candidate for one evaluation run vs research_agent.
- `watchlist.py` — **the RESEARCH universe, not the traded one** (since
  2026-08-09). Stored as NAMED GROUPS (`trader_settings.json`'s
  `watchlist_groups`), with `tickers` DERIVED as the deduped union and
  regenerated on every save. Groups are the source of truth; `tickers` stays
  the contract every consumer reads (research agent, Kronos, backtests,
  trader_app). Edit via `trader_app.py` menu 9 ONLY — the old raw ticker edit
  in Settings was removed deliberately, because writing `tickers` directly
  would desync it from the groups and be silently reverted on the next group
  save.
  **What FTMO can actually trade is a different set entirely** — CFDs named
  `EURUSD`, `US30.cash`, `NATGAS.cash` — derived from the venue's own symbol
  capture by `ftmo_signal.build_universe`. Keeping the two apart is the honest
  arrangement: researching AAPL on yfinance daily bars is a real activity, and
  pretending this list also describes what can be bought would be exactly the
  quiet mismatch this project keeps getting bitten by.
  Symbols are still validated on entry, and the REASON changed with the venue:
  it was "the order path trades US stocks only", it is now "yfinance reports
  volume as identically ZERO for spot FX and Kronos conditions on volume", so
  foreign listings (`9988.HK`), FX (`EUR.USD`), crypto (`BTC-USD`) and futures
  (`ES=F`) are still dropped and REPORTED, never silently discarded. Same
  symbols, an honest reason. Has a `--selftest`-style `python3 watchlist.py`
  offline check.
  `--group <name>` / `--list-groups` work on `run_research_agent_watchlist.py`.
  **The held-position guard was REMOVED with IBKR, deliberately.** It existed
  because `paper_trader.py` filtered live holdings with `if sym in tickers`, so
  a removed symbol's position went invisible and stopped being managed. Nothing
  filters positions by this list now — `ftmo_closes.py` reconciles against what
  the venue reports is actually open. If any consumer ever starts filtering
  holdings by the watchlist again, put the guard back.

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

  **RE-SCREENED AT A 5-DAY HORIZON, 2026-08-08 — ALL FOUR FAILED AGAIN.**
  Run because `PRED_LEN` changed 20 -> 5 (owner decision), which is a different
  cadence and therefore a legitimate re-screen under the rule below, NOT
  parameter-hunting: same cached price data, same seed (42), same 24
  checkpoints, same matched baseline. Only the horizon moved.
  `KronosAI/kronos_ic_5d.log` is the raw output.

  | class | Kronos IC | t | hit | momentum IC | verdict |
  |---|---|---|---|---|---|
  | indices | +0.052 | +0.61 | 49.6% | +0.069 | **FAILED** |
  | FX (CME futures) | -0.064 | -0.69 | 54.7% | -0.051 | **FAILED** |
  | commodities | -0.017 | -0.21 | 50.4% | +0.090 | **FAILED** |
  | crypto | +0.103 | +1.45 | 50.8% | +0.045 | **FAILED** |

  Max |t| is 1.45. **Shortening the horizon did not help: every IC moved
  TOWARD zero or stayed put** (indices +0.068 -> +0.052, FX -0.138 -> -0.064,
  commodities -0.053 -> -0.017, crypto unchanged). The matched momentum
  baseline failed all four here too. The horizon change bought nothing
  measurable in either direction; the owner's decision on 2026-08-08 was to
  **stay at 5**, with that stated.

  **The DIRECTIONAL HIT RATE is the number that matters for the hybrid**, and
  it is 49.6% / 54.7% / 50.4% / 50.8% against a 50% chance rate — a coin flip.
  FX's 54.7% is the best of them, sits at ~1.3 sigma on pooled pairs that are
  not independent, and comes with a NEGATIVE IC. This is why
  `ftmo_signal.apply_kronos_veto` is built and selftested but **deliberately
  NOT wired into `plan_orders`**: the AND gate uses Kronos purely as a sign
  filter, so on a coin-flip hit rate it would delete momentum picks at random
  and make the hybrid strictly worse than momentum alone. Do not wire it on
  this evidence. It is not a filter; it is a subtraction.

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
  **EVIDENCE AGAINST, 2026-08-08: it did not reproduce at a 5-day horizon.**
  The same class, same data, same dates, same seed returned a hit rate of
  **49.6%** — an ordinary coin flip, not the sub-chance rate a systematic
  directional bias would produce. A real long bias should show up at both
  horizons; showing up only at 20 days is what a one-screen artifact looks
  like. This does not formally kill the hypothesis (the proper test named
  above — mean predicted vs mean realized per class — still has not been run),
  but it is the first independent look and it points the other way. Do not
  cite the 39.6% figure without this alongside it.

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
  top-N rotation is unaffected" is wrong.** Two dry-run rotation proposals
  ~30 minutes apart, same closed-market data, same `sample_count`,
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
  **Mitigated 2026-08-02 by the margin route**, now in `ftmo_signal.py`:
  `apply_rotation_margin()` gives an incumbent holding hysteresis — it keeps
  its slot unless a challenger beats it by more than `rotation_margin_pct`
  (default **1.0** point, calibrated to the observed spread above, not to
  theory; 0 restores strict ranking). Applied where the held set is known, so
  the runner and the browser preview cannot diverge. `rank_boundary_gap()`
  prints the rank N/N+1 gap with every proposal, so "check the gap" is on
  screen rather than a thing to remember. Both are pure functions with offline
  coverage in the module's `--selftest`, which replays the actual 2026-07-28
  pair of runs and asserts they collapse to the same decision.
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

- Phase 1 (research agent): built. 38 graded calls at 5d as of 2026-08-03 and
  no detectable skill — see the Work Queue for the honest reading of that
  sample.
- Phase 2 (infrastructure): hardened and self-tested. **670 offline checks**
  across the ten FTMO modules, plus the `api/` selftests. No credentials or
  venue connection needed for any of them.
- Phase 3 (unattended trading on simulated capital): LIVE on FTMO since
  2026-08-06, armed, firing hourly at :30 inside a 16:30-11:30 Sofia window.
  This phase ran on IBKR paper from 2026-07-21 to 2026-08-02 with a human
  approval loop; that venue was retired and then removed. **An LLM is never in
  the intraday firing loop** — rules fire at machine speed, the agent reasons
  at research speed.
- Phase 4 (real capital): LOCKED, and not reachable from any code path that
  currently exists.

### What Phase 3 on IBKR cost, and why it is still written down

The venue is gone; these are the incidents that produced half the rules above,
and every one of them is a live hazard on FTMO too.

- **A stop with no explicit TIF silently expired at the session close**,
  leaving three positions completely unprotected with nobody aware
  (2026-07-21). *Lesson: after ANY entry, verify the stop by reading the venue
  back — "PreSubmitted" checked minutes after placement says nothing about
  hours later.*
- **GOOGL closed on 2026-07-23 and NOTHING recorded it for two days.** Its stop
  gapped through (326.06 stop, filled at the 321.13 open) — est. -$422, ~$69 of
  it pure gap slippage. No journal row, no reflection, no alert; the code only
  journalled exits it placed itself. *That hole is what `ftmo_closes.py`
  exists to close on this venue.*
- **The daily-loss breaker did not fire on that $422 loss**, because it was a
  pre-trade gate consulted only when placing an order and nothing tried to
  place one that day. *This is precisely why FTMO gets a continuous monitor —
  see rule 3.*
- **A rebalance HALF-EXECUTED and the journal recorded it as a total failure**,
  for a full day (2026-07-27). Two orders filled while every record said the
  account was unchanged; it went from 2 positions to 4. Both root causes — an
  unset TIF misread as a rejection, and a status snapshot taken one second
  after placement — are in the condensed broker gotchas below.
- **A notional cap blocked the EXITS for both open positions**, because the
  limit was not gated on `opening`. It trapped winners specifically: both were
  under the cap at entry and appreciated past it. *Rule 3.*

Corrections for all of the above were appended to `trade_journal.csv` as
`RESULT_CORRECTED` / `NOTE` rows, with the originals left in place and
annotated. Those 46 rows are still served by the Ledger.

## Close detection — the two-tier lesson (now in `ftmo_closes.py`)

The IBKR implementation is gone; **the design rule it earned is not, and
`ftmo_closes.py` inherits it.** Two prohibitions, both paid for in production:

- **Do not collapse it to one tier.** The live event stream is nearly free and
  catches almost nothing, because the runner's session lives about two minutes
  an hour. The diff against remembered state is what actually finds closes.
- **Do not treat a failed read as an empty account.** IBKR's version once
  journalled a phantom full liquidation on a Saturday against two positions
  that were open the whole time, because `ib.positions()` returned empty from a
  swallowed startup timeout. Every "vanished" conclusion needs a SUCCESSFUL
  read, and a diff that would close EVERYTHING is re-read before anything is
  written.

The incident that created the whole tier-2 requirement: a GOOGL position closed
on 2026-07-23 and **nothing recorded it for two days** — no journal row, no
alert, no reflection. Its stop had gapped through (326.06 stop, filled at the
321.13 open), which is also why `classify_close()` compares SIDES rather than
distances: a stop that gaps fills BEYOND its level, so nearest-level matching
calls the single most important case "neither".

**Detection time is not event time** — a weekend close is discovered Monday and
the row says so.

## Work queue for Claude Code (in order — finish the job)

1. ~~IBKR venue~~ — BUILT, RUN, RETIRED 2026-08-02, REMOVED 2026-08-09. Kept
   as a numbered item so the queue's history stays readable; the lessons are
   under "What Phase 3 on IBKR cost" above and in the condensed broker
   gotchas. One design point worth carrying forward: **exits run before
   entries**, so position-count headroom is freed before anything new is
   sized. `ftmo_signal.plan_orders` does the same.
2. ~~Web UI on IBKR~~ — superseded by item 5.
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
4. **IBKR — REMOVED 2026-08-09.** Owner instruction, with the rule-9 conflict
   stated first and the decision taken with it in view.
   **Three positions were presumed still open when the code was deleted:**
   JNJ 19 @ 249.98, DIS 52 @ 95.39, AMZN 21 @ 232.73, last verified against
   the broker on 2026-08-02. They could NOT be re-verified at removal time —
   IB Gateway was refusing connections on 4002 and had been for about a week,
   so the monitor that rule 9 protected had in fact been failing every 30
   minutes and watching nothing. Their GTC stops live at the broker and are
   unaffected by anything deleted here; what was given up is the RECORD — if
   one closes, nothing will journal it. Accepted knowingly: paper account,
   audit-quality cost, and git retains every file.
   Do not reconstruct this as "the positions were closed first". They were not.
5. **Web UI — BUILT 2026-08-01, rebuilt as a four-screen watch station
   2026-08-09.** `api/` (FastAPI) + `web/` (Next.js 16, shadcn/Base UI,
   lightweight-charts). Start with `./run_web.sh`, open http://localhost:3000.
   **Local only — never deploy it and never bind 0.0.0.0:** it can arm the
   unattended runner, so there is no auth layer because there is no network
   exposure. Full rationale in `web/README.md`; UI-specific rules in
   `web/CLAUDE.md`.
   - **Watch / Signal / Market / Ledger**, down from eight routes. Three of the
     old ones were a dimmed IBKR section for a venue that placed no orders and
     went with it. Every old URL redirects, and the ones that became tabs carry
     `?tab=` so a bookmark lands where it used to.
   - **The night band** on `/watch` is the one new capability, not a restyle:
     `/api/ftmo/timeline` reconstructs a full session (16:30→11:30 Sofia) from
     `ftmo_audit/*.jsonl` and draws one cell per hourly wakeup.
     **A firing that was due and did not happen is drawn, not omitted** — the
     22 consecutive sleep failures of 2026-08-08 went unnoticed for 19 hours,
     and this is where that becomes visible at a glance. It reads the audit
     files off disk with no venue session, so it still answers when the broker
     is unreachable.
   - **THIS UI PLACES NO ORDERS.** The preview → execute(token) write flow, the
     dedicated order worker thread and the bracket dialog all belonged to IBKR
     and went with it. The single remaining write is arming or disarming
     `ftmo_runner.py`, which edits `trader_settings.json`. That switch is
     deliberately **not gated on the venue being reachable** — a switch you
     cannot reach when things are going wrong is not a switch — and arming
     (never disarming) requires a confirmation that states what the evidence
     actually says.
   - **The backend is a thin wrapper on purpose.** Risk decisions, sizing,
     journalling and indicator math stay in `ftmo_rules.py` / `ftmo_sizing.py`
     / `trade_journal.py` / `indicators.py`. The browser and the terminal
     cannot diverge about whether the account is safe.
   - **The annunciator rail** replaces a row of status badges: a lamp that is
     DARK has nothing to say, so a quiet rail is a quiet account. Caution
     (amber) stays distinct from warning (red), and anything UNKNOWN lights
     amber rather than staying dark — a dark BREACHED lamp on a dashboard that
     never reached the venue would be the most dangerous pixel in the app.
   - Still true, and still worth weighing: more research/trading cycles is the
     evidence this project is gated on; a dashboard is not.
6. **FTMO venue — LIVE AND ARMED. The only venue since 2026-08-09.**
   Governed by rules 8 and 9. Five modules, selftested offline (no credentials
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

## FTMO autotrade (unattended) — `ftmo_runner.py`

Kronos deciding and executing on FTMO with no approval step, and since the IBKR
removal the only order path in the project. Rules 8 and 9 govern it, and it is
the **third deliberate exception to rule 5** — flag it that way.

**Armed by `trader_settings.json` → `ftmo.autotrade.enabled`**, or the header
switch on the web UI, or `trader_app.py` menu 8. A missing key reads as OFF.
A selftest asserts an unrelated top-level `autotrade` block cannot arm it —
that existed to keep the two venues' switches apart, and is worth keeping now
that a stale `autotrade.enabled` could linger in an old settings file.
The switch is **not gated on the venue being reachable**: a switch you cannot
reach when things are going wrong is not a switch.

The cycle, once per invocation: connect → positions and balance → rule engine →
FTMO's own daily bars → Kronos → rank → plan → **exits, then entries** → verify
every stop by reading the venue back → journal + audit + text.

### The universe is the whole account now, not a basket (2026-08-11)

Owner instruction: forecast everything the FTMO account can actually trade.
The runner ranks **101 symbols across five classes** — commodities 16,
crypto 30, fx 5, indices 4, **stocks 46** — derived from the venue's own
capture by `ftmo_signal.universe_from_capture`, which had existed since
2026-08-08 and had never been wired in. Until this change the runner used
`DEFAULT_UNIVERSE`, a hand-written basket of **14**.

`ftmo_signal.resolve_universe()` decides, and its precedence is
most-specific-first: an explicit `ftmo.universe` in `trader_settings.json`
wins outright; otherwise `ftmo.universe_source` (`"capture"`, the default, or
`"default"` for the old basket); otherwise capture. An explicitly EMPTY
`ftmo.universe` still reaches `build_universe` and still raises — emptying the
config is how someone turns the bot off, and a fallback there would be the
worst possible reading of it. An unrecognised `universe_source` **raises**
rather than quietly reverting to 14 symbols.
The provenance string is logged and audited every firing, so "which symbols
was this run even looking at" is answerable afterwards from the log rather
than from whichever settings file happens to be on disk later.

**Ranking 101 symbols by predicted percentage return systematically selects
the most volatile instruments in the account, and on this universe that means
micro-cap alt-coins.** The first live dry-run's top four were GALUSD +47.67%,
VECUSD +25.51%, IMXUSD +18.11%, MANUSD +16.65% — every one a crypto priced in
fractions of a cent, ahead of every index, every FX pair and all 46 stock
CFDs. A five-day forecast of +47% is not a forecast, it is the noisiest series
in the set winning a contest scored on amplitude. The 14-symbol basket had the
same bias and bounded it by construction; 30 cryptos do not.

**Fixed the same day by ranking WITHIN asset class** (owner instruction,
2026-08-11). `ftmo_signal.cap_per_class()` allows each class at most
`ftmo.autotrade.max_per_class` candidates — **default 1** — so the pool
becomes the class leaders and a top_n of 4 necessarily spans four different
classes. `0` disables the cap and restores pure global ranking.

It is a FILTER on the candidate pool, not a re-scoring, and that choice is
load-bearing. `apply_rotation_margin` compares raw predicted-return
differences against `margin_pct`, which is calibrated to an observed ~1-point
sampling spread and **not** to theory. Normalising returns into z-scores or
percentiles would have silently changed the units that margin is measured in,
and this project has already shipped one inverted-hysteresis bug that traded
live for a day. A filter leaves every downstream comparison in the units it
was calibrated in; only the pool those comparisons run over gets smaller.

Three consequences worth knowing before reading a plan:

- **The pool, the boundary gap and the target all use the SAME capped list.**
  Measuring the rank N/N+1 gap on the full ranking while selecting from a
  capped one would print a number describing a decision nobody made — on the
  fixture above the uncapped gap is 13.55 and the real one is 0.60.
- **A held position that is no longer its class's leader gets rotated out.**
  That is the cap working, not a bug: holding two cryptos is the
  concentration it exists to prevent. Exits are computed from `held` and the
  target and never consult it — rule 3 keeps every exit path ungated.
- `format_plan` marks the table `*` selected, `+` eligible class leader, blank
  = a higher-ranked name in the same class took the slot, and prints the pool.

**This suppresses concentration; it does not create edge.** Every IC screen
this project has run is still ~0, and picking the best of a bad class is still
picking from a bad class. What it buys is that a single asset class can no
longer take the whole book on the strength of having the widest ruler.

Three costs that came with it, all measured on the 2026-08-11 dry-run:

- **A cycle now takes ~6.3 minutes, up from ~3.** Bars for 101 symbols take
  ~100s, the forecast 261s in four batches. Still comfortably inside an hourly
  schedule, but it is more wall clock exposed to the sleep race that cost a
  full day on 2026-08-08.
- **DO NOT run `--dry-run` while a scheduled firing is in progress. It can
  WEDGE the unattended run, and this was demonstrated, not theorised.** On
  2026-08-11 a manual dry-run overlapped the 19:30 firing; each process loads
  its own ~2 GB Kronos model, the machine went into swap (89,817 pageouts,
  27% memory free), and the unattended firing was left with **28 seconds of
  CPU across 13 minutes of wall clock and an RSS of 19 MB** — its model paged
  out, thrashing rather than computing. It had to be killed; it placed
  nothing, because the account was breached, but on an unbreached account
  that is a firing silently lost. The 14-symbol universe was light enough to
  hide this. Check `ps` for a running `ftmo_runner.py` first, or preview from
  `/signal` in the web UI, which reuses the one long-lived session.
  Note the symptom is **not** the sleep signature: `caffeinate` was held the
  whole time and the machine never slept. Low CPU with a tiny RSS is memory
  pressure; the sleep failures wear a `SessionError` instead.
- **`ftmo_session.TRENDBAR_MIN_INTERVAL_S` (0.22s) paces historical requests**
  to ~4.5/sec, under cTrader's documented 5/sec. It is enforced in
  `trendbars()` — the one place every historical request passes — because a
  limiter a caller can forget to use is not a limiter.
- **`assert_bars_match_quote` now accepts the bars the caller already
  fetched.** It used to pull its own 3-bar sample, so each symbol cost two
  historical round trips: ~202 per firing at this size. Checking the same
  series the forecast consumes is also strictly more honest than re-fetching.

Two things that had to scale with it, and one number to watch:

- The post-subscribe settle is `min(2 + 0.04n, 8)` seconds, not a flat 2.
  `assert_bars_match_quote` asserts **nothing** for a symbol that has not
  ticked yet, so a fixed 2s would have silently reduced the 1000x-scaling
  guard to a handful of names. The runner now logs
  `price-scaling cross-check: N/M verified against a live quote` every firing.
  **It read 101/101 on the first real run; if that number starts dropping, the
  guard is eroding and the log is the only place it shows.**
- Kronos is called in batches of `ftmo.autotrade.forecast_batch` (default 24)
  via `kronos_agent.forecast_frames(batch_size=...)`. `predict_batch` stacks
  every symbol into one tensor and 101 × sample_count 10 is 1,010 sequences in
  a single allocation. Batching is a scheduling detail, not a modelling one —
  each symbol's forecast depends only on its own history. `0` restores one
  call for everything.
- **Six symbols are skipped every run for short history** and are named in the
  log: ARM(130), LMT(130), META(386), RTX(381), SPCX(39), DXY.cash(221) —
  Kronos needs LOOKBACK=400 daily bars. 95 of 101 are forecast.

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

Better positioned than the IBKR version was, in one specific way: cTrader
returns the actual closing DEAL via `ProtoOADealListByPositionIdReq`, so a
detected close carries the venue's own price, gross profit, swap and
commission. The IBKR monitor wrote no reflection on an unattended close because
it had no realized P&L to build one from; here there is one, so the research
feedback loop no longer has a hole exactly where the unattended closes are.

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
as keeping a retired venue monitored while it still held positions. **It has
no launchd job
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
`zoneinfo` and stays right across the EEST/EET switch. The retired IBKR runner
split NYSE hours the same way and for the same reason.

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

To disarm: the header switch on the web UI, `trader_app.py` menu 8, or set
`ftmo.autotrade.enabled` false. To
stop the schedule:
`launchctl unload ~/Library/LaunchAgents/com.tradingbotapp.ftmo.plist`.

**`trader_settings.json` carries the armed flag and is tracked in git.** If it
is left modified-but-uncommitted, a stray `git checkout .` silently DISARMS the
runner. That direction fails safe, but it fails *quietly* — check the flag
before concluding the bot is running.

Preview without arming anything: the "Preview plan" button on `/signal`, or
`python3 ftmo_runner.py --force --dry-run`. Both run the identical pipeline and
place nothing.

## Phone notifications (TelegramBot/) — use this for anything long-running

Default to running backtests and other long one-shot scripts through
`./run_notify.sh <script> [args]` rather than calling them directly.
Scripts that already notify from inside themselves must **NOT** be wrapped
(`ftmo_runner.py`, `ftmo_closes.py`, `run_research_agent_watchlist.sh`,
`daily_digest.py`) — wrapping them double-notifies or spams a no-op poller. Full detail, including
what each of those texts on and how to wire up a new one, is in the
**`notify-on-long-runs` skill**.

Note both digests quote `CLAUDE.md`'s Work Queue and Empirical Findings
sections close to verbatim, so keep those reasonably current.

## Known environment gotchas

- **Three broker lessons from the retired venue, kept because they generalise.**
  The IBKR code is gone; these are not about IBKR.
  1. **Never leave a field the broker's config can fill in.** `place_bracket_order`
     built its parent order with no explicit TIF. The Order Preset silently
     filled in DAY and *announced* it as error 10349, which looked like a
     rejection and was a warning — the journal recorded two orders as
     `Cancelled` that had actually FILLED, and the account ran two positions
     ahead of every record for a full day. An unset field is one someone else
     gets to choose, and you will not necessarily be told what they chose.
  2. **Never report an outcome you did not verify against the venue.** That
     same incident journalled an order's status after a fixed one-second sleep
     — a snapshot, not an outcome. The FTMO counterpart is live: a rejected
     cTrader order arrives as an EVENT, not an error response, and the first
     live FTMO order was refused while the code reported `{'sent': True}`.
  3. **Never derive a rate's direction from arithmetic that reconciles either
     way.** Converting a EUR-denominated account to USD needed the broker's own
     `ExchangeRate`, and inverting it misstated equity by ~29% (1.137 vs 0.879).
     The broker's own cash-balance identity was tried as the check first and
     REJECTED: an inverted rate still reconciled within 0.26%. It took an
     independent yfinance quote to catch it. A self-consistent check is not a
     check.
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
  it. Same class as the "`Cancelled` RESULT row seconds after placement"
  incident in the condensed broker gotchas — never report an outcome you did
  not verify against the venue.
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
  Remote is the private repo `kOkO34344/TradingBotApp`.
  **There is NO CI on this repo.** This entry used to claim a Pylint GitHub
  Actions workflow ran on PRs at `.github/workflows/pylint.yml`; there is no
  `.github` directory at all and there never was one in the history. Corrected
  2026-08-11. Worth keeping as a correction rather than a silent deletion,
  because a documented check that does not exist is worse than a known gap —
  it is the same shape as the four fabricated grades in `graded_calls.csv`
  and the `{'sent': True}` on a refused order: a record asserting an outcome
  nobody verified.
  What actually guards the code is local and now automatic: the
  `PostToolUse` hook in `.claude/settings.json` runs all twelve `--selftest`
  modules after any edit to a top-level `.py`. See the hooks section below.

## Practical

- Owner's shell shows `(base)` conda AND `(.venv)` — make sure `.venv` is active.
- Commit style: plain descriptive messages, commit after each working increment.
