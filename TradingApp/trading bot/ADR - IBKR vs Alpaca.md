---
tags: [adr, architecture, broker, decision]
date: 2026-07-21
status: "HISTORICAL — superseded 2026-08-02; the project trades FTMO"
---

# ADR: Interactive Brokers vs Alpaca

> [!danger] HISTORICAL — superseded on 2026-08-02, code removed 2026-08-09
> This decision is moot. The project does not use Interactive Brokers or
> Alpaca; it trades **FTMO** through the cTrader Open API on a Challenge
> account with simulated capital. See [[FTMO Venue]].
>
> Kept because the reasoning — asset-class coverage, paper-account fidelity,
> API maturity — is the same reasoning that later chose cTrader over
> MetaTrader (the official `MetaTrader5` package is Windows-only and this
> machine is an arm64 Mac with no VM).


## Context

The original [[Plan]] recommended **Alpaca** (`alpaca-py`) as the broker/data provider for this project. It's free, friendly, has good docs, and is the industry standard for retail automation learning.

The actual implementation diverged to **Interactive Brokers** (`ib_async`, paper account via IB Gateway). This note captures why the switch happened, and why the decision is final for Phase 3+.

## Decision

**Use Interactive Brokers.** The paper connection is now live and hardened; switching back to Alpaca would require rewriting `ibkr_service.py`, `trader_app.py` menu, and re-verifying the whole execution layer. The sunk-cost argument (setup friction already happened) plus the benefit (multi-asset coverage: stocks, forex, futures, crypto) argues for staying.

## Rationale

### Alpaca: Strengths

- **Simplicity:** Dead-simple API, good docs, Python SDK works immediately
- **Cost-free:** No commission, no data costs, instant account setup
- **Community:** Tons of tutorials, StackOverflow answers, Slack communities
- **Focused:** Designed specifically for automation; no cruft

### Alpaca: Weaknesses

- **Equities only** (stocks and ETFs) — no forex, futures, commodities, crypto in the same account
- **Limited paper testing** — Alpaca's paper account is less realistic than IBKR's (no actual order queue matching, e.g.)
- **Fragility in market hours:** Occasional API instability during market open/close

### Interactive Brokers: Strengths

- **All asset classes:** Stocks, ETFs, forex, futures (ES, NQ, /CL, /GC, etc.), crypto, bonds, options all in one account
- **Real order matching:** Paper trading uses the same matching engine as live (just against paper prices)
- **Professional grade:** Used by hedge funds, pros; less flashy but more robust
- **Global:** Access to markets in 150+ countries

### Interactive Brokers: Weaknesses

- **API learning curve:** `ib_async` is more verbose than Alpaca's SDK; less hand-holding in docs
- **Setup friction:** Account verification (address checks, API enablement) takes more steps
- **Paper account quirks:** IB Gateway vs TWS, different port numbers for paper/live, firewall issues on some networks
- **Steeper:** Overkill for a simple stock-only strategy

## Why The Switch Happened (Chronology)

The decision wasn't strategic upfront; it emerged from constraints:

1. **Original plan:** Use Alpaca for everything
2. **Reality:** User wanted to trade forex, futures, crypto *alongside* stocks in the same system
3. **Issue:** Alpaca doesn't offer forex/futures/crypto — would need separate brokers/APIs
4. **Solution:** Switch to IBKR, which does all four in one account
5. **Sunk cost:** By the time this was realized, connection code was already half-written for IBKR
6. **Verification:** Paper account got address-verified (2026-07-21) and the connection works

## Why Switching Back Now Is Defensible But Expensive

**Defensible reasons to switch back:**
- Alpaca is simpler; would reduce code complexity
- Alpaca's focus on automation is good for learning
- Forex/futures/crypto could be handled by separate systems or ignored for now

**Why we're not switching:**
- Rewriting `ibkr_service.py` (connection, contract builders, order placement, data pulls) would cost ~8 hours of dev + testing
- `trader_app.py` menu (8 options) is already tuned to IBKR contract types and data availability
- The risk: a rewrite introduces bugs during the most critical phase (Phase 3 paper trading)
- The benefit: slightly simpler code that we'd outgrow once live (the pro-level account would need IBKR anyway)
- **Verdict:** Not worth the risk/reward ratio at this stage

## If Starting Over Today

If a new trader with the same requirements started today, the recommendation would be:

- **Equities + ETFs only?** → Alpaca. Ship faster, iterate on strategy.
- **Equities + forex + futures + crypto?** → IBKR. Accept the setup friction now, avoid rewriting layer 2.

For **this project**: IBKR is locked in. The infrastructure is verified; the paper connection works; Phase 3 depends on it.

## Related Notes

- [[IBKR Integration]] — how to connect, contracts, order types, security
- [[Risk Management System]] — lives in `ibkr_service.py`
- [[Plan]] — Phase 0 (setup) and Phase 3 (paper trading)

## Caveats

If user later decides to trade only stocks on Alpaca (dropping the forex/futures/crypto requirement), a port would be trivial:

```python
# Current: IBKR-specific
from ibkr_service import stock, place_bracket_order

# Hypothetical: Alpaca-specific
from alpaca_service import stock, place_bracket_order
# Same API, different backend
```

The abstraction (contract builders, order placement, journal logging) is clean enough that swapping brokers is a day's work if needed. But that day isn't now.
