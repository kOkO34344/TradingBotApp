---
tags: [ftmo, execution, infrastructure, risk, prop-firm]
status: "LIVE AND ARMED — the ONLY venue since 2026-08-09. Every 15 min inside a 16:30-23:00 Sofia window, Mon-Fri. DAILY LOSS LIMIT BREACHED 2026-08-11; entries refused since. No asset class passed its IC screen; running anyway is a recorded exception."
source: ftmo_rules.py, ftmo_monitor.py, ftmo_sizing.py, ftmo_audit.py, ftmo_service.py, ftmo_session.py, ftmo_signal.py, ftmo_runner.py, ftmo_closes.py, ftmo_watch.py, trade_journal.py
last_updated: 2026-08-12
---

# FTMO Venue

> [!danger] BREACHED — the daily loss limit, 2026-08-11
> The rule engine's own reading, repeated on every firing since 10:31 that
> day: **`BREACH: daily loss 1,294.78 >= limit 1,250.00`**, and
> `NO NEW ENTRIES: daily loss 1,294.78 >= soft limit 1,237.50`. Day-start
> balance was 24,721.03.
>
> Four positions closed on 2026-08-11 and the account has been **flat**
> since — EURUSD on its take-profit (gross −93.01), then SOLUSD (−48.05),
> NATGAS.cash (−629.97) and US500.cash (−12.82), all detected by
> `ftmo_closes.py` rather than placed by the runner. Every firing after that
> journalled `BLOCKED`. **The runner is still armed and still firing**; it is
> the rule engine, not the switch, that is holding it.
>
> Read this the right way round. The limit **worked** — it refused entries and
> never once blocked an exit. What failed is upstream of it: a configuration
> that left **$6.25** between our flatten tier and FTMO's hard cliff, set the
> same day, on a book of leveraged CFDs. The capital is simulated.

> [!important] The only venue, as of 2026-08-09
> IBKR was removed entirely. **Thirteen modules carry 762 offline selftests**
> (re-measured 2026-08-11 — the "579 across ten modules" figure this note
> carried did not reproduce) and none of them need credentials or a
> connection.
>
> **Schedule vs window are different things.** launchd wakes the runner **every
> 15 minutes at :00 :15 :30 :45, all 24 hours, every day** — a deliberate
> superset. The real window is **16:30–23:00 Europe/Sofia, MONDAY TO FRIDAY**
> (the 09:30–16:00 New York cash session), enforced by
> `within_trading_window()`, which is authoritative. **27 firings per weekday**,
> both endpoints inclusive.
>
> **The screens were re-run at a 5-day horizon on 2026-08-08 and all four
> failed again** (indices +0.052, FX −0.064, commodities −0.017, crypto +0.103;
> max |t| 1.45). Shortening the horizon moved every IC toward zero or left it
> put.


**Owner decision, 2026-08-02: FTMO becomes the trading venue and IBKR is
retired in place.** IBKR's code was removed entirely a week later, on
2026-08-09, with three positions presumed still open and unverifiable — see
[[IBKR Integration]]. Nothing in this project monitors them any more.

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
| Instruments | stock CFDs, indices, FX, commodities/crypto — all five classes the account carries |
| Signal | Kronos everywhere, **each class gated on its own IC screen** — a gate now overridden |
| Cadence | every 15 min, 16:30–23:00 Sofia, Mon–Fri; 5-day forecast horizon |
| Buffer | **1%** (`buffer_pct` 0.01, cut from 0.05 on 2026-08-11) |
| Limit action | block entries at soft, flatten at hard |
| Sizing | ATR model, **1.65% per trade**, `top_n` 5, `max_per_class` 3, book capped at the soft limit |
| Stops | server-side, attached at entry, verified after fill; **take-profit too since 2026-08-08** |
| Autonomy | **fully unattended** |

### The 2026-08-11 risk change, and why it went the way it did

The ask was "bigger risks". The arithmetic was put first and changed the shape
of the answer:

| | before | after |
|---|---|---|
| `risk_pct` | 3.0 | **1.65** |
| `buffer_pct` | 0.05 | **0.01** |
| `top_n` | 4 | **5** |
| `max_per_class` | 1 | **3** |
| daily soft / flatten | 1,187.50 / 1,218.75 | **1,237.50 / 1,243.75** |
| funded positions | ~1.6 | **exactly 3** |

**Raising `risk_pct` alone does not raise risk here** — `size_position` takes
`min(per_trade_risk, budget_remaining)` and the whole book is capped at the
daily soft limit, so at 5% the first entry would eat the entire budget and
every later one would be refused. That was already visible in the live journal:
on 2026-08-10 the runner sized SOLUSD $745, LTCUSD $349, then NATGAS **$0.92**.

So the lever that moved was the daily **ceiling**, not the per-trade percentage.
Per-trade risk went *down*, total daily exposure went *up*, and the book got
wider rather than more concentrated.

> [!danger] The reserve is $6.25, and the account cleared it the same day
> $6.25 is what now sits between our flatten tier (1,243.75) and FTMO's
> $1,250 cliff. That is well under one tick of index-CFD slippage, and a stop
> that gaps through — which this project has already watched happen, GOOGL
> filling 1.5% beyond its level — clears it without noticing.
>
> This is why `ftmo_monitor` grew early warnings at 50% and 75% of the daily
> budget on the same day: with soft, flatten and breach inside $12.50 of each
> other, the first posture change you hear about is otherwise effectively the
> last one.
>
> **It was breached on 2026-08-11, the day it was set** — final reading
> 1,294.78, i.e. $44.78 past the hard limit. One observation, not a verdict on
> the design, but it is the observation the design was most exposed to.

Derived limits on $25,000, 2-Step, **as the live rule engine actually printed
them in `trade_journal.csv` on 2026-08-11**:

```
daily   hard $1,250.00   flatten $1,243.75   soft $1,237.50
```

The total-loss tier and the 1-Step figures move with `buffer_pct` too; run
`python3 ftmo_rules.py --show` for the current set rather than trusting the
older numbers this note used to carry (hard 1,250 / flatten 1,125 / soft 1,000,
which were the 20%-buffer values).

Three positions at 1.65% fill the daily budget exactly, so a simultaneous
stop-out across the whole book lands **on** $1,237.50 rather than through
$1,250 — provided no stop gaps.

## Unattended trading is the second exception to the evidence rule

The project's rule 5 says autonomy is earned by graded evidence.
`autotrade_runner.py` was the first documented exception — see
[[Autotrade (Experimental)]]. FTMO running fully unattended is the second, and
it should be flagged the same way rather than treated as precedent.

What autonomy removes is the **human approval step**, never a limit. The rule
engine, the equity monitor and the sizer are enforced regardless. Kronos may
only trade an asset class that has passed its own IC screen — the owner's own
condition, and the thing that kept this from being "enable everything and
hope". **That condition was overridden on 2026-08-05 and the path was armed on
2026-08-06** — see the third-exception section below. It is recorded here as
written because the condition existing, and then being knowingly set aside, is
the honest sequence; do not read this paragraph as a gate still holding.

The Challenge account is **simulated**, so this does not breach the
paper-before-real-money rule. The real exposure is the entry fee. Phase 4 (real
capital) stays locked and is not reachable from any code path that exists.

## The modules

All pure-logic except the transport ones, all with offline `--selftest` needing
no credentials and no network. **762 checks total**, re-measured 2026-08-11:
`ftmo_runner` 115, `ftmo_signal` 102, `ftmo_sizing` 90, `ftmo_monitor` 80,
`ftmo_session` 70, `ftmo_rules` 70, `ftmo_audit` 48, `ftmo_watch` 43,
`ftmo_service` 43, `ftmo_closes` 43, `trade_journal` 26, `indicators` 20,
`secrets_store` 12.

> [!warning] The previous figures in this note were wrong, and had drifted
> before anyone noticed
> This note carried "426 as of 2026-08-06" and then "579 across ten modules";
> neither reproduced. `ftmo_signal` was recorded as 35 and was really 73,
> `ftmo_sizing` as 81 and was really 90, and `indicators` and `secrets_store`
> were omitted entirely. Recorded as a correction rather than quietly
> overwritten — a count nobody re-measures is the same class of thing as the
> CI workflow `CLAUDE.md` claimed for weeks and never had.
>
> Re-measure with, and note the two output formats — `indicators.py` prints
> `PASS`, everything else prints `ok`, so a sweep matching only `ok` scores it
> zero:
>
> ```
> for m in $(grep -l -- --selftest *.py); do .venv/bin/python3 $m --selftest \
>   | grep -cE '^\s+(ok|FAIL|PASS)'; done
> ```

> [!warning] `ftmo_audit`'s selftest prints `AUDIT WRITE FAILED` to stderr on
> purpose, while testing an unwritable path. That is a **passing** test. Do not
> let a grep for "FAILED" convince you the suite is broken — it fooled me once.

- **`ftmo_rules.py`** — the decision. Answers three questions that must never
  be conflated: may I OPEN, must I FLATTEN, could this phase PASS. The third is
  *not* a trading permission — too few trading days means keep trading, not
  stop.
- **`ftmo_monitor.py`** — the watching. Edge-triggered state machine over
  OK / BLOCKED / UNKNOWN / FLATTEN / BREACHED. Gained early warnings at 50%
  and 75% of the daily budget on 2026-08-11, because the three tiers now sit
  inside $12.50 of each other. **It had never actually run until
  `ftmo_watch.py` existed** — see below.
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
- **`ftmo_closes.py`** (2026-08-08) — detects positions that closed **without**
  the runner. Two-tier: a live execution-event stream that catches almost
  nothing (the runner's session lives ~2 minutes an hour) and, doing the real
  work, a diff of `ftmo_runner_state.json` against what the venue reports.
  Better positioned than the IBKR version ever was, because cTrader returns
  the actual closing DEAL — price, gross profit, swap and commission — so an
  unattended close carries real P&L instead of a shrug. **All four of the
  2026-08-11 closes were found by this, not by the runner.**
- **`ftmo_watch.py`** (2026-08-11) — the DRIVER `ftmo_monitor.py` never had,
  and the second thing here that can place an order. **It closes only and can
  never open**: no signal, no sizer, no forecast, and it cannot import torch.
  Session-scoped — it exits at 23:00 Sofia, and `ftmo_watch.sh` holds one
  `caffeinate -i` for that lifetime, which is a battery decision as much as a
  trading one. See the fourth-exception section below.
- **`trade_journal.py`** — the journal's column set and the `venue` column.
  Extracted from the IBKR adapter before that venue was removed, which is why
  the audit trail did not have to move when it went. The 46 `venue=ibkr` rows
  stay forever.

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

### The universe is the whole account now, not a basket (2026-08-11)

Owner instruction: forecast everything the FTMO account can actually trade.
The runner ranks **101 symbols across five classes** — commodities 16, crypto
30, FX 5, indices 4, **stocks 46** — derived from the venue's own capture by
`ftmo_signal.universe_from_capture`. Until this change it used a hand-written
basket of **14**. Six symbols are skipped every run for short history and are
named in the log; **95 of 101 are forecast**.

**Ranking 101 symbols by predicted percentage return systematically selects
the most volatile instruments in the account, and here that means micro-cap
alt-coins.** The first dry-run's top four were GALUSD +47.67%, VECUSD +25.51%,
IMXUSD +18.11%, MANUSD +16.65% — every one a crypto priced in fractions of a
cent, ahead of every index, every FX pair and all 46 stock CFDs. A five-day
forecast of +47% is not a forecast; it is the noisiest series in the set
winning a contest scored on amplitude. The 14-symbol basket had the same bias
and bounded it by construction. Thirty cryptos do not.

**Fixed the same day by ranking WITHIN asset class.** `cap_per_class()` allows
each class at most `max_per_class` candidates, so the pool becomes the class
leaders. It is a **filter on the candidate pool, not a re-scoring**, and that
choice is load-bearing: `apply_rotation_margin` compares raw predicted-return
differences against a margin calibrated to an observed ~1-point sampling
spread, so normalising into z-scores would have silently changed the units
that margin is measured in.

Three consequences worth knowing before reading a plan:

- **The pool, the boundary gap and the target all use the same capped list.**
  Measuring the rank N/N+1 gap on the full ranking while selecting from a
  capped one prints a number describing a decision nobody made.
- **A held position that is no longer its class's leader gets rotated out.**
  That is the cap working. Exits never consult it.
- A cycle now takes **~6.3 minutes, up from ~3**.

**This suppresses concentration; it does not create edge.** Every IC screen is
still ~0, and picking the best of a bad class is still picking from a bad
class. What it buys is that one asset class can no longer take the whole book
on the strength of having the widest ruler.

> [!warning] Do NOT run `--dry-run` while a scheduled firing is in progress
> Demonstrated, not theorised. On 2026-08-11 a manual dry-run overlapped the
> 19:30 firing; each process loads its own ~2 GB Kronos model, the Mac went
> into swap (89,817 pageouts, 27% memory free), and the unattended firing was
> left with **28 seconds of CPU across 13 minutes of wall clock at an RSS of
> 19 MB** — its model paged out, thrashing rather than computing. It had to be
> killed. It placed nothing only because the account was already breached; on
> a healthy account that is a firing silently lost.
>
> The symptom is **not** the sleep signature — `caffeinate` was held the whole
> time and the machine never slept. Low CPU with a tiny RSS is memory
> pressure. Check `ps` for a running `ftmo_runner.py` first, or preview from
> `/signal` in the web UI, which reuses the one long-lived session.

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
`com.tradingbotapp.ftmo`, which now wakes it **every 15 minutes, all 24 hours,
every day** — a deliberate superset of the real window, which is **16:30–23:00
Europe/Sofia, Monday to Friday**, enforced in code by
`within_trading_window()`. That is **27 firings per weekday**.

**This was revised on 2026-08-11 from hourly inside 16:30–11:30 every day but
Sunday, and it is a NARROWING — coverage fell from 19 hours across 6 days to
6.5 hours across 5.** A position opened at 22:45 on Friday is not looked at by
the runner again until 16:30 Monday, 65 hours later. Its stop and take-profit
live at the venue and are unaffected, so it stays protected; what was given up
is management and, without the reconcile job, the record.

Firing every 15 minutes on a 5-day forecast is further against the cadence this
project documented, and it was chosen anyway with that stated. The earlier
reasoning — that a daily rebalance on a multi-day forecast should not be
re-decided two dozen times a day, paying spread on sampling noise each time —
**has not been disproved. It was overridden, twice.**

Three things make 96 wakeups a day safe rather than merely survivable:

- The window is checked **before the audit log opens and long before torch is
  imported**, so an out-of-window wakeup costs one settings read.
- The window **no longer wraps midnight**, so it is a range and not a union.
  Both forms are selftested, so neither can be reintroduced by accident.
- **A `flock` PID lock stops firings piling up.** A cycle takes ~6.3 minutes
  against a 15-minute interval. `flock` rather than a file-exists check,
  because the kernel releases it however the holder dies — a crashed runner
  cannot wedge the schedule.

### The launchd jobs — five labels, and only two can trade

| label | when | what | can it place an order? |
|---|---|---|---|
| `com.tradingbotapp.ftmo` | every 15 min, 24/7 (superset) | the runner | **yes — opens and closes** |
| `com.tradingbotapp.ftmowatch` | hourly :30 Mon–Fri; exits 23:00 Sofia | equity watcher, holds `caffeinate` | **yes — closes only** |
| `com.tradingbotapp.ftmoreconcile` | every 30 min, 24/7 | close detection + FTMO day roll | no |
| `com.tradingbotapp.dailydigest` / `…evening` | 07:30 / 20:00 | phone digests | no |
| `com.tradingbotapp.vaultsync` | 22:00 | this vault | no |

**Verify launchd state UNSANDBOXED**, with
`launchctl print gui/$(id -u)/<label>`. A bare `launchctl list` from an agent
shell reports a different domain and shows loaded jobs as missing.

**The reconcile job is load-bearing, not a convenience.** 00:00 Europe/Prague
is 01:00 Sofia, and the first in-window firing of a new FTMO day is 16:30 —
15.5 hours later. `advance_state()` samples `day_start_balance` at roll time,
so rolling that late would silently exclude every overnight move from the
daily-loss calculation. **A limit that under-reports is worse than no limit,
because it looks like one.** The reconcile job rolls the day within 30 minutes
of the true boundary and keeps the journal honest about weekend stop-outs.

Neither the watcher nor the reconcile job is disarmed by the autotrade toggle,
deliberately: recording and watching are not trading.

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

## This is the THIRD exception to the evidence rule — and there is now a FOURTH

Rule 5 says autonomy is earned by graded evidence. The exceptions, in order:

1. `autotrade_runner.py` — see [[Autotrade (Experimental)]]
2. the FTMO path running fully unattended (rule 9)
3. **Kronos actually firing on FTMO with no class having passed a screen**
4. **`ftmo_watch.py` flattening the book with no human in the loop**
   (2026-08-11), taken with the alert-only alternative stated first and
   priced. It is the first of the four **not bound to a schedule**. What it
   removes is the human on a FLATTEN — never a limit.

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
6. ~~Watch the first real firings~~ — done, and they placed real orders. The
   journal now carries `venue=ftmo` entries, exits, take-profit hits and
   detections from 2026-08-07 onward.
7. ~~Give `ftmo_monitor` a driver~~ — done 2026-08-11, `ftmo_watch.py`.
8. ~~Wire a launchd job for `--reconcile`~~ — done,
   `com.tradingbotapp.ftmoreconcile`, every 30 minutes, 24/7.
9. **Open, as of 2026-08-12: decide what happens after the breach.** The
   account cleared its own daily limit on 2026-08-11 and the runner is still
   armed and still being refused every 15 minutes. Leaving it that way is
   safe but is not a decision. The $6.25 reserve is the thing to look at.
10. **Bring the night band in line with the new schedule.**
    `api/ftmo_api.py`'s `timeline()` still reconstructs 16:30 → 11:30 as 20
    hourly slots while the runner fires every 15 minutes to 23:00, so the band
    under-draws the firings that happened.
11. The evidence gap is unchanged and is still the thing that matters: no
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

- [[IBKR Integration]] — 🗄️ historical; the venue and its code are gone
- [[Risk Management System]] — RiskGuard, and why it cannot do this job
- [[Kronos Research Agent]] — the signal being pointed at this
- [[Autotrade (Experimental)]] — the first exception to the evidence rule
- [[Phase Milestones Dashboard]] — where this sits overall
