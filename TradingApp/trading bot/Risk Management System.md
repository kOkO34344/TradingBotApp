---
tags: [risk, execution, infrastructure]
status: "live in code"
source: ibkr_service.py, ftmo_rules.py, ftmo_monitor.py
last_updated: 2026-08-02
---

# Risk Management System

> [!warning] There are now THREE risk layers, not two
> A third was added 2026-08-02 for the FTMO venue, and it works on a different
> principle: **continuous equity monitoring** rather than a pre-trade gate.
> The reason is that every FTMO limit is measured on equity *including
> floating P&L*, so an account can fail with no order placed at all — which
> RiskGuard, described below, structurally cannot detect. See [[FTMO Venue]].
>
> Also note the limits below are **stale**: they were raised on 2026-07-27 to
> 50,000 notional / 2,000 daily loss / 8 positions after the old caps blocked
> the exits for two open positions and trapped them.

The trading bot has **two distinct risk-management layers**: one in the backtester (optional "risk engine" for signal strategies), and one in the live execution layer (mandatory for all orders).

## Layer 1: Backtester Risk Engine (Optional)

Used in `trader_app.py` for the SMA/momentum strategies. Toggle-able in menu option 6.

### Components

1. **Trend filter** — only enter long when Close > SMA(200)
   - Rationale: avoid entering shorts or longs in strong downtrends
   - Effect: cuts entries by ~40%, reduces whipsaws

2. **2× ATR trailing stop**
   - Entry minus (2 × 14-bar ATR) at open, tightens as price rises
   - Rationale: volatility-aware stop that adapts to market regime
   - Effect: smaller individual losses, but still allows big runners to run

3. **Fixed-fractional position sizing**
   - Position size = (Equity × risk_pct) / stop_distance
   - If a stop-out hits, the loss equals ~risk_pct of current equity
   - Example: 2% risk per trade, $10k equity, 2×ATR stop = $50 potential loss
   - Rationale: prevents any single trade from blowing up the account; compounding works both ways

### Results (from variant_experiments.py)

| Variant | Avg CAGR | Avg Max DD | Effect |
|---|---:|---:|---|
| V1 baseline (none) | 6.1% | -34.0% | SMA 20/50 unhedged |
| V2 + trend filter | 7.2% | -33.2% | Modest improvement, fewer whipsaws |
| V3 + ATR stop | 6.8% | -28.5% | Cuts DD but also cuts winners |
| V4 + both + sizing | 7.8% | -30.1% | Best of the three, still loses to B&H |

**Caveat:** Hardening a losing signal doesn't fix the underlying problem. The risk engine was never meant to save the SMA crossover — it's a best-practice guardrail.

## Layer 2: Live Execution Risk Guard (Mandatory)

Enforced in `ibkr_service.py`, sits in front of **every order**, paper or (eventually) live. Rules are stored in `risk_limits.json` (editable, but changes are explicit).

### Architecture

```python
class RiskGuard:
    def check(ib, contract, quantity, est_price, has_stop, opening=True):
        # Returns (True, "ok") or (False, "reason blocked")
```

Every order path (`place_bracket_order`, `place_market_order`) calls `guard.check()` before submission. If it returns `False`, the order never reaches the broker. Instead, an entry is written to `trade_journal.csv` with the block reason.

### Rules (defaults in risk_limits.json)

```json
{
  "max_order_notional_usd": 50000,
  "max_open_positions": 8,
  "max_daily_loss_usd": 2000,
  "require_stop_attached": true
}
```

| Rule | Value | Applies to | Why |
|---|---|---|---|
| `max_order_notional_usd` | $50,000 | **entries only** | Caps new exposure per order (~4.4% of the $1.14M paper account) |
| `max_open_positions` | 8 | **entries only** | Avoids over-concentration |
| `max_daily_loss_usd` | $2,000 | **entries only** | Circuit breaker: stops opening new risk after a bad day |
| `require_stop_attached` | true | all orders | No bare orders without a stop; prevents "hope and pray" trades |

**Values raised 2026-07-27** from 5000/5/300. The old ones were mis-scaled to a
$1.14M account: $5,000 was 0.44% per order and was overriding the 2×ATR risk
model entirely (the model wanted ~3,291 AMZN shares; the cap allowed 21,
deploying just 1.3% of the account), and $300 was *less than a single
position's stop-loss risk* (~$314), so one ordinary stop-out tripped the
breaker.

### Enforcement Points

**All checked before submission. Every exposure limit is gated on `opening`
and can never block an exit** — see the 2026-07-27 incident below.

1. **Notional size check** (entries only)
   ```
   if opening and quantity * est_price > max_order_notional_usd:
       → BLOCKED, logged to journal, printed to stderr
   ```

2. **Stop attachment check** (all orders — deliberately ungated, rule 2)
   ```
   if require_stop_attached and not has_stop:
       → BLOCKED
   ```

3. **Open positions check** (entries only)
   ```
   if opening and len(current_positions) >= max_open_positions:
       → BLOCKED
   ```

4. **Daily loss circuit breaker** (entries only)
   ```
   if opening:
       realized_pnl = extract from ib.accountValues()
       if realized_pnl <= -abs(max_daily_loss_usd):
           → BLOCKED, "done for the day"
   ```

### ⚠️ A risk limit blocked an EXIT — found and fixed 2026-07-27

The notional cap and the daily-loss breaker used to apply to **every** order,
including ones that flatten or reduce a position. `paper_trader.py` had been
passing `opening=False` for exits correctly all along — but only
`max_open_positions` actually honoured it.

**A limit caps NEW exposure. Blocking a close *raises* risk, which is the
opposite of the job.** It also traps *winners* specifically: a position bought
under the cap that appreciates past it becomes un-exitable.

That is exactly what happened during an approved Kronos rebalance:

| | Entry notional | At exit | Result |
|---|---|---|---|
| AAPL | 15 × 328.04 = $4,921 | 15 × 333.80 = **$5,007** | BLOCKED |
| JNJ | 19 × 249.98 = $4,750 | 19 × 263.40 = **$5,005** | BLOCKED |

Both had been bought *under* the then-$5,000 cap. The rebalance silently held
instead of rotating — journal rows `BLOCKED JNJ` / `BLOCKED AAPL` at
`2026-07-27T01:51`.

**Fix:** both checks gated on `opening`. The breaker case matters more than
the cap: after a bad day you would otherwise be unable to close out of
anything, which is precisely when getting out matters most.
`require_stop_attached` stays ungated (an exit needs no stop of its own; exit
paths pass `allow_no_stop=True`). Two selftest cases now cover the exemption —
`python3 ibkr_service.py --selftest`.

### Known gap, found and fixed 2026-07-21: the stop check is about *placement*, not *persistence*

The "stop attachment" check (#2 above) verifies a stop order is submitted alongside the entry — it says nothing about whether that stop is still *alive* later. In production, `place_bracket_order`'s stop leg had no explicit time-in-force, so IBKR defaulted it to `DAY`, and it silently expired at end of the trading session. All three positions from the first `paper_trader.py` run (GOOGL, AAPL, JNJ) were briefly unprotected as a result — caught by re-checking positions, not by any automated alert.

**Fix:** stop (and target, if used) legs now explicitly set `tif="GTC"` in `ibkr_service.py`, so they persist until the position is actually closed. **Residual risk (as first written 2026-07-21):** RiskGuard has no ongoing check that a position's stop is still `PreSubmitted`/`GTC` — that verification is currently manual, not code-enforced.

**⚠️ That residual risk materialized on 2026-07-23, found 2026-07-25.** GOOGL's
GTC stop worked correctly — the position gapped through it at the open
rather than filling at the stop price — but the resulting close reached
*neither* `trade_journal.csv` nor a Telegram alert. Root cause:
`reflect_on_trades.py` (the "future improvement" this section called for)
detected closes only via `ib.reqExecutions()`, and **IBKR serves executions
for the current session only** — verified directly: a 30-day
`ExecutionFilter` returned 0 rows. Any close happening while the script
wasn't polling that exact session (overnight, a weekend, machine asleep) was
invisible to it permanently; `LOOKBACK_DAYS` couldn't help because the data
simply isn't there to request.

**Fix (2026-07-25):** `reflect_on_trades.py` is now two-tier — `reqExecutions`
first (exact fill price and P&L when available), then a position-snapshot
diff (`ib.positions()` vs. the previous run's saved snapshot) as a fallback
that cannot silently miss an event, at the cost of not knowing exit price or
P&L for what it catches. Both tiers now write to `trade_journal.csv`
directly (`CLOSE_FILLED` / `CLOSE_DETECTED`) — previously **neither tier
journaled a close at all**; `paper_trader.py` only journals exits it places
itself, so an autonomously-firing GTC stop reached the journal from nowhere.
GOOGL's close was backfilled by hand as `CLOSE_RECONSTRUCTED` (inferred from
daily bars: 07-23 opened at 321.13, below the 326.06 stop — filled at the
open, est. -$422, ~$69 of that pure gap slippage).

**Known remaining gap:** the snapshot tier journals and Telegram-alerts a
detected close but writes **no reflection** — `build_prompt()` needs a
realized P&L it doesn't have. So a weekend/overnight stop-out still leaves
nothing in `trade_reflections/` for `research_agent.py`'s
`load_reflections()` to learn from. Fixing that means reconstructing the
exit from price bars, the way the GOOGL backfill did manually — not yet
automated.

**Also clarified 2026-07-25 — the daily-loss circuit breaker is a pre-trade
gate, not a monitor.** `daily_realized_pnl()` reads IBKR's own `RealizedPnL`
account value, and `check_order()` only consults it when an order is about
to be *placed*. Nothing tried to place one on 07-23, so the breaker was
never evaluated for GOOGL's loss at all — it cannot stop a loss that arrives
from a stop firing on its own, only refuse the *next* order after one. Worth
knowing before relying on it as a safety net under [[Autotrade
(Experimental)]]'s unattended firings.

### Trade Journal Audit Trail

Every attempt (whether approved or blocked) is logged to `trade_journal.csv`:

```csv
timestamp,event,symbol,sec_type,action,quantity,price,stop,target,status,detail
2026-07-21T14:32:15,SUBMIT,AAPL,STK,BUY,50,189.50,185.00,200.00,submitted,bracket order
2026-07-21T14:32:45,RESULT,AAPL,STK,BUY,50,189.51,185.00,200.00,filled,filled 50/50
2026-07-21T15:15:20,BLOCKED,MSFT,STK,BUY,100,380.25,,,,blocked,notional $38025 exceeds limit $5000
```

Events: `SUBMIT`, `RESULT`, `BLOCKED`, `UNPROTECTED`, `CLOSE_FILLED`,
`CLOSE_DETECTED`, `CLOSE_RECONSTRUCTED`, `RESULT_CORRECTED`, `NOTE`

This journal is **not reconstructed from IBKR's logs** — it's written by the bot before and after every order attempt, so there's a single source of truth for what the bot tried to do and why.

> [!warning] "Single source of truth" is an aspiration the journal has twice failed
> It has described a non-existent account twice: phantom `CLOSE_DETECTED`
> rows on 07-25 for two positions that were open the whole time, and
> `Cancelled` rows on 07-27 for two orders that actually filled. Both are
> now annotated in place with `NOTE` / `RESULT_CORRECTED` rows rather than
> rewritten. **Verify against IBKR before trusting the journal**, and prefer
> `RESULT_CORRECTED` over any original `RESULT` dated before 2026-07-28.

## Decision Philosophy

**Why these specific numbers?**

- **$5,000 notional:** On a $10k account, this is ~50% of equity. Aggressive enough to deploy capital meaningfully, conservative enough to survive a 10% adverse move without a stop being too far away.
- **5 positions:** Matches the original plan's constraint. Prevents accidental over-concentration. Diversifies away idiosyncratic risk.
- **$300 daily loss:** On a $10k account, 3% of equity per day. Brutal enough to force the bot out before catastrophic blowup, loose enough to avoid whipsawing in/out on noise. Calibrate this once paper trading produces evidence.
- **Stop required:** Non-negotiable. Any position without a stop is a position bet on "being right" rather than "managing risk." Prevents the classic "but I'm sure AAPL will bounce" trap.

## Layer 3: Post-fill protection verification (added 2026-07-28)

RiskGuard is a **pre-trade** gate: it decides whether an order may be sent. It
cannot tell you what happened afterwards. Rule 2 ("no order without a stop")
was therefore enforced only at the moment of *submission* — a bracket whose
parent filled while its stop leg died left naked exposure that nothing
detected.

`place_bracket_order` now, on every fill:

1. Waits for a **terminal** parent status via `wait_for_status()` (it used to
   snapshot the status after a flat `ib.sleep(1)`, which is how two filled
   orders got journalled as `Cancelled`).
2. Re-requests open orders from IBKR and confirms a stop covering the **full
   filled quantity** with `tif == "GTC"`. A DAY stop counts as **no**
   protection, deliberately — that is the 2026-07-21 overnight-expiry failure.
3. If protection is missing, journals `UNPROTECTED` and **texts immediately**.

It does **not** auto-place a replacement stop. Silent remediation would hide
how often this happens; a human decides.

It also captures IBKR's own error text (`OrderErrorCollector`) into the
`RESULT` row, so a cancellation explains itself instead of being a bare
`Cancelled` that costs a day of guessing — which is exactly what happened
with error 10349 on 07-27.

## Bypass Mechanisms

Every rule can be overridden **by editing `risk_limits.json` explicitly** (not in the code, not in a prompt). This is deliberate: changing a risk rule should be a visible, traceable decision.

For urgent situations (e.g., "I need to test a bigger position"), edit the JSON, save it, and re-run. Every change is a git commit waiting to happen (assuming the code lives in version control, which it should).

## Interaction with Research Agent (Future)

When `paper_trader.py` exists (Phase 3), order proposals will flow: agent → proposed trade → human approval (y/n) → `place_bracket_order`. At that point, the RiskGuard is the last line of defense: if a human-approved trade somehow violates a limit (e.g., they fat-fingered a quantity), the guard still blocks it and logs why.

The guard is **not a substitute** for human judgment or agent reasoning — it's a circuit breaker, like the PPP on a nuclear power plant.

## Testing

`ibkr_service.py --selftest` runs **31** offline checks (2026-07-28):

- Contract builders (stock, forex, futures, crypto)
- Data-type routing (MIDPOINT for forex, AGGTRADES for crypto, TRADES for equities)
- RiskGuard logic (allows sane, blocks oversized, blocks stopless, blocks at max positions)
- Journal roundtrip (writes, reads, parses correctly)
- Bracket order validation (stops are on the right side of entries)
- Stop-protection predicate (full GTC cover passes; no stop, DAY stop, partial
  cover, cancelled and inactive stops all fail; plus the exact error-10349
  shape of a cancelled GTC leg alongside a surviving DAY one)
- IBKR error capture (keeps order errors, drops routine chatter, ignores other
  orders' reqIds, dedupes, stays a single journal-safe line, unsubscribes)

All pass as of 2026-07-28. The connected smoke test (`python3 ibkr_service.py` with TWS/Gateway running) verifies the IBKR connection but **places no orders**.

## Related Notes

- [[IBKR Integration]] — the execution layer and connection architecture
- [[Trade Journal Structure]] — how to read and interpret the journal
- [[ADR - Python Rules, Not Model Predictions]] — why this belongs in code, not in a prompt

## Files

- `ibkr_service.py` — contains RiskGuard class and all enforcement
- `risk_limits.json` — the limits (auto-created with defaults if missing)
- `trade_journal.csv` — the audit trail (created on first write)


---

## Layer 3: FTMO continuous equity monitor (added 2026-08-02)

Full detail in [[FTMO Venue]]. The short version of why it exists:

**RiskGuard's daily-loss breaker is a pre-trade gate.** `check_order()`
consults it only while an order is being placed, and it reads IBKR's *realised*
`RealizedPnL`. So it cannot see a loss that arrives on its own. On 2026-07-23
GOOGL's stop gapped through for roughly −$422 against a then-$300 limit and the
breaker was never evaluated, because nothing tried to place an order that day.
The loss was invisible for two days.

**Half-fixed 2026-08-02, and it matters which half.** Enforcement is unchanged
— still a pre-trade gate, still cannot stop a loss arriving on its own. What
changed is *visibility*: the trip condition moved into a pure, selftested
`daily_loss_breaker_status()` that both RiskGuard and a new monitor in
`reflect_on_trades.py` call, so the gate and the alert cannot disagree. The
monitor runs every 30 minutes and texts once per day when the limit is already
breached. A GOOGL-style overnight stop-out now surfaces within half an hour
instead of at the next order attempt. Nothing is flattened or disabled
automatically.

**The FTMO venue could not use that design at all**, because FTMO measures
equity including floating P&L and a 30-minute cycle is far too slow — a $25,000
account can move $1,000 in under a minute. So `ftmo_monitor.py` is
event-driven, recomputes equity on every tick, and acts on posture *changes*:
block new entries at 80% of a limit, close everything at 90%, and treat stale
quotes as UNKNOWN rather than safe.
