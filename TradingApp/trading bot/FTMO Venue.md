---
tags: [ftmo, execution, infrastructure, risk, prop-firm]
status: "In progress — five modules built and selftested; blocked on cTrader app activation"
source: ftmo_rules.py, ftmo_monitor.py, ftmo_sizing.py, ftmo_audit.py, ftmo_service.py
last_updated: 2026-08-02
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

## The five modules

All pure-logic except the last, all with offline `--selftest` needing no
credentials and no network. **259 checks total.**

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

## Current blocker

The cTrader Open API app at `openapi.ctrader.com/apps` is still **Submitted**,
not **Active**, so application auth returns `CH_CLIENT_AUTH_FAILURE: OA client
is not in active state`.

That error already proves a lot works: the TLS handshake to
`demo.ctraderapi.com:5035` succeeded, the protobuf request encoded and sent,
and a real error response came back and was mapped correctly. Nothing below the
application-auth layer needs investigating.

Nothing past that layer can be built or verified — the order path needs real
symbol specifications from the venue and confirmation that server-side stops
attach as assumed.

## Next steps once the app activates

1. `python3 ftmo_service.py --authorize`, then `--probe`. If no accounts appear,
   the likeliest cause is the trial account being created *after* the app was
   authorised — re-run `--authorize` to re-consent.
2. Bind real symbol specs from the venue, replacing the plausible-but-invented
   ones used in the sizing tests.
3. Migrate `trade_journal.csv` to add a `venue` column (there is a header
   misalignment trap — see the code notes).
4. **IC-screen each asset class before enabling it.** Only stock CFDs inherit
   any existing evidence, and that evidence is IC ~0.
5. Build the signal→order path, then an integration pass across all five
   modules.

## Related

- [[IBKR Integration]] — the retired venue, still monitoring three positions
- [[Risk Management System]] — RiskGuard, and why it cannot do this job
- [[Kronos Research Agent]] — the signal being pointed at this
- [[Autotrade (Experimental)]] — the first exception to the evidence rule
- [[Phase Milestones Dashboard]] — where this sits overall
