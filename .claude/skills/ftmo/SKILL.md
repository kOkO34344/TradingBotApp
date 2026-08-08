---
name: ftmo
description: How the FTMO venue works in TradingBotApp — the rule engine, equity monitor, position sizer, audit log and cTrader Open API adapter. Read before touching any ftmo_*.py file, before changing FTMO limits or buffers, or when asked about the Challenge rules, cTrader connectivity, or why the FTMO path uses a continuous monitor instead of RiskGuard.
---

# The FTMO venue

Five modules. `ftmo_rules.py` decides, `ftmo_monitor.py` watches,
`ftmo_sizing.py` sizes, `ftmo_audit.py` records why, `ftmo_service.py` talks to
the broker. Each has an offline `--selftest` needing no credentials and no
network; 465 checks total as of 2026-08-07, across these five plus
`ftmo_session`, `ftmo_signal`, `ftmo_runner` and `trade_journal`.

**Rule 9 in `CLAUDE.md` governs this venue and is not restated here — read it
first.** Every module's own docstring carries its full rationale; this file is
the map and the cross-cutting invariants.

## Agreed configuration (owner decisions, 2026-08-02)

| | |
|---|---|
| Platform | cTrader Open API (MT5's Python package is Windows-only) |
| Account | FTMO Free Trial, $25,000 |
| Rules | 1-Step and 2-Step both encoded, switchable by config |
| Instruments | stock CFDs, indices, FX, commodities/crypto — all four |
| Signal | Kronos everywhere, each class gated on its own IC screen |
| Cadence | daily rebalance, multi-day holds |
| Buffer | 5% — stop opening at 4.75% daily / 9.5% drawdown |
| Limit action | block entries at soft, flatten at hard |
| Monitor | event-driven off the cTrader stream |
| Sizing | ATR model, 3% per trade, top_n 4, book capped at the soft limit |
| Stops | server-side, attached at entry, verified after fill |
| Records | `trade_journal.csv` + venue column, plus the separate audit log |
| Autonomy | fully unattended (see rule 9) |

Derived limits on $25,000 — check with `python3 ftmo_rules.py --show`.
**These move with `buffer_pct`; the numbers below are the live 0.05 setting,
not a constant.** At the previous 0.20 buffer the 2-Step soft/flatten pair was
$1,000/$1,125 daily and $2,000/$2,250 total — old notes quoting those are
describing a buffer that is no longer configured, not a changed rule.

```
2-Step   daily  hard $1,250   flatten $1,218.75   soft $1,187.50
         total  hard $2,500   flatten $2,437.50   soft $2,375.00   (static)
         target $2,500        min 4 trading days

1-Step   daily  hard   $750   flatten   $731.25   soft   $712.50
         total  hard $2,500   flatten $2,437.50   soft $2,375.00   (trailing)
         target $2,500        Best Day Rule active
```

**RISK RAISED 2026-08-09 — owner decision, and the evidence position was
stated first.** `risk_pct` 1.0 -> **3.0** ($750/trade on $25k) and `buffer_pct`
0.20 -> **0.05**, to reach the profit target faster. Kronos has no demonstrated
edge on any asset class this project has measured (IC ~0, hit rate ~50% on all
four classes at both horizons), so this scales variance without scaling
expected return — it does not make the account more likely to pass, it makes it
reach one of the two ends sooner, and only one of those ends is permanent. That
was put to the owner with the per-option arithmetic and chosen anyway. Record it
that way; do not rationalise it as a validated improvement, and do not quietly
revert it either.

Two consequences that are properties of the new numbers, not opinions:

- **The reserve is now $31.25.** Our flatten tier sits at $1,218.75 against
  FTMO's $1,250 cliff. The buffer exists for slippage and gaps — at 0.05 there
  is roughly one tick of index-CFD slippage between "we flatten" and "the
  challenge is failed". A stop that gaps through, which this project has
  already seen once (GOOGL, 1.5% beyond its level), clears that gap easily.
- **A firing is now 1 full position + 1 truncated one.** At $750/trade against
  a $1,187.50 book cap: entry 1 takes $750.06, entry 2 is capped to $437.44,
  entry 3 is refused with "no portfolio risk budget left today". `top_n` stays
  4 deliberately — the surplus candidates cost nothing and give the budget
  somewhere to go when a higher-ranked name is skipped for a negative forecast.

## The invariant that shapes everything

**Every FTMO limit is measured on equity INCLUDING floating P&L.** The account
can fail with no order placed and nothing realised — an overnight gap on a
position already held is sufficient.

This is why the FTMO path gets a continuous monitor rather than a pre-trade
gate. `RiskGuard.check()` reads IBKR's realised `RealizedPnL` and only when an
order is being placed; it provably cannot see this. The 2026-07-23 GOOGL
stop-out moved the IBKR account $422 with nothing running and was invisible for
two days. On a $25,000 FTMO account with a $1,250 hard daily limit, the same
blind spot is a failed account rather than a reporting gap.

## `ftmo_rules.py` — the decision

Three questions that must never be conflated: may I **OPEN**, must I
**FLATTEN**, could this phase **PASS**. The third is not a trading permission —
too few trading days, or a best day breaking the consistency rule, means keep
trading, not stop. Modelling it as a block would be exactly backwards.

Each published limit becomes three thresholds, because stopping exactly at
FTMO's number leaves nothing for slippage or a gap:

```
soft    = limit x (1 - buffer)       stop OPENING
flatten = limit x (1 - buffer/2)     CLOSE EVERYTHING
breach  = limit                      already failed
```

The 1-Step trailing floor moves ONLY in `roll_day()`, at the 00:00 CE(S)T
boundary, off the day's **closing** balance. Ratcheting it on intraday equity
would tighten the limit using profit that was never kept. There is a test with
a 26,800 intraday spike closing at 25,400 asserting the mark goes to 25,400.

Day boundaries use `Europe/Prague` via `zoneinfo`, never a hardcoded offset and
never host time. A naive datetime is refused rather than assumed.

## `ftmo_monitor.py` — the watching

Pure state machine over OK / BLOCKED / UNKNOWN / FLATTEN / BREACHED. Four
properties not to regress:

1. **Edge-triggered.** Actions fire on posture change, not per tick.
   Level-triggering would emit one flatten instruction per tick.
2. **Stale quotes are UNKNOWN, never safe.** `floating_pnl()` returns `None`,
   deliberately not `0.0`. Blocks at 10s, flattens at 60s — a 2s blip is not a
   reason to liquidate, 60s blind on a leveraged book is. Same reasoning as
   `OpenOrderStateUnknown` and the empty `ib.positions()` bug.
3. **Floating P&L marks at the exit side of the spread** — long at bid, short
   at ask. Marking at the mid flatters equity by half a spread per position,
   the unsafe direction on a limit measured in equity.
4. **`heartbeat()` is the only thing that notices SILENCE**, because every
   other entry point is driven by an arriving message.

**A position with no quote yet is aged from its OPEN, not treated as
infinitely stale.** The execution event that registers a position arrives
before the first spot tick for its symbol; treating that as infinite age made
the monitor emit FLATTEN at the instant every position opened, which in
production would have liquidated every new entry. Found 2026-08-02 by replaying
a synthetic breach day end-to-end — the unit tests all happened to quote a
symbol before asserting on posture, so none of them caught it. There is now a
regression test. **Worth generalising: drive these modules together against a
realistic sequence before trusting the unit tests.**

## `ftmo_sizing.py` — the size

Two constraints, both enforced: risk at the stop <= `risk_pct` of equity
(**3.0** since 2026-08-09), AND the sum of every open position's risk stays
inside the daily soft limit, so a simultaneous stop-out across the whole book
lands ON $1,187.50 rather than through $1,250.

**The two constraints no longer divide evenly, and that is the visible sign the
risk was raised.** At 1% four positions filled the budget exactly; at 3% the
book cap is reached partway through the second, so a firing produces one
full-size position and one truncated one. A `capped by portfolio budget` reason
on entry 2 and a `no portfolio risk budget left today` refusal on entry 3 are
now the NORMAL output — do not read either as a defect.

Not a copy of `paper_trader.size_position()` — a "unit" means something
different per instrument (100,000 base units per FX lot, an ounce of gold, a
share of a stock CFD, an index point), and the quote currency is frequently not
the account currency. **`quote_to_account_rate` has NO default** and a
non-positive one is refused rather than treated as 1.0; the inverted
`ExchangeRate` that misstated equity by ~29% is the precedent.

Two behaviours that look like bugs and are not:

- **Flooring to the volume step absorbs float residue in our favour.**
  `1.0850 - 1.0750` is `0.010000000000000009`, so a "100 pip" stop computes a
  hair wide and the size a hair small — 24,999 units risking 249.99 rather than
  25,000 risking 250.00. The error can only reduce risk. Do NOT "fix" it by
  rounding to nearest.
- **When a symbol's MINIMUM volume already risks more than the budget, the
  order is refused with volume 0** rather than placed slightly oversized. On a
  $25,000 account this was real for wide-ATR index CFDs at the old $250 per
  trade; at $750 fewer symbols hit it, so **raising the risk quietly widened
  the tradeable universe** — a side effect of the 2026-08-09 change worth
  knowing, since a symbol that was previously self-refusing can now size.

Rounding residue leaves a few cents of budget after the book cap is reached,
enough for `size_position` to accept a micro extra position. That function is
budget-only by design; the position COUNT cap lives in `plan_entry`. Both are
tested.

## `ftmo_audit.py` — the record

`trade_journal.csv` records what was DONE; this records **why it was allowed**.
Append-only JSONL, one file per FTMO day (`ftmo_audit/YYYY-MM-DD.jsonl`,
gitignored), aligned to 00:00 CE(S)T so the day of a breach is one file.

Logs transitions and decisions, NOT every evaluation — at tick rate that would
be millions of lines a day and would bury the four that matter. A rate-limited
snapshot keeps a trail on quiet days so silence is provably "nothing happened"
rather than "the monitor was dead".

**A write failure is swallowed and counted (`write_failures`), never raised.**
That inverts rule 6 for this file only: an exception out of a logging call
could prevent a FLATTEN from executing, and losing an audit line is strictly
less bad than failing to close a breaching position. A torn final line from a
killed process is flagged `UNPARSEABLE`, not fatal.

`python3 ftmo_audit.py --report [YYYY-MM-DD]` replays a day.

## `ftmo_service.py` — the broker

cTrader Open API, protobuf over TLS. OAuth (browser consent on a localhost
callback), application and account auth, and a read-only `--probe` listing
accounts, balance, open positions with stop state, and the symbol universe.

```
python3 ftmo_service.py --authorize    # one-time, writes tokens to .env
python3 ftmo_service.py --probe        # read-only, places nothing
python3 ftmo_service.py --refresh      # new access token from the refresh token
```

Credentials live in a gitignored `.env` at mode 600 — never logged, never
printed, never interpolated into a shell command. `save_env_value()` rewrites
keys in place so re-authorising cannot leave two conflicting
`CTRADER_ACCESS_TOKEN` lines where the last silently wins.

**Twisted vs asyncio:** the SDK is Twisted while `ib_async` and the FastAPI
backend are asyncio. They can share a process only via Twisted's asyncio
reactor, and it must be installed before anything imports the default reactor —
`install_asyncio_reactor()` does that. The CLI paths run standalone and are
safe today; anything importing this into the web backend must call it first.

**The dependency pins are load-bearing.** `ctrader-open-api` hard-pins
protobuf==3.20.1, Twisted==24.3.0, pyOpenSSL==24.1.0. See the comments in
`requirements.txt` before changing any of them — particularly the note on why
yfinance must NOT be dropped to "resolve" the protobuf downgrade.

## Connection status — LIVE as of 2026-08-05

The Open API app is `Active`, OAuth is complete and the venue is reachable end
to end. `--probe` and `--symbols` both pass read-only:

```
ctidTraderAccountId 48137229   login 17166058   broker ftmo
balance 25,000.00 USD   leverage 100x   HEDGED   FULL_ACCESS
0 open positions   0 pending orders   202 tradeable symbols
```

**`CTRADER_HOST=live` is required and is not a rule 1 breach.** cTrader routes
by endpoint and FTMO issues Challenge/Trial accounts on its LIVE server with
SIMULATED capital, so the account is live-*type* while the money is not real.
The wrong endpoint returns a bare `CANT_ROUTE_REQUEST` naming neither account
nor endpoint; `select_account()` now refuses that before account auth and says
which value to set. Do not switch back to demo — the account is not there.

The access token lasts ~30 days. `python3 ftmo_service.py --refresh` renews it;
`--probe` prints the remaining life on every run.

### Symbol specs are captured, not invented

`python3 ftmo_service.py --symbols` writes all 202 real `ProtoOASymbol` specs
to `ftmo_symbol_specs.json`, which is **tracked in git** so `ftmo_sizing.py`'s
selftest can assert against real venue values while staying offline.
`spec_from_capture(name)` builds a `SymbolSpec` from it.

This mattered more than expected: **every spec the sizing tests had invented
was wrong**, EURUSD's min/step by 1000x (100 → 100,000), XAUUSD's by 100x,
US30.cash's digits by one place. The risk maths had been validated against
numbers the venue never reports. The selftest now sweeps all 202 real symbols
asserting no accepted size ever exceeds the budget.

Note `ProtoOASymbolsListReq` returns only `ProtoOALightSymbol` and carries no
volume or lot data at all — the specs come from `ProtoOASymbolByIdReq`.

### Server-side stops: VERIFIED 2026-08-05

This used to be the venue's one load-bearing assumption. It is not an
assumption any more. `ftmo_smoke_order.py --confirm` placed one real
minimum-size trade on the live trial (BTCUSD, position 9822997, 0.01 units,
risk $29.72), read it back as `stopLoss 61,784.01  protected: True`, closed it,
and left the account flat — $0.40 of spread for the whole proof. Re-run it any
time the order path changes.

It still remains true that all four asset classes failed their IC screen
(rule 9), so the path works and has no class with measured edge to fire on.

## The unattended runner — `ftmo_runner.py` (2026-08-06)

The FTMO counterpart to `autotrade_runner.py`. Full operational detail is in
CLAUDE.md's "FTMO autotrade" section; the parts that matter when touching the
modules in this skill:

- **It is ARMED as of 2026-08-06** and scheduled by `com.tradingbotapp.ftmo`.
  This is no longer a thing that might run — it runs.
- **The launchd schedule is a SUPERSET, not the window.** launchd wakes it
  hourly at :30 all day; the real window is 16:30-11:30 next morning, every
  day except Sunday, Europe/Sofia, enforced by
  `within_trading_window()` — that function is authoritative. The window
  wraps midnight so it is a UNION (`t >= OPEN or t <= CLOSE`), never a range;
  "except Sunday" applies to the Sofia calendar day, so Saturday evening runs
  and Sunday morning does not. Checked before the audit log opens and before
  torch is imported, so out-of-window wakeups are free.
- It is armed by `ftmo.autotrade.enabled`, **its own toggle**. IBKR's
  `autotrade.enabled` cannot arm it, and there is a selftest asserting that.
  Disarm from `/ftmo`, or unload the launchd job.
- It calls `ftmo_signal.plan_orders()` — and so does the web preview
  (`api/ftmo_api.plan`). Keep it that way. The moment the browser computes a
  rank, a size or a stop of its own, there are two implementations of the risk
  maths and one will eventually be wrong.
- `ftmo_runner_state.json` carries the day-start balance and trailing
  high-water mark between one-shot invocations. **Without it the daily limit
  evaluates against the current balance every run and can never trip.** If you
  change `AccountState`'s fields, change `RunnerState` with them.
- FLATTEN is decided before any forecast runs, and `flatten_all()` has no rule
  engine, sizer or limit in front of it (rule 3).

Verified end to end against the live venue in dry-run on 2026-08-06: 14/14
symbols passed `assert_bars_match_quote`, Kronos forecast in ~90s, rule engine
OPEN OK, four entries sized to $994.71 total risk — inside the then-$1,000
daily soft limit. Nothing was placed. **That run predates the 2026-08-09 risk
change**, so its four-entries-at-$250 shape is history, not the current
expected output; see the sizing section for what a firing looks like now.
