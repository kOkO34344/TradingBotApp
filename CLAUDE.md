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
   (`trader_settings.json`'s `autotrade.enabled`). See the Autotrade section
   below before touching this.

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
  `--group <name>` / `--list-groups` work on `run_research_agent_watchlist.py`
  — that flag is written and working, but lives in a file that is still
  UNTRACKED (like `run_notify.sh` / `daily_digest.py`, it came from another
  session and depends on the untracked `TelegramBot/`), so it is not in any
  commit yet. Committing it means committing that whole external set.
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
- **Kronos per-ticker output is noisier than `sample_count=10` suggests.**
  Three consecutive runs on identical data put GOOGL at +2.69% / -3.72% /
  +4.38% — an 8-point spread. Top-3 was stable across all three, so top-N
  rotation is unaffected, but no individual number should be read alone.

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
  Remaining open positions verified 2026-07-25: AAPL 15 @ 328.04 and JNJ 19 @
  249.98, both with live **GTC** stops covering the full quantity (309.10 /
  237.61), and no orphaned GOOGL stop left behind. **Re-verified directly
  against IBKR 2026-07-27** — still open, still `tif=GTC`, `PreSubmitted`.
  **A Kronos rebalance was attempted 2026-07-27 and placed ZERO trades.**
  Approved (AMZN/MSFT/DIS in, AAPL/JNJ out), and every single leg failed, for
  two unrelated reasons — both now understood, one still open:
  1. Both **exits were BLOCKED by RiskGuard's notional cap**, which applied to
     closes as well as opens. Fixed and merged same day (see rule 3).
  2. All three **entries were CANCELLED by IBKR, error 10349** "Order TIF was
     set to DAY based on order preset". **STILL OPEN — Gateway-side config,
     not code.** An Order Preset forces DAY TIF and collides with the explicit
     GTC bracket legs from the 2026-07-21 fix, so IBKR cancels rather than
     accepts. Fix in Gateway's Global Configuration → Presets. **Until this is
     changed, every bracket order will be cancelled** — including every
     autotrade firing. Check this before trusting any rebalance to work.
  Net effect: the account was unchanged, still AAPL 15 / JNJ 19. Note the
  failure mode was *silent in the worst way* — an approved rebalance that
  looks like it ran and simply holds instead of rotating.
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
3. **Research agent — ongoing, in progress.** Re-run 2026-07-25 on the full
   14-ticker watchlist (grown from the original 12 via `AVGO`/`ASML`) — fresh
   notes for all 14 in `research_log/`. `grade_calls.py` was actually run
   2026-07-25: still 0 graded (38 notes × 2 horizons = 76 pending) — the
   5-trading-day horizon needs the 07-20/21 notes to reach ~5 trading days
   old, which lands **~2026-07-30**, not 07-25 as earlier estimated (calendar
   days ≠ trading days). Next actions:
   - Re-run `python3 run_research_agent_watchlist.py` weekly (next due
     ~2026-08-01). `--group <name>` / `--list-groups` also work if only
     part of the watchlist needs a refresh — see the Watchlist section below.
   - Once notes are ≥5 trading days old (~2026-07-30+), run
     `python3 grade_calls.py` again and actually read the calibration
     report — don't just run it, look at it.
4. **Paper trading — operational, not a build task.** `paper_trader.py` holds
   real open positions. **GOOGL closed 2026-07-23** (gapped through its GTC
   stop, ~-$422, found + backfilled 2026-07-25 — see Phase 3 status above);
   current holdings are **AAPL (15) and JNJ (19)**, re-verified directly
   against IBKR 2026-07-27 with live GTC stops for their full quantity.
   **BLOCKER before the next rebalance: fix the Gateway Order Preset forcing
   DAY TIF (error 10349).** Until that is cleared in Global Configuration →
   Presets, every bracket entry is cancelled on arrival — the 2026-07-27
   rebalance placed zero trades because of it. Verify with a single small
   dry-run-then-real entry before trusting a full rotation. Going forward:
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
5. **Web UI (`TraderAppFullStack.txt`) — now legitimately unblocked.** Items
   1-2 are done AND real fills now exist in `trade_journal.csv`, so the
   original "a dashboard before fills exist would display zeros" objection
   no longer applies. Still backend-first: FastAPI wrapper around
   ibkr_service + journal reader, then a Vite frontend (NOT create-react-app
   — deprecated). Reasonable to start when the owner wants it, but weigh it
   against #3/#4 above — more research/trading cycles is more of the
   evidence this project is actually gated on; a dashboard is not.

## Autotrade (experimental, unattended) — `autotrade_runner.py`

Built 2026-07-24. Unattended hourly rebalancing: no y/n prompt, RiskGuard
fully enforced regardless. **Built despite both eligible signals showing no
measurable edge at this cadence** (see Empirical findings) — a deliberate
live paper experiment at the owner's explicit, twice-confirmed request, not
because either signal is validated. See rule 7.

- **Toggle:** `trader_settings.json`'s `"autotrade": {"enabled": bool,
  "signal": "momentum"|"kronos"}`. Set via `trader_app.py` menu item 8, or
  edit the JSON directly. Defaults to `enabled: false`.
- **Schedule:** `com.tradingbotapp.autotrade.plist`, hourly 16:00-23:00 local
  (this machine runs EEST/EET) — a superset of NYSE 9:30-16:00 ET year-round
  (the two DST regimes keep a constant ~7h gap). `autotrade_runner.py` does
  its own authoritative America/New_York market-hours check on every firing
  (`zoneinfo`, not host time) — the launchd schedule only needs to cover the
  window, not match it exactly.
- **Signal:** `autotrade_signals.py` — hourly bars (yfinance, ~2-3yr history,
  separate cache `KronosAI/price_data_hourly_live/` from the backtest's
  `price_data_hourly/`), same LOOKBACK=400/PRED_LEN=20 bar counts the IC
  screen used. `ind.atr()` on hourly bars gives a 14-HOUR stop distance, not
  14-day — deliberately tighter, appropriate for the shorter intended hold.
- **Execution:** `paper_trader.execute_rebalance(..., auto_approve=True)` —
  the exact same sizing/RiskGuard/bracket-order function the human-approved
  path uses (extracted 2026-07-24 specifically so both paths can never
  diverge in risk handling). `client_id=13` (distinct from trader_app's 7,
  paper_trader's 9, reflect_on_trades' 11 — lets all run concurrently).
- **Notifications:** texts on any executed trade or error; silent on no-op
  cycles (same convention as `reflect_on_trades.py`). Every cycle — no-op or
  not — gets one line in `autotrade_runner.log` regardless; that log is what
  to check to confirm it's actually alive, since Telegram silence on a quiet
  market day is expected, not a sign of failure.
- **Known real risk, not just theoretical:** RiskGuard's per-order/position/
  daily-loss limits don't guard against slow bleed from turnover costs on a
  no-edge signal trading far more often than the validated monthly cadence.
  `trade_journal.csv` is the audit trail for catching that — check it
  periodically, don't just assume silence means it's fine.
- **To disable:** turn the toggle off (safest, keeps the job installed for
  later), or `launchctl unload ~/Library/LaunchAgents/com.tradingbotapp.autotrade.plist`
  to stop it firing entirely.

## Phone notifications (TelegramBot/) — use this for anything long-running

One Telegram bot, `TelegramBot/notify.py` (`send_telegram(text)`), reused
across the project. Owner gets a phone text; credentials live in
`TelegramBot/.env` (gitignored, see `TelegramBot/README.md` for setup).

**Any future session (Claude Code or otherwise) starting a backtest or
other one-shot script should default to running it through the generic
wrapper, not calling the script directly** — this is how "notify me from
any session" actually holds:

```bash
./run_notify.sh sma_crossover_backtest.py
./run_notify.sh KronosAI/kronos_backtest.py --sample-count 5
./run_notify.sh research_agent.py AAPL
nohup ./run_notify.sh <script> [args] > /dev/null 2>&1 & disown   # detached
```

It texts on start, on a 20-min output stall (possible hang), and on
done/failed with a tail of the output. Full logs land in `run_logs/`.

Other notification points already wired in (do NOT wrap these in
`run_notify.sh` too — they'd double-notify or spam a no-op poller):
- `run_research_agent_watchlist.sh` — loops the whole watchlist, sends
  ONE consolidated direction+confidence digest instead of one per ticker.
- `reflect_on_trades.py` — texts on every newly-closed position it finds
  (win/loss + reflection status). Runs every 30 min via launchd and is a
  no-op most runs, so this notification lives INSIDE the script,
  conditional on an actual close — never wrap the whole script.
- `ibkr_service.py`'s `journal()` — texts whenever RiskGuard actually
  BLOCKS an order (real journal writes only, not the `--selftest` path).
- `paper_trader.py` — one consolidated text after a real (approved)
  rebalance executes, listing what was bought/sold.
- `autotrade_runner.py` — same convention as `paper_trader.py` above (one
  consolidated text per executed rebalance, tagged `-hourly`), plus an
  alert on any error. Silent on no-op cycles — see the Autotrade section.
- `daily_digest.py` — two modes, one script:
  - `--mode morning` (default) via `com.tradingbotapp.dailydigest.plist`,
    07:30 local — "plan the day": due-today/freshness flags + Work Queue
    + Empirical Findings, quoted close to verbatim from this file.
  - `--mode evening` via `com.tradingbotapp.dailydigestevening.plist`,
    20:00 local (before the 22:00 vault sync) — "recap the day": today's
    actual activity (trade_journal.csv entries since midnight, new
    research notes logged today, new trade_reflections/ files today),
    plus the same freshness flags as a heads-up for tomorrow.
  No LLM call either way, just file reads — keep CLAUDE.md's Work Queue
  and Empirical Findings sections reasonably current since both digests
  quote them directly. Manual run: `./daily_digest.sh [morning|evening]`.

If you add a new recurring/polling script, give it its own conditional
`send_telegram()` call at the actual event, the same way
`reflect_on_trades.py` does — don't put a blanket wrapper around a poller.

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
- **A Gateway Order Preset can silently cancel every bracket order.** IBKR
  error **10349** "Order TIF was set to DAY based on order preset" — the
  preset overrides the explicit `tif="GTC"` on bracket legs and IBKR cancels
  instead of accepting. Hit 2026-07-27, killed all three entries of an
  approved rebalance. Fix in Gateway's **Global Configuration → Presets**.
  This is invisible from the code side: `place_bracket_order` returns, the
  journal records SUBMIT, and the RESULT row just says `Cancelled`.
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
- **GitHub: `gh` is installed manually at `~/.local/bin/gh`** — there is no
  Homebrew on this machine, so upgrades mean re-downloading the release zip
  and `install -m 755` over it. Auth token is in the macOS keyring, config in
  `~/.config/gh/`, and `gh auth setup-git` is configured so `git push` works.
  Remote is the private repo `kOkO34344/TradingBotApp`. A Pylint GitHub
  Actions workflow runs on PRs (`.github/workflows/pylint.yml`).

## Practical

- Python env: `.venv` in this folder; `pip install -r requirements.txt`.
- Owner's shell shows `(base)` conda AND `(.venv)` — make sure `.venv` is active.
- IBKR: TWS paper port 7497, Gateway 4002. Live ports are refused in code.
- Commit style: plain descriptive messages, commit after each working increment.
