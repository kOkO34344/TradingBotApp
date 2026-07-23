---
tags: [adr, risk, philosophy, design]
date: 2026-07-21
status: "Finalized — codified in ibkr_service.py"
---

# ADR: Risk Enforcement in Python, Not Model Predictions

## The Question

When the research agent proposes a trade (or a human approves one), how should risk be managed?

**Option A (the trap):** Train the model with "always use stops," "never risk > 2%," and trust it to self-regulate.

**Option B (the right way):** Enforce all risk rules in Python code before the order reaches the broker. Let the model propose anything; let the human approve anything; the code decides what actually executes.

This project implements **Option B**, via the RiskGuard in `ibkr_service.py`.

## Context

Risk management in a human-in-the-loop system has two layers:

1. **Heuristic layer (model/human):** Agent or human reason about whether a trade makes sense
2. **Enforcement layer (code):** Hard rules that no order violates, period

Relying only on layer 1 is how accounts blow up. A model confidently reasoning "this trade makes sense" is not the same as "this trade won't lose more than 2% of the account." A human rushed and distracted is not the same as code checking every order.

## Decision

**All risk limits live in code.** Specifically:

- `risk_limits.json` defines the rules (max order size, max daily loss, max open positions, stop required)
- `RiskGuard` class in `ibkr_service.py` checks every order against those rules before submission
- If an order violates a limit, it's **blocked** and logged, not rejected at prompt time

**The model and human are trusted to reason about strategy, not about risk.**

## Why This Way

### Analogy: Nuclear power plant

- **Heuristic layer:** The reactor operator (trained, competent, reasoning in real-time)
- **Enforcement layer:** Passive physical failsafes (pressure relief valve, cooling system backup, fuel rod constraints)

The operator makes decisions. The failsafes stop a catastrophic decision from actually happening. You don't trust the operator to physically prevent a meltdown; you trust the physics.

Trading is the same.

### Concrete example

**Scenario:** Human is distracted, approves an order for 200 shares at $150 = $30,000 on a $10,000 account.

**Option A (model self-regulation):**
```python
# In the prompt:
"Be sure to check position size... you should never risk more than 2%..."
# Human approval: "fine"
# Order placed: 200 shares
# Account melts down: -$10,000 in one trade

# Why it failed:
# - Model was never asked "is 200 shares safe?" explicitly
# - Model might hallucinate that the stop will catch it
# - Model can't actually know current account equity in real time
# - Human was distracted and didn't re-check
```

**Option B (code enforcement):**
```python
# In risk_limits.json:
{
  "max_order_notional_usd": 5000
}

# Human approval: "fine"
# Code checks: 200 × $150 = $30,000 > $5,000 limit
# Order BLOCKED, logged to journal
# Account safe: trade never happened

# Why it worked:
# - Rule is in code, not in a prompt
# - Rule is checked *before* submission, not during
# - Rule is dumb simple: multiplication and comparison
# - Human can still override, but has to edit the JSON explicitly
```

## Rules That Live in Code

(From [[Risk Management System]])

| Rule | Default | Why |
|---|---|---|
| `max_order_notional_usd` | $5,000 | Single trade can't exceed ~50% of typical account |
| `max_open_positions` | 5 | Diversify, avoid concentration |
| `max_daily_loss_usd` | $300 | Circuit breaker: if you lose this much today, bot stops trading |
| `require_stop_attached` | true | No naked positions; every order must have a stop |

These are **not optional**; they're baked into `place_bracket_order` and `place_market_order`. Every order path checks them.

## What Still Requires Judgment

The model and human still decide:

- **Direction:** Long or short?
- **Timing:** When to enter, when to exit?
- **Opportunity:** Is this the right trade to make?
- **Trade-offs:** Given the rules, is 5 open positions the right limit, or should it be 3?

The code doesn't do any of this. It just says: "If you've decided on a trade, here are the hard limits you can't violate."

## Interaction with the Research Agent

When `paper_trader.py` exists (Phase 3), the flow will be:

```
research_agent.query()
  → proposes a rebalance ("buy top-3 momentum, sell rest")
human approval
  → you review and approve (or reject)
place_bracket_order()
  → RiskGuard checks notional, positions, stop
    ├─ PASS: order goes to IBKR
    └─ FAIL: order blocked, journal logged
```

The research agent reasons at the strategy level. The human judges at the approval level. The RiskGuard is the final line of defense, dumb and mechanical.

## Overrides

Every limit in `risk_limits.json` is explicitly editable. **If you want to override a rule:**

```json
{
  "max_order_notional_usd": 10000,  // Raise from $5k to $10k
  ...
}
```

This is intentional: changing a risk rule is a **deliberate action** (edit a file, save, re-run), not a casual prompt tweak. Every change is trackable (git history) and reversible (undo the edit).

## Why Not Just "Trust the Model"?

Three reasons:

1. **Models hallucinate.** Even Claude can confidently state wrong risk metrics.
2. **Models can't enforce.** A model can recommend a stop, but if you're using an API directly, the model can't actually guarantee the broker receives it.
3. **Humans get distracted.** You might miss a nuance in an approval, or be tired, or be multitasking. A rule in code doesn't get tired.

The combination (model reasoning + human judgment + code enforcement) is stronger than any one layer alone.

## Precedent

- **Stripe** (payments): Fraud rules in code + human review for edge cases
- **AWS** (cloud infrastructure): IAM policies + MFA gates + service quotas
- **Hedge funds**: Risk limits in code + senior trader approval + daily risk reviews
- **Exchanges**: Order validation (price reasonableness, contract specs) in code before any order is even sent to clearing

This is not a paranoid pattern; it's standard in any financial system that's been live longer than 6 months.

## Related Notes

- [[Risk Management System]] — the actual implementation
- [[IBKR Integration]] — how orders are placed and gated
- [[Plan]] — Phase 3 (paper trading) and Phase 4 (live capital)
- [[Autotrade (Experimental)]] — 2026-07-24: the one place the *approval* layer (not the risk layer) was deliberately removed. RiskGuard enforcement described in this ADR is fully intact there too — only the human `y/n` step is gone, and only because the owner explicitly asked for it twice, knowing the signal shows no edge.

## Future: Hooks

The Claude Agent SDK has a `PreToolUse` hook mechanism. In a future version (not Phase 3, but Phase 4+), the hook could intercept a trade proposal:

```python
@agent.on_pre_tool_use
def gate_orders(tool_name, tool_input):
    if tool_name == "place_trade":
        # Send Telegram: "Approve this trade? [yes/no]"
        # Block execution until human replies
        # Pass to RiskGuard if approved
```

This would add a **Telegram gate** (push notification, one-tap approval) on top of the code-based RiskGuard. Still dual-layer, but faster approval flow. **Not implemented yet;** the current plan is a terminal-based `paper_trader.py` approval loop for Phase 3.
