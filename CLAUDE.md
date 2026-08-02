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

## Architecture

File purposes are documented in each script's own module docstring
(`trader_app.py`, `ibkr_service.py`, `research_agent.py`, `grade_calls.py`,
`indicators.py`) — read those rather than duplicating them here.

- `signal_policy.py` is the SINGLE SOURCE OF TRUTH for which signal may run
  and which is the default (rule 8). Every live signal path imports it;
  nothing decides this locally. Has a `python3 signal_policy.py` offline
  selftest. To change the project's focus signal, change `DEFAULT_SIGNAL` /
  `DISABLED_SIGNALS` there — not in five `.get()` fallbacks.

- `ftmo_rules.py` is the SINGLE SOURCE OF TRUTH for FTMO Challenge limits —
  pure logic, no network, `--selftest` (70 checks) and `--show` for the derived
  numbers. Answers three questions that must never be conflated: may I OPEN,
  must I FLATTEN, and could this phase PASS. The third is not a trading
  permission — too few trading days or a failed Best Day Rule means keep
  trading, not stop. Every FTMO limit is measured on **equity including
  floating P&L**, so a limit can be breached with no order placed; that is why
  the FTMO path gets a continuous monitor and not a pre-trade gate like
  `RiskGuard`. Each published limit becomes three thresholds — soft (stop
  opening), flatten (close everything), breach (already failed) — because
  stopping exactly at FTMO's number leaves nothing for slippage or a gap.
  The 1-Step trailing floor moves ONLY in `roll_day()`, at the 00:00 CE(S)T
  boundary, off the day's CLOSING balance; ratcheting it on intraday equity
  would tighten the limit using profit that was never kept.
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

## Open hypotheses (NOT findings — do not cite these as evidence)

Kept separate from Empirical Findings on purpose: these are single
observations that have not met this project's evidence bar. Promote one only
after testing it properly.

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
  at the open, not the stop. Est. -$422 on 14 shares, ~$69 of that pure gap
  slippage. No journal row, no reflection, no Telegram alert. Root cause:
  `reflect_on_trades.py` detected closes only via `reqExecutions`, and IBKR
  serves executions for the CURRENT SESSION ONLY — verified returning 0 rows
  for a 30-day filter. Any close happening while the script isn't polling
  that session (overnight, weekend, machine asleep) was invisible to it
  permanently, and `LOOKBACK_DAYS` couldn't help because the data isn't there
  to ask for. Fixed (see the close-detection note below); the GOOGL row was
  backfilled as `CLOSE_RECONSTRUCTED` with its inference method in `detail`.
  This also exposed that nothing journaled autonomous stop fills at all
  (`paper_trader.py` only journals exits it places itself).
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
  **The 2026-07-27 Kronos rebalance HALF-EXECUTED, and the journal recorded it
  as a total failure.** Approved (AMZN/MSFT/DIS in, AAPL/JNJ out). What the
  journal said at the time: both exits blocked, all three entries `Cancelled`,
  zero trades, account unchanged. What had actually happened, verified
  read-only against IBKR on 2026-07-28:
  1. Both **exits were BLOCKED by RiskGuard's notional cap**, which applied to
     closes as well as opens. True, and fixed the same day (see rule 3).
  2. **AMZN 21 @ 232.73 and DIS 52 @ 95.39 FILLED**, each with a live
     full-quantity **GTC** stop (217.74 / 90.83, `PreSubmitted`). Only **MSFT**
     did not fill. IBKR error 10349 was blamed at the time for the
     cancellations; that diagnosis was **wrong** — see the 10349 entry under
     Known environment gotchas. Whatever stopped MSFT specifically, it was
     not the preset.
  Net effect: the account went from 2 positions to **4** — AAPL 15, JNJ 19,
  DIS 52, AMZN 21 — while every record here and in `trade_journal.csv` said it
  was unchanged, for a full day. Root cause of the false record was
  `place_bracket_order` journalling a one-second status snapshot — see the
  `Cancelled`-RESULT-row entry under Known environment gotchas. Fixed
  2026-07-28; corrections appended to `trade_journal.csv` as
  `RESULT_CORRECTED` / `NOTE` rows (originals left in place, annotated).
- Phase 4 (tiny real capital): locked until months of Phase 3 evidence.

## Close detection is two-tier (`reflect_on_trades.py`)

Do not "simplify" this back to one tier — the second exists because the first
provably loses events (see the GOOGL incident above).

1. **`reqExecutions`** — exact fill price, realized P&L, commission. Only ever
   sees the current session.
2. **Position-snapshot diff** — compares live positions against
   `trade_reflections/.position_snapshot.json` from the previous run. Catches
   any close the execution tier missed, including partial reductions, at the
   cost of not knowing exit price or P&L. Seeds silently on first run
   (no snapshot ⇒ record baseline, report nothing), dedupes against tier 1 so
   a close caught by both journals once.

**Tier 2 must fetch positions via `fetch_positions_confirmed()`, never a bare
`ib.positions()`** — and the reason is a bug that already fired in production.
`ib.positions()` reads a cache filled by a best-effort startup request inside
`IB.connect()`: `connectAsync` gathers those under `asyncio.wait_for(...,
timeout=4)` with `return_exceptions=True` and, unless `raiseSyncErrors=True`,
**swallows a timeout** — logging "positions request timed out" and returning a
connected, healthy-looking `IB` whose position cache is empty. An empty
`ib.positions()` is therefore ambiguous: genuinely flat, or the fetch failed.
Tier 2 read it as flat, i.e. "everything closed."

Result on 2026-07-25T20:29:38: phantom `CLOSE_DETECTED` rows for AAPL 15→0 and
JNJ 19→0 while both were open on IBKR with live GTC stops — on a **Saturday**,
with no session between that run and the 16:27:14 snapshot that still showed
them. It also advanced the snapshot to `{}`, discarding the real baseline.
Reproduced deterministically 2026-07-27 and fixed by re-requesting positions
explicitly and letting a timeout **raise** (run aborts, nothing journaled,
snapshot untouched) instead of degrading to `[]`. An *answered* request that
returns nothing is a real flat account — `positionEnd` resolves the future —
so the two cases are no longer the same value.

Note this is the same swallow mechanism behind the "benign" connect warnings
in work-queue item 1 ("open orders request timed out"). Benign there, a
fabricated liquidation here — don't generalize "that warning is harmless."

Both tiers now write to `trade_journal.csv` (`CLOSE_FILLED` / `CLOSE_DETECTED`),
independently of whether the reflection agent call succeeds. **Detection time
is not event time** for tier 2 — a weekend close is journaled Monday, and the
row says so.

**Tier 2 journals and texts but writes NO reflection**, because `build_prompt`
needs a realized P&L it doesn't have. So a weekend/overnight stop-out leaves
no `trade_reflections/*.md`, and nothing for `research_agent.py`'s
`load_reflections()` to feed on — the feedback loop has a hole exactly where
the unattended closes are. Fixing it means reconstructing the exit from bars
(as the GOOGL backfill did by hand) rather than trusting IBKR for it.
The snapshot is advanced only after every detected close is journaled, and
never on `--dry-run` — advancing first would move the baseline past a close
that was never recorded, which is the original bug.

## Work queue for Claude Code (in order — finish the job)

1. ~~TWS smoke test~~ — DONE 2026-07-21. Connected to IB Gateway paper (port
   4002), account DUQ903866 verified, pulled real 15-min bars. Note: two
   benign `ib_async` warnings on connect ("open orders request timed out",
   "completed orders request timed out") — harmless on a fresh account with
   no order history, not a code bug.
2. ~~Phase 3 paper-trading loop~~ (`paper_trader.py`) — DONE 2026-07-21 and
   run for real once. Signal: **Kronos forecast ranking, top-N of watchlist**
   (default since 2026-07-28, rule 8; it was momentum until then, which is why
   older journal rows and reflections are labelled `momentum`). Sizing: `qty = floor((NetLiq_usd * risk_pct_per_trade%) /
   (2*ATR))`, clamped to `risk_limits.json`'s max order notional using the
   *buffered* entry price (not raw market price — a real bug hit and fixed
   during the first run). Exits cancel the open stop leg and confirm the
   cancel before flattening; exits run before entries so RiskGuard's
   max_open_positions headroom is freed first. `place_market_order` gained
   an `opening: bool` param so closes don't get checked as new exposure.
   `--dry-run` connects read-only and prints the proposal without asking.
   No scheduler yet — owner runs it manually. Next: a few more manual
   monthly cycles before even considering cron/launchd.
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
   State as of 2026-08-02: `graded_calls.csv` is still **0 graded / 76 pending**
   (38 at 5d, 38 at 21d) and is still the 2026-07-28 run — the projected
   ~07-29 arrival of the first 5d grades came and went without a re-run, so
   the grades exist in the price data but not in the file. Both cycles below
   are overdue; neither has been run since 07-25/07-28. Next actions:
   - Re-run `python3 run_research_agent_watchlist.py` weekly. Every note in
     `research_log/` is dated 2026-07-25, so this is overdue.
     `--group <name>` / `--list-groups` also work if only part of the
     watchlist needs a refresh — see the Watchlist section below.
   - Re-run `python3 grade_calls.py --csv` and actually read the calibration
     report — don't just run it, look at it. Treat any report claiming grades
     from notes not in `research_log/` as corrupt.
   - Pending-call shape as of 2026-07-28 (worth knowing before grading):
     **74% no-edge** (28/38), 16% long, 11% short, and confidence is clustered
     low (18 calls at 3/10, none above 6/10). A mostly-no-edge, low-confidence
     book is cheap to be "right" about under the ±2% flat band — read the
     eventual win rate with that in mind rather than as skill.
4. **Paper trading — operational, not a build task.** `paper_trader.py` holds
   real open positions. **GOOGL closed 2026-07-23** (gapped through its GTC
   stop, ~-$422, found + backfilled 2026-07-25 — see Phase 3 status above);
   **AAPL closed between 2026-07-29T15:22 and 2026-08-01T22:26** — detected
   by `reflect_on_trades.py`'s position-diff tier and journalled
   `CLOSE_DETECTED`, so this one did NOT repeat the GOOGL silent-close
   failure. No execution record, therefore no exit price, realized P&L or
   reflection — the known tier-2 gap. AAPL's last close before detection was
   307.36 against a 309.10 stop, consistent with the stop firing, but that is
   inference and not a record.
   Current holdings are therefore **JNJ (19 @ 249.98), DIS (52 @ 95.39) and
   AMZN (21 @ 232.73)** — three positions. Stop protection was UNKNOWN on
   2026-08-01 (Gateway's `reqAllOpenOrders` was timing out while positions and
   market data answered normally — the 2026-07-29 symptom) and was
   **re-verified read-only on 2026-08-02: all three protected, full quantity,
   `tif=GTC`, `PreSubmitted`** — AMZN 21 @ stop 217.74, DIS 52 @ 90.83, JNJ
   19 @ 237.61. `reqAllOpenOrders` answered normally on that attempt, so the
   07-29/08-01 wedge was transient. DIS and AMZN came from the 2026-07-27
   rebalance that the journal wrongly recorded as `Cancelled` (see Phase 3
   status above).
   **The "Gateway Order Preset blocker" is CLOSED** — it was a missing `tif`
   on our own parent order, fixed in code 2026-07-28 and verified by probe
   (no 10349, LMT `tif=DAY` / STP `tif=GTC`). No Gateway change is or was
   needed. Going forward:
   - Re-run monthly (or on-demand) for the next rebalance; check `--dry-run`
     first if unsure what it'll propose. `--dry-run` now connects genuinely
     `readonly=True` (TWS-enforced) and no longer needs a live market-data
     line to size — see the FX-conversion note above.
   - **Every position check must verify stops are GTC, not just "present."**
     Query `ib.trades()` (not just `ib.openTrades()` right after placing) and
     check `order.tif == "GTC"` — a DAY stop will look fine for hours and
     then silently vanish at end of session.
   - `reflect_on_trades.py` now catches closes two ways (executions +
     position-snapshot diff, see the Close detection section above), so a
     GOOGL-style silent close should surface within one 30-min cycle instead
     of needing a manual audit to find. Still worth periodically checking
     `trade_journal.csv` matches what's actually on IBKR — the snapshot tier
     journals a close but not a reflection (no realized P&L to build the
     prompt from), so a research-feedback gap remains for unattended closes.
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

## Autotrade (experimental, unattended) — `autotrade_runner.py`

Unattended hourly rebalancing, off by default. Rule 7 above governs it.
Operational detail (toggle, schedule, signal, execution, notifications,
how to disable) lives in the **`autotrade` skill** — read it before touching
`autotrade_runner.py` or `autotrade_signals.py`.

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
- **cTrader `CH_CLIENT_AUTH_FAILURE: OA client is not in active state` is not a
  credential typo.** It means the Open API application itself is still in
  `Submitted` state at openapi.ctrader.com/apps and has not been approved to
  `Active` yet. The Client ID/Secret are shown before activation, so they look
  usable and are not. Diagnosed 2026-08-02 on the first app-auth attempt.
  Everything below that error is already proven when you see it: the TLS
  handshake to `demo.ctraderapi.com:5035` succeeded, the protobuf request
  encoded and sent, and a real `ProtoOAErrorRes` came back — so do NOT go
  hunting for network, TLS or SDK problems. Wait for activation, then re-run
  `python3 ftmo_service.py --probe`.
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
