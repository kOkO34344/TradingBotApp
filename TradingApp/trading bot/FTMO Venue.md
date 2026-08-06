---
tags: [ftmo, execution, infrastructure, risk, prop-firm]
status: "LIVE AND ARMED 2026-08-06 — unattended Kronos trading, launchd 01:15 daily. No asset class passed its IC screen; running anyway is a recorded exception to rule 5."
source: ftmo_rules.py, ftmo_monitor.py, ftmo_sizing.py, ftmo_audit.py, ftmo_service.py, ftmo_session.py, ftmo_signal.py, ftmo_runner.py, trade_journal.py
last_updated: 2026-08-06
---

# FTMO Venue

**Owner decision, 2026-08-02: FTMO becomes the trading venue and IBKR is
retired in place.** IBKR places no new orders but keeps monitoring its three
open positions until they close naturally — see [[IBKR Integration]].

This is a genuinely new direction for the project, and it deserves recording
honestly rather than as a natural next step. The evidence position at the
moment of the decision: **zero graded calls**, Kronos measured IC ~0 on the
only screens ever run, and no IC screen at all for indices, FX or commodities.
The bot is being pointed at a 3–5% daily loss limit with no measured edge. The
owner asked for it with that stated, twice.

## Why cTrader and not MetaTrader

The official `MetaTrader5` Python package is **Windows-only** and has no
distribution for this Apple Silicon Mac — verified, not assumed. There is no
Parallels, VMware, UTM, CrossOver, Wine, Docker or QEMU on the machine. So the
usual prop-firm path (Python driving an MT5 terminal) would have required a VM
or a rented VPS, and the bot would have stopped running locally.

cTrader Open API is protobuf over TLS and runs natively here. FTMO supports it
and issues a cTrader ID in the Client Area. FTMO discontinued DXtrade in March
2026, so the real choice was MT4/MT5/cTrader.

## The rules being encoded

Both products, switchable by config. From FTMO's own trading-objectives page:

| | 2-Step | 1-Step |
|---|---|---|
| Profit target | 10%, then 5% (Verification) | 10% |
| Max daily loss | **5%** | **3%** |
| Max loss | 10%, **static** | 10%, **end-of-day trailing** |
| Min trading days | 4 per phase | none |
| Consistency | — | **Best Day Rule** (best day ≤ 50% of positive-day profit) |

**The invariant that shapes the whole design: every FTMO limit is measured on
equity INCLUDING floating P&L.** The account can fail with no order placed and
nothing realised — an overnight gap on a position already held is enough.

That is why this venue gets a **continuous monitor** rather than a pre-trade
gate. `RiskGuard.check()` reads IBKR's realised P&L and only when an order is
being placed; it structurally cannot see this. The 2026-07-23 GOOGL stop-out
moved the IBKR account $422 with nothing running and went unnoticed for two
days — see [[Risk Management System]]. On a $25,000 FTMO account with a $1,250
hard daily limit, that same blind spot is a failed account, not a reporting gap.

## Agreed configuration

| | |
|---|---|
| Account | FTMO Free Trial, $25,000 |
| Instruments | stock CFDs, indices, FX, commodities/crypto — all four |
| Signal | Kronos everywhere, **each class gated on its own IC screen** |
| Cadence | daily rebalance, multi-day holds |
| Buffer | 20% — stop opening at 4% daily / 8% drawdown |
| Limit action | block entries at soft, flatten at hard |
| Sizing | ATR model, 1% per trade, max 4 positions, book capped at the soft limit |
| Stops | server-side, attached at entry, verified after fill |
| Autonomy | **fully unattended** |

Derived limits on $25,000 (`python3 ftmo_rules.py --show`):

```
2-Step   daily  hard $1,250   flatten $1,125   soft $1,000
         total  hard $2,500   flatten $2,250   soft $2,000   (static)

1-Step   daily  hard   $750   flatten   $675   soft   $600
         total  hard $2,500   flatten $2,250   soft $2,000   (trailing)
```

Four positions at 1% fill the daily budget exactly, so a simultaneous stop-out
across the whole book lands **on** $1,000 rather than through $1,250.

## Unattended trading is the second exception to the evidence rule

The project's rule 5 says autonomy is earned by graded evidence.
`autotrade_runner.py` was the first documented exception — see
[[Autotrade (Experimental)]]. FTMO running fully unattended is the second, and
it should be flagged the same way rather than treated as precedent.

What autonomy removes is the **human approval step**, never a limit. The rule
engine, the equity monitor and the sizer are enforced regardless. Kronos may
only trade an asset class that has passed its own IC screen — the owner's own
condition, and the thing that keeps this from being "enable everything and
hope".

The Challenge account is **simulated**, so this does not breach the
paper-before-real-money rule. The real exposure is the entry fee. Phase 4 (real
capital on IBKR) stays locked.

## The modules

All pure-logic except the transport ones, all with offline `--selftest` needing
no credentials and no network. **426 checks total** as of 2026-08-06:
`ftmo_rules` 70, `ftmo_sizing` 72, `ftmo_monitor` 63, `ftmo_audit` 48,
`ftmo_service` 43, `ftmo_session` 40, `ftmo_runner` 38, `ftmo_signal` 26,
`trade_journal` 26.

> [!warning] `ftmo_audit`'s selftest prints `AUDIT WRITE FAILED` to stderr on
> purpose, while testing an unwritable path. That is a **passing** test. Do not
> let a grep for "FAILED" convince you the suite is broken — it fooled me once.

- **`ftmo_rules.py`** — the decision. Answers three questions that must never
  be conflated: may I OPEN, must I FLATTEN, could this phase PASS. The third is
  *not* a trading permission — too few trading days means keep trading, not
  stop.
- **`ftmo_monitor.py`** — the watching. Edge-triggered state machine over
  OK / BLOCKED / UNKNOWN / FLATTEN / BREACHED.
- **`ftmo_sizing.py`** — the size. Per-trade *and* per-portfolio caps.
- **`ftmo_audit.py`** — the record. Append-only JSONL, one file per FTMO day,
  recording *why* a decision was allowed. `trade_journal.csv` records what was
  done; this records the reasoning behind it.
- **`ftmo_service.py`** — the broker. cTrader Open API, OAuth, read-only probe.
  One-shot: it **cannot trade**.
- **`ftmo_session.py`** — the long-lived connection. Streaming quotes,
  trendbars, orders. This is the one that can actually place something.
- **`ftmo_signal.py`** — turns a Kronos ranking into sized, stop-protected
  orders. The join between the research side and the venue side.
- **`ftmo_runner.py`** — the unattended runner. See below.
- **`trade_journal.py`** — the journal's column set and the `venue` column,
  shared with the IBKR side so both venues cannot drift apart on the schema.

## Three things worth remembering

**Stale data is not safe data.** If quotes stop arriving for a held symbol,
equity is UNKNOWN — not unchanged. The monitor blocks entries at 10s of silence
and flattens at 60s. This project has made the opposite mistake twice already
(an empty `ib.positions()` read as "flat" manufactured a phantom liquidation),
so it is treated as a known failure mode rather than a theoretical one.

**The one real bug so far was found by integration, not unit tests.** A
position is registered from its execution event, which arrives *before* the
first price tick for its symbol. The monitor treated "no quote yet" as
infinitely stale and emitted FLATTEN at the instant every position opened — in
production it would have tried to liquidate every new entry. All 56 unit tests
passed; it surfaced only when the modules were driven together against a
realistic sequence. **Do an integration pass before trusting the unit tests.**

**Float residue works in our favour, deliberately.** `1.0850 - 1.0750` is
`0.010000000000000009`, so a "100 pip" stop computes a hair wide and the
position a hair small. Flooring to the volume step means the error can only
ever *reduce* risk. Rounding to nearest would break that.

## Connected — 2026-08-05

The app went **Active**, OAuth completed, and the venue is reachable end to
end. `--probe` and `--symbols` both pass read-only:

```
ctidTraderAccountId 48137229   login 17166058   broker ftmo
balance 25,000.00 USD   leverage 100x   HEDGED   FULL_ACCESS
0 open positions   0 pending orders   202 tradeable symbols
```

The access token lasts ~30 days; `--refresh` renews it and `--probe` prints the
remaining life every run.

### `CTRADER_HOST=live` is required, and it is not a rule 1 breach

cTrader routes **by endpoint**: a live-type account authenticates only on
`live.ctraderapi.com`, a demo-type account only on the demo host. FTMO issues
its Challenge and Free Trial accounts on its **live** cTrader server with
**simulated** capital, so the trial is legitimately live-*type* while the money
is not real. `isLive` is a routing flag, not a claim about capital.

Get it wrong and the venue says only `CANT_ROUTE_REQUEST: Cannot route
request` — four words naming neither the account nor the endpoint, arriving
immediately *after* a successful application auth and a successful account
list. It reads exactly like a token-scope problem and is not.
`select_account()` now refuses the mismatch before sending account auth and
names the value to set.

### Every invented symbol spec was wrong

The first real capture showed the sizing tests had been validated against
numbers the venue never reports:

| symbol | field | invented | real |
|---|---|---|---|
| EURUSD | min/step volume | 100 | **100,000** |
| XAUUSD | min/step volume | 1 | **100** |
| US30.cash | min/step volume | 10 | **1** |
| US30.cash | digits | 1 | **2** |

`python3 ftmo_service.py --symbols` now captures all 202 real specs to
`ftmo_symbol_specs.json`, which is **tracked in git** so the sizing selftest
asserts against real venue values while staying offline. The selftest sweeps
every one of the 202 asserting no accepted size ever exceeds the risk budget.

Worth noting for anyone reading the code: `ProtoOASymbolsListReq` returns only
*light* symbols and carries no volume or lot data at all. The numbers that
matter come from `ProtoOASymbolByIdReq`.

## What the evidence says the venue may trade: nothing

All four asset classes were IC-screened on 2026-08-03 and **all four failed** —
see [[Kronos Research Agent]] for the table. The matched momentum baseline
failed all four as well, so this is not Kronos losing to a better alternative:
nothing works on any of these classes at this cadence.

Until 2026-08-06 that gate held and the venue traded nothing. **It is now
trading anyway**, by explicit owner decision — see the third-exception section
above. The gate was not overridden by a config change or quietly re-run until
something passed; it was overridden knowingly, with the evidence stated, and
that is the honest way to record it.

The traded basket is USD-quoted only, 14 symbols across all four classes. On
the runs so far the selection has been three crypto plus natural gas — a
concentrated, high-volatility set, which is what ranking a mixed universe by
predicted return produces when crypto is the volatile end of it.

## Server-side stops: VERIFIED 2026-08-05

This was the venue's one load-bearing assumption and it is now settled.
`ftmo_smoke_order.py --confirm` placed one real minimum-size trade on the live
trial and read it back from the venue:

```
position 9822997  BTCUSD BUY 0.01u
entry 64,755.88   stopLoss 61,784.01   protected: True
placed -> read back -> closed -> flat.   cost $0.40 of spread
```

The whole risk model rested on this. Forty cents was a cheap price for turning
an assumption into a fact.

## Armed and unattended — 2026-08-06

`ftmo_runner.py` is the unattended path: connect → rule engine → FTMO's own
daily bars → Kronos → rank → plan → **exits, then entries** → verify every stop
by reading the venue back → journal + audit + Telegram.

It is **armed** (`ftmo.autotrade.enabled = true`) and scheduled via
`com.tradingbotapp.ftmo` at **01:15 local, once per day**. Not hourly: the
cadence is a daily rebalance on a 20-day forecast, and firing hourly would
re-decide a 20-day view 24 times a day and pay spread on sampling noise each
time. 01:15 local sits just after the Europe/Prague FTMO day boundary, so the
day-start balance rolls on settled numbers.

Verified end to end in dry-run before arming, placing nothing: 14/14 symbols
passed the bar/quote scaling cross-check, Kronos forecast in ~90s, rule engine
OPEN OK, four entries totalling **$994.71** of risk — inside the $1,000 daily
soft limit. That is the portfolio cap working, not a coincidence.

Four properties not to regress:

1. **FLATTEN is decided before any forecast runs**, and `flatten_all()` has no
   rule engine, sizer or limit in front of it. A limit caps NEW exposure;
   blocking an exit raises risk. This project already made the opposite
   mistake — see [[Risk Management System]].
2. **An equity it cannot fully price is not an equity it trades on.** A
   position with no quote blocks new entries rather than being marked flat.
3. **Stops are verified by reading the venue back**, never from the fact that
   an order was sent. A rejected cTrader order arrives as an *event*, not an
   error response — the first live FTMO order was refused while the code
   cheerfully reported `{'sent': True}`.
4. **torch imports only after the enabled check**, so a disarmed firing is a
   cheap settings read rather than a 2 GB model load.

### `ftmo_runner_state.json` is why the daily limit works at all

The FTMO daily limit is measured against the balance at 00:00 CE(S)T, and the
1-Step trailing floor moves off a completed day's *closing* balance. A one-shot
script cannot know either without remembering. Without this file the limit
would evaluate against the current balance every run — a daily loss of 0.00
forever, i.e. **a limit that can never trip**.

It is gitignored deliberately: it belongs to *this* machine's account, and a
checkout elsewhere inheriting a day-start balance from an account it is not
connected to is exactly how the limit would evaluate against a fiction. The
first run seeds day-start from the **live balance**, not from
`initial_capital` — seeding $25,000 onto a $24,300 balance would invent a $700
loss that never happened and could block trading on the spot.

## This is the THIRD exception to the evidence rule

Rule 5 says autonomy is earned by graded evidence. The exceptions, in order:

1. `autotrade_runner.py` — see [[Autotrade (Experimental)]]
2. the FTMO path running fully unattended (rule 9)
3. **Kronos actually firing on FTMO with no class having passed a screen**

All four asset classes were IC-screened on 2026-08-03 and all four failed
(|t| ≤ 1.55 in every direction); the matched momentum baseline failed all four
too. The original condition was "Kronos may only trade a class that passed its
own IC screen". **No class passed, and it is trading anyway**, on the owner's
explicit instruction of 2026-08-05 given with the evidence stated first.

Record it that way. It is not precedent and it is not a validated strategy.
What autonomy removes is the human approval step — never a limit.

## Next steps

1. ~~`--authorize` then `--probe`~~ — done 2026-08-05, both pass.
2. ~~Bind real symbol specs~~ — done 2026-08-05, see above.
3. ~~Prove server-side stops attach at entry~~ — done 2026-08-05, PASSED.
4. ~~Migrate `trade_journal.csv` to add a `venue` column~~ — done 2026-08-06;
   the migration lives in the writer and self-heals, see below.
5. ~~Build the signal→order path and drive all the modules together~~ — done
   2026-08-06, `ftmo_runner.py`, verified live in dry-run.
6. **Watch the first real firings.** Nothing here has ever placed an order
   unattended. Check `ftmo_launchd.log`, the Telegram messages, and the
   `venue=ftmo` rows in `trade_journal.csv` after 01:15.
7. The evidence gap is unchanged and is now the *only* thing outstanding: no
   class has measured edge. Closing it needs research cycles, not code.

## The `venue` column, and the silent corruption it nearly caused

`trade_journal.csv` had to gain a `venue` column before an FTMO order could be
recorded at all (rule 6). The trap is worth remembering because nothing would
have complained:

`journal()` wrote the header **only when the file did not exist**. So extending
the column list alone would have appended 12-value rows under an 11-column
header — and every reader is a `csv.DictReader`, which quietly drops the extra
value into the `None` restkey and **reports no error**. Silent corruption, in
the one file the project's whole audit trail depends on.

The fix puts the migration in the writer (`trade_journal.py`), running once, in
place, before appending. Self-healing rather than "remember to run the script
first" — same reasoning as `secrets_store.resolve()` falling back to the legacy
paths: both writers are unattended, so a half-applied migration must degrade to
"still works". It backs up, verifies the read-back field-by-field *before*
replacing the original, refuses a header it does not recognise, and is
idempotent.

Run against the live journal 2026-08-06: 46 rows, every one identical across
all 11 original columns, all backfilled `venue=ibkr`, no restkey leak,
`api/journal_api.py` still reading it. `append()` now **requires** an explicit
venue — an unlabelled row cannot be reconciled against either broker later.

## Credentials

`secrets/ctrader.env`, mode 600, gitignored — see [[Risk Management System]]
and `secrets_store.py`. Audited 2026-08-05 against the full git history: no
credential has ever been committed.

## Related

- [[IBKR Integration]] — the retired venue, still monitoring three positions
- [[Risk Management System]] — RiskGuard, and why it cannot do this job
- [[Kronos Research Agent]] — the signal being pointed at this
- [[Autotrade (Experimental)]] — the first exception to the evidence rule
- [[Phase Milestones Dashboard]] — where this sits overall
