---
tags: [risk, execution, infrastructure]
status: "live in code"
source: ibkr_service.py
---

# Risk Management System

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
  "max_order_notional_usd": 5000,
  "max_open_positions": 5,
  "max_daily_loss_usd": 300,
  "require_stop_attached": true
}
```

| Rule | Default | Why |
|---|---|---|
| `max_order_notional_usd` | $5,000 | A single bad trade can't exceed 50% of account equity |
| `max_open_positions` | 5 | Avoids over-concentration; matches the plan's "max 5 positions" |
| `max_daily_loss_usd` | $300 | Circuit breaker: if realized losses hit $300 today, bot stops trading for the day |
| `require_stop_attached` | true | No bare orders without a stop; prevents "hope and pray" trades |

### Enforcement Points

**All checked before submission:**

1. **Notional size check**
   ```
   if quantity * est_price > max_order_notional_usd:
       → BLOCKED, logged to journal, printed to stderr
   ```

2. **Stop attachment check**
   ```
   if require_stop_attached and not has_stop:
       → BLOCKED
   ```

3. **Open positions check** (only for new entries)
   ```
   if opening and len(current_positions) >= max_open_positions:
       → BLOCKED
   ```

4. **Daily loss circuit breaker**
   ```
   realized_pnl = extract from ib.accountValues()
   if realized_pnl <= -abs(max_daily_loss_usd):
       → BLOCKED, "done for the day"
   ```

### Known gap, found and fixed 2026-07-21: the stop check is about *placement*, not *persistence*

The "stop attachment" check (#2 above) verifies a stop order is submitted alongside the entry — it says nothing about whether that stop is still *alive* later. In production, `place_bracket_order`'s stop leg had no explicit time-in-force, so IBKR defaulted it to `DAY`, and it silently expired at end of the trading session. All three positions from the first `paper_trader.py` run (GOOGL, AAPL, JNJ) were briefly unprotected as a result — caught by re-checking positions, not by any automated alert.

**Fix:** stop (and target, if used) legs now explicitly set `tif="GTC"` in `ibkr_service.py`, so they persist until the position is actually closed. **Residual risk:** RiskGuard has no ongoing check that a position's stop is still `PreSubmitted`/`GTC` — that verification is currently manual (part of the periodic position health-check in [[IBKR Integration]]), not code-enforced. A future improvement would be a standalone check (or scheduled job) that flags any open position with no matching GTC stop order.

### Trade Journal Audit Trail

Every attempt (whether approved or blocked) is logged to `trade_journal.csv`:

```csv
timestamp,event,symbol,sec_type,action,quantity,price,stop,target,status,detail
2026-07-21T14:32:15,SUBMIT,AAPL,STK,BUY,50,189.50,185.00,200.00,submitted,bracket order
2026-07-21T14:32:45,RESULT,AAPL,STK,BUY,50,189.51,185.00,200.00,filled,filled 50/50
2026-07-21T15:15:20,BLOCKED,MSFT,STK,BUY,100,380.25,,,,blocked,notional $38025 exceeds limit $5000
```

Events: `SUBMIT`, `RESULT`, `BLOCKED`

This journal is **not reconstructed from IBKR's logs** — it's written by the bot before and after every order attempt, so there's a single source of truth for what the bot tried to do and why.

## Decision Philosophy

**Why these specific numbers?**

- **$5,000 notional:** On a $10k account, this is ~50% of equity. Aggressive enough to deploy capital meaningfully, conservative enough to survive a 10% adverse move without a stop being too far away.
- **5 positions:** Matches the original plan's constraint. Prevents accidental over-concentration. Diversifies away idiosyncratic risk.
- **$300 daily loss:** On a $10k account, 3% of equity per day. Brutal enough to force the bot out before catastrophic blowup, loose enough to avoid whipsawing in/out on noise. Calibrate this once paper trading produces evidence.
- **Stop required:** Non-negotiable. Any position without a stop is a position bet on "being right" rather than "managing risk." Prevents the classic "but I'm sure AAPL will bounce" trap.

## Bypass Mechanisms

Every rule can be overridden **by editing `risk_limits.json` explicitly** (not in the code, not in a prompt). This is deliberate: changing a risk rule should be a visible, traceable decision.

For urgent situations (e.g., "I need to test a bigger position"), edit the JSON, save it, and re-run. Every change is a git commit waiting to happen (assuming the code lives in version control, which it should).

## Interaction with Research Agent (Future)

When `paper_trader.py` exists (Phase 3), order proposals will flow: agent → proposed trade → human approval (y/n) → `place_bracket_order`. At that point, the RiskGuard is the last line of defense: if a human-approved trade somehow violates a limit (e.g., they fat-fingered a quantity), the guard still blocks it and logs why.

The guard is **not a substitute** for human judgment or agent reasoning — it's a circuit breaker, like the PPP on a nuclear power plant.

## Testing

`ibkr_service.py --selftest` runs 18 offline checks:

- Contract builders (stock, forex, futures, crypto)
- Data-type routing (MIDPOINT for forex, AGGTRADES for crypto, TRADES for equities)
- RiskGuard logic (allows sane, blocks oversized, blocks stopless, blocks at max positions)
- Journal roundtrip (writes, reads, parses correctly)
- Bracket order validation (stops are on the right side of entries)

All pass as delivered. The connected smoke test (`python3 ibkr_service.py` with TWS/Gateway running) verifies the IBKR connection but **places no orders**.

## Related Notes

- [[IBKR Integration]] — the execution layer and connection architecture
- [[Trade Journal Structure]] — how to read and interpret the journal
- [[ADR - Python Rules, Not Model Predictions]] — why this belongs in code, not in a prompt

## Files

- `ibkr_service.py` — contains RiskGuard class and all enforcement
- `risk_limits.json` — the limits (auto-created with defaults if missing)
- `trade_journal.csv` — the audit trail (created on first write)
