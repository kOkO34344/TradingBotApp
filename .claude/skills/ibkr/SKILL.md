---
name: ibkr
description: The IBKR paper-trading venue in TradingBotApp — close detection, the open positions still being monitored, stop verification, and the incidents that shaped the code. Read before touching ibkr_service.py, paper_trader.py, reflect_on_trades.py or autotrade_runner.py, or when auditing the trade journal against the real account.
---

# The IBKR venue (retired in place, rule 9)

FTMO is the trading venue now. IBKR places no new orders — but it is NOT dead
code and must not be deleted: three positions are still open and something has
to keep managing them.

**Open positions, verified read-only 2026-08-02** — all protected by
full-quantity `tif=GTC` stops in `PreSubmitted`:

| symbol | qty | avg cost | stop |
|---|---|---|---|
| JNJ | 19 | 249.98 | 237.61 |
| DIS | 52 | 95.39 | 90.83 |
| AMZN | 21 | 232.73 | 217.74 |

DIS and AMZN came from the 2026-07-27 rebalance the journal wrongly recorded as
`Cancelled`. `reflect_on_trades.py` and its launchd job keep running until
these close naturally.

**Every position check must verify stops are GTC, not merely present.** Query
`ib.trades()` (not `ib.openTrades()` right after placing) and check
`order.tif == "GTC"` — a DAY stop looks fine for hours and then silently
vanishes at the session close. That is not hypothetical: on 2026-07-21 a
missing TIF left three positions completely unprotected overnight.

## Verifying the account

`--dry-run` connects genuinely `readonly=True` (TWS-enforced). Use a clientId
no one else holds — trader_app 7, paper_trader 9, reflect_on_trades 11,
autotrade 13, web hub 15, trader_worker 16. `verify_stop_protection()` in
`ibkr_service.py` is the shared check; do not write a parallel one.

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


## Operational history (the reason the code looks like this)

4. **IBKR paper trading — RETIRED IN PLACE 2026-08-02 (rule 9), monitoring
   only.** No new orders on this venue. The three open positions stay managed
   until they close naturally; `reflect_on_trades.py` and its launchd job keep
   running. Everything below is still current and still matters, because those
   positions are still live. `paper_trader.py` holds
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
