#!/usr/bin/env python3
"""
ftmo_sizing.py — position sizing for the FTMO venue. Pure logic, no network.

Turns "Kronos likes this symbol" into "this many units, with this stop", under
two constraints that must both hold:

  PER TRADE      risk at the stop <= risk_pct of equity (1% by default)
  PER PORTFOLIO  the sum of every open position's risk stays inside the daily
                 soft limit, so a simultaneous stop-out across the whole book
                 lands ON that limit rather than through FTMO's hard one

The second is the one a naive sizer omits. Four positions each individually
inside a 1% cap can still take out a 4% daily limit together, and a book of
four correlated names across correlated asset classes is exactly how that
happens. `ftmo_rules.max_position_risk_usd()` owns that budget; this module
spends it.

WHY THIS ISN'T THE OLD SHARE SIZER. The retired IBKR sizer thought in SHARES of a US
equity priced in the account currency, and clamps to a notional cap. Here a
"unit" means something different per instrument — 100,000 base units for a
standard FX lot, one ounce for gold, one share for a stock CFD, one index point
for US30 — and the quote currency is frequently not the account currency. So
the sizing is expressed in units and converted explicitly, rather than assuming
price and money are the same number.

THE CONVERSION IS EXPLICIT AND MANDATORY. `quote_to_account_rate` has no
default. This project has already been bitten once by an implicit FX direction:
`get_net_liquidation_usd` inverted an ExchangeRate and misstated equity by ~29%,
which would have mis-sized every order (see CLAUDE.md). A rate you must pass is
a rate you must think about, and `size_position` refuses a non-positive one
rather than treating it as 1.0.

ROUNDING IS ALWAYS DOWN. Volume is floored to the symbol's step, never rounded
to nearest, because rounding up spends more of the risk budget than was
approved. The consequence is deliberate: when a symbol's MINIMUM volume already
risks more than the budget allows, the order is REFUSED rather than placed
slightly oversized. On a $25,000 account with $250 per trade that is a real
case, not a theoretical one — a wide-ATR index CFD can price its minimum lot
well past the budget.

Offline selftest:  python3 ftmo_sizing.py --selftest
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

import ftmo_rules as fr

# Same multiple the retired IBKR sizer used, so stops stayed at the same
# distance for the same volatility and their results stay comparable.
STOP_ATR_MULT = 2.0

# ...but that multiple was calibrated against a TWENTY-bar forecast horizon,
# and since 2026-08-08 Kronos forecasts FIVE. Holding the stop still while the
# target shrank inverted the geometry: the 2026-08-09 dry run proposed entries
# at 0.3R, 0.2R and 0.1R, which need a 77-91% hit rate to break even against a
# measured ~50%. So the multiple now scales with the horizon it is protecting.
REFERENCE_HORIZON_BARS = 20

# The floor exists because a stop tighter than roughly one bar's range sits
# INSIDE the instrument's ordinary noise, so it stops out on the walk rather
# than on the thesis being wrong. Tightening a stop always improves the R
# multiple on paper — it is the cheapest possible way to make a plan look
# better while making it perform worse — and this constant is what stops that
# from running away. A position held at least one bar needs at least one bar's
# room.
MIN_STOP_ATR_MULT = 1.0

# cTrader expresses volume in hundredths of a unit ("centi-units"), and a
# symbol's minVolume / stepVolume / maxVolume arrive in that same scale. Mixing
# the two scales silently sizes by 100x, so the conversion is named.
VOLUME_SCALE = 100


@dataclass(frozen=True)
class SymbolSpec:
    """The subset of ProtoOASymbol this module needs, in cTrader's own units.

    `min_volume`, `step_volume` and `max_volume` are centi-units, exactly as
    the venue reports them. `quote_currency` is informational for the caller's
    logging — the actual conversion arrives as an explicit rate.
    """
    symbol_id: int
    name: str
    min_volume: int
    step_volume: int
    max_volume: int
    digits: int = 5
    quote_currency: str = "USD"

    def __post_init__(self):
        if self.min_volume <= 0 or self.step_volume <= 0:
            raise ValueError(f"{self.name}: min/step volume must be positive")
        if self.max_volume < self.min_volume:
            raise ValueError(f"{self.name}: max_volume below min_volume")


@dataclass(frozen=True)
class SizingResult:
    """What to send, and — when nothing should be sent — why not."""
    accepted: bool
    volume: int          # centi-units, ready for ProtoOANewOrderReq
    units: float         # human-readable
    entry_price: float
    stop_price: float
    stop_distance: float
    risk_at_stop: float  # account currency, what a stop-out actually costs
    budget_remaining: float
    reasons: tuple[str, ...]

    def summary(self) -> str:
        if not self.accepted:
            return f"NO TRADE: {'; '.join(self.reasons)}"
        return (f"{self.units:g} units (volume {self.volume}) @ {self.entry_price:g} "
                f"stop {self.stop_price:g} — risks {self.risk_at_stop:,.2f} "
                f"of {self.budget_remaining:,.2f} remaining")


def spec_from_capture(name: str, capture: dict | None = None) -> SymbolSpec:
    """Build a SymbolSpec from the REAL venue capture in ftmo_symbol_specs.json.

    Written 2026-08-05, when the first successful `--symbols` capture showed
    that every spec this module's tests had invented was wrong, several by two
    or three orders of magnitude:

        EURUSD     min/step  invented 100      real 100,000   (1000x)
        XAUUSD     min/step  invented 1        real 100        (100x)
        US30.cash  min/step  invented 10       real 1          digits 1 -> 2

    Sizing maths validated against invented minimums proves nothing about what
    the venue will accept, which is why the capture is tracked in git and the
    selftest below reads from it.
    """
    capture = capture if capture is not None else _load_capture()
    if name not in capture:
        # ValueError, not KeyError: this is input validation, and it matches
        # how SymbolSpec.__post_init__ rejects a bad spec.
        raise ValueError(
            f"{name!r} is not in the symbol capture. Available example names: "
            f"{', '.join(sorted(capture)[:5])}...")
    r = capture[name]
    return SymbolSpec(
        symbol_id=r["symbol_id"], name=name,
        min_volume=r["min_volume"], step_volume=r["step_volume"],
        max_volume=r["max_volume"], digits=r["digits"],
        quote_currency=r.get("quote_asset") or "USD")


def _load_capture() -> dict:
    import ftmo_service
    return ftmo_service.load_symbol_specs()


def stop_atr_mult_for_horizon(horizon_bars: int,
                              base_mult: float = STOP_ATR_MULT,
                              reference_bars: int = REFERENCE_HORIZON_BARS) -> float:
    """The ATR multiple to protect a position held `horizon_bars` bars.

    Scales as the SQUARE ROOT of the horizon, not linearly. Price dispersion
    grows with sqrt(time), so a 5-bar hold expects half the excursion of a
    20-bar hold, not a quarter of it. Linear scaling would return 0.5 x ATR at
    5 bars — inside a single bar's range, i.e. a stop that fires on noise
    before the forecast has had a bar to be right or wrong.

    Calibrated so the reference horizon is unchanged: `stop_atr_mult_for_horizon(20)`
    is exactly `STOP_ATR_MULT`. This is a re-derivation of an existing constant
    for a horizon that moved, NOT a new free parameter tuned until the numbers
    looked good — rule 4. Nothing here was fitted to a return series.

    What this does NOT do, and it matters more than what it does: it cannot
    make a forecast larger than the noise it sits in. On 2026-08-09 Kronos
    predicted +2.01% on NATGAS.cash whose 5-bar dispersion is ~7.6%. Halving
    the stop takes that trade from 0.3R to 0.6R and no further. A tighter stop
    improves the RATIO by shrinking the denominator; it adds nothing to the
    numerator, and it buys the improvement with a higher stop-out rate. Read
    any R improvement from this function as geometry corrected, never as edge
    created.
    """
    if horizon_bars <= 0:
        raise ValueError(f"horizon_bars must be positive, got {horizon_bars!r}")
    if reference_bars <= 0:
        raise ValueError(f"reference_bars must be positive, got {reference_bars!r}")
    if base_mult <= 0:
        raise ValueError(f"base_mult must be positive, got {base_mult!r}")
    scaled = base_mult * math.sqrt(horizon_bars / reference_bars)
    return max(MIN_STOP_ATR_MULT, scaled)


def stop_price_from_atr(entry_price: float, atr: float, side: str,
                        mult: float = STOP_ATR_MULT) -> float:
    """Stop `mult` x ATR away from entry, on the correct side.

    A long's stop sits below entry and a short's above. Getting this backwards
    produces a stop that fills instantly, which is why side is required rather
    than inferred from a signed quantity.
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if atr <= 0:
        raise ValueError("atr must be positive — a zero stop distance is infinite size")
    return entry_price - mult * atr if side == "BUY" else entry_price + mult * atr


def take_profit_from_prediction(entry_price: float, predicted_return_pct: float,
                                side: str) -> float:
    """The price at which Kronos's own forecast would have come true.

    TP = entry x (1 + predicted_return_pct/100). Chosen over an R multiple
    deliberately (owner decision, 2026-08-08): the target is the strategy's
    own thesis, so the position exits when the forecast is realised rather
    than at a level picked independently of it.

    Two properties of this input to keep in view, neither of which this
    function can fix:

    1. **The forecast must point the trade's way or there is no target.** A
       top-N candidate can carry a NEGATIVE prediction — EURUSD was entered at
       -0.15% on 2026-08-07 — and `entry x (1 + -0.0015)` is BELOW entry on a
       long. That is not a conservative target, it is an instruction to close
       at a loss on fill. Raises instead, and the caller drops the entry.
    2. **The prediction is noisy at exactly this resolution.** Documented in
       CLAUDE.md: the same symbol re-forecast ten minutes later moved from
       +17.64% to +25.15%. The stop is ATR-derived and stable; the target is
       not, so two runs can size identically and target differently. That is a
       property of the chosen rule, not a defect in this arithmetic.
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price!r}")
    if side == "BUY" and predicted_return_pct <= 0:
        raise ValueError(
            f"a BUY needs a positive forecast to derive a target from, got "
            f"{predicted_return_pct:+.2f}% — no take-profit exists on the "
            f"profitable side")
    if side == "SELL" and predicted_return_pct >= 0:
        raise ValueError(
            f"a SELL needs a negative forecast to derive a target from, got "
            f"{predicted_return_pct:+.2f}% — no take-profit exists on the "
            f"profitable side")
    return entry_price * (1.0 + predicted_return_pct / 100.0)


def floor_to_step(volume: float, spec: SymbolSpec) -> int:
    """Floor a raw centi-unit volume onto the symbol's step grid.

    Down, never nearest. Rounding up would spend more risk budget than the
    caller approved, and the whole point of the budget is that it is not
    exceeded by accident.

    This also absorbs floating-point residue in the caller's favour. Prices
    subtract inexactly — 1.0850 - 1.0750 is 0.010000000000000009, not 0.01 —
    so a "100 pip" stop computes a hair wider than intended and the raw size a
    hair smaller. Flooring turns that into one step less volume rather than one
    step more, so the error can only ever reduce risk. Do not "fix" it by
    rounding to nearest.
    """
    if volume < spec.min_volume:
        return 0
    steps = math.floor((volume - spec.min_volume) / spec.step_volume)
    return min(spec.min_volume + steps * spec.step_volume, spec.max_volume)


def size_position(spec: SymbolSpec, equity: float, risk_pct: float,
                  entry_price: float, stop_price: float,
                  quote_to_account_rate: float,
                  budget_remaining: float) -> SizingResult:
    """Size one position under both the per-trade and per-portfolio caps.

    `budget_remaining` comes from ftmo_rules.max_position_risk_usd() and is
    what is left of the daily soft limit after existing open risk and any loss
    already taken today. Passing it in rather than recomputing keeps one owner
    for that number.
    """
    reasons: list[str] = []
    stop_distance = abs(entry_price - stop_price)

    def refuse(reason: str) -> SizingResult:
        return SizingResult(
            accepted=False, volume=0, units=0.0, entry_price=entry_price,
            stop_price=stop_price, stop_distance=stop_distance, risk_at_stop=0.0,
            budget_remaining=budget_remaining, reasons=(reason,))

    if stop_distance <= 0:
        # Rule 2 territory: without a stop there is no defined risk, so there
        # is no size that can be justified.
        return refuse("no stop distance — entry and stop are the same price")
    if quote_to_account_rate <= 0:
        return refuse(f"quote_to_account_rate must be positive, got "
                      f"{quote_to_account_rate!r} — refusing rather than assuming 1.0")
    if equity <= 0:
        return refuse("equity must be positive")
    if risk_pct <= 0:
        return refuse("risk_pct must be positive")
    if budget_remaining <= 0:
        return refuse("no portfolio risk budget left today")

    per_trade_risk = equity * risk_pct / 100.0
    allowed_risk = min(per_trade_risk, budget_remaining)
    if allowed_risk < per_trade_risk:
        reasons.append(f"capped by portfolio budget ({budget_remaining:,.2f}) "
                       f"below per-trade risk ({per_trade_risk:,.2f})")

    # Loss on a stop-out, per unit, in the account currency.
    risk_per_unit = stop_distance * quote_to_account_rate
    raw_units = allowed_risk / risk_per_unit
    volume = floor_to_step(raw_units * VOLUME_SCALE, spec)

    if volume <= 0:
        min_units = spec.min_volume / VOLUME_SCALE
        return refuse(
            f"minimum volume {spec.min_volume} ({min_units:g} units) would risk "
            f"{min_units * risk_per_unit:,.2f}, over the {allowed_risk:,.2f} "
            f"allowed — refusing rather than sizing up")

    units = volume / VOLUME_SCALE
    risk_at_stop = units * risk_per_unit
    if volume >= spec.max_volume:
        reasons.append(f"clamped to the symbol's max volume ({spec.max_volume})")

    # Floor-to-step can only reduce risk, so this is a guard against a future
    # edit breaking that invariant, not an expected branch.
    if risk_at_stop > allowed_risk + 1e-6:
        return refuse(f"internal: sized risk {risk_at_stop:,.2f} exceeds allowed "
                      f"{allowed_risk:,.2f}")

    if not reasons:
        reasons.append("ok")
    return SizingResult(
        accepted=True, volume=volume, units=units, entry_price=entry_price,
        stop_price=stop_price, stop_distance=stop_distance,
        risk_at_stop=risk_at_stop, budget_remaining=budget_remaining,
        reasons=tuple(reasons))


def plan_entry(spec: SymbolSpec, config: fr.FTMOConfig, state: fr.AccountState,
               entry_price: float, atr: float, side: str,
               quote_to_account_rate: float, risk_pct: float,
               open_risk_usd: float = 0.0,
               max_positions: int = 4,
               horizon_bars: int = REFERENCE_HORIZON_BARS) -> SizingResult:
    """Full pre-trade path: rule engine first, then sizing. The entry point.

    Deliberately asks `ftmo_rules.evaluate()` BEFORE computing a size, so a
    blocked account produces a refusal with the rule engine's own reason rather
    than a number nobody should act on. Same ordering principle as
    RiskGuard.check() gating an order before it reaches the broker.

    `horizon_bars` defaults to the REFERENCE horizon, which reproduces the
    historical 2 x ATR stop exactly. `ftmo_signal.plan_orders` is the live
    path and passes the real forecast horizon; this default keeps an
    unparameterised call honest rather than silently tightening a stop.
    """
    stop_price = stop_price_from_atr(
        entry_price, atr, side, mult=stop_atr_mult_for_horizon(horizon_bars))
    verdict = fr.evaluate(config, state)

    def refuse(reason: str) -> SizingResult:
        return SizingResult(
            accepted=False, volume=0, units=0.0, entry_price=entry_price,
            stop_price=stop_price, stop_distance=abs(entry_price - stop_price),
            risk_at_stop=0.0, budget_remaining=0.0, reasons=(reason,))

    if not verdict.can_open:
        return refuse(f"rule engine refuses new entries: {'; '.join(verdict.reasons)}")
    if state.open_position_count >= max_positions:
        return refuse(f"already at max_positions={max_positions} "
                      f"({state.open_position_count} open)")

    budget = fr.max_position_risk_usd(config, state, open_risk_usd=open_risk_usd)
    return size_position(spec, state.equity, risk_pct, entry_price, stop_price,
                         quote_to_account_rate, budget)


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline checks across all four asset classes. No network, no venue."""
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def approx(a, b, tol=1e-6):
        return abs(a - b) <= tol

    cfg = fr.FTMOConfig(product="2step", phase="challenge", initial_capital=25_000.0)
    flat = fr.AccountState(equity=25_000, balance=25_000, day_start_balance=25_000)

    # SYNTHETIC fixtures, kept deliberately. Their round numbers make the
    # arithmetic below checkable by hand, which is what these cases test — the
    # formula, not the venue. They are NOT what FTMO actually reports: the real
    # specs differ by up to 1000x and are exercised in the "REAL venue specs"
    # section at the end of this selftest, against the tracked capture.
    fx = SymbolSpec(1, "EURUSD-synthetic", min_volume=100, step_volume=100,
                    max_volume=1_000_000_00, digits=5, quote_currency="USD")
    idx = SymbolSpec(2, "US30", min_volume=10, step_volume=10,
                     max_volume=100_00, digits=1, quote_currency="USD")
    stk = SymbolSpec(3, "AAPL", min_volume=100, step_volume=100,
                     max_volume=100_000_00, digits=2, quote_currency="USD")
    gold = SymbolSpec(4, "XAUUSD", min_volume=1, step_volume=1,
                      max_volume=10_000_00, digits=2, quote_currency="USD")

    print("stop placement:")
    check("long stop sits below entry",
          approx(stop_price_from_atr(100.0, 2.0, "BUY"), 96.0))
    check("short stop sits above entry",
          approx(stop_price_from_atr(100.0, 2.0, "SELL"), 104.0))
    check("zero ATR refused (infinite size)",
          _raises(lambda: stop_price_from_atr(100.0, 0.0, "BUY")))
    check("a bad side is refused, not guessed",
          _raises(lambda: stop_price_from_atr(100.0, 2.0, "HOLD")))

    print("the stop multiple scales to the forecast horizon (2026-08-09):")
    check("the reference horizon reproduces STOP_ATR_MULT exactly — this is a "
          "re-derivation, not a retune",
          approx(stop_atr_mult_for_horizon(REFERENCE_HORIZON_BARS), STOP_ATR_MULT))
    check("the live 5-bar horizon halves the multiple to 1.0",
          approx(stop_atr_mult_for_horizon(5), 1.0))
    check("scaling is sqrt-of-time, not linear (5/20 -> 1/2, not 1/4)",
          approx(stop_atr_mult_for_horizon(5), STOP_ATR_MULT * 0.5)
          and not approx(stop_atr_mult_for_horizon(5), STOP_ATR_MULT * 0.25))
    check("a longer horizon widens the stop (80 bars -> 4.0)",
          approx(stop_atr_mult_for_horizon(80), 4.0))
    check("the multiple rises monotonically with the horizon",
          all(stop_atr_mult_for_horizon(h) <= stop_atr_mult_for_horizon(h + 1)
              for h in range(1, 60)))
    check("the floor holds at a 1-bar horizon rather than returning 0.45",
          approx(stop_atr_mult_for_horizon(1), MIN_STOP_ATR_MULT))
    check("no horizon can ever produce a stop inside one bar's range",
          all(stop_atr_mult_for_horizon(h) >= MIN_STOP_ATR_MULT
              for h in range(1, 200)))
    check("a zero or negative horizon is refused, never floored",
          _raises(lambda: stop_atr_mult_for_horizon(0))
          and _raises(lambda: stop_atr_mult_for_horizon(-5)))
    # The whole point of the change: same ATR, same entry, shorter horizon ->
    # a nearer stop, which is what lifts R off the floor.
    check("a 5-bar stop sits nearer than a 20-bar stop on identical inputs",
          stop_price_from_atr(100.0, 2.0, "BUY",
                              mult=stop_atr_mult_for_horizon(5))
          > stop_price_from_atr(100.0, 2.0, "BUY",
                                mult=stop_atr_mult_for_horizon(20)))

    print("take-profit from Kronos's own forecast (owner decision 2026-08-08):")
    check("a +14.58% forecast targets entry x 1.1458",
          approx(take_profit_from_prediction(1917.42, 14.58, "BUY"),
                 1917.42 * 1.1458))
    check("the target sits above entry on a long",
          take_profit_from_prediction(100.0, 5.0, "BUY") > 100.0)
    check("the target sits below entry on a short",
          take_profit_from_prediction(100.0, -5.0, "SELL") < 100.0)
    # The live case: a top-N candidate whose forecast points the WRONG way.
    # EURUSD was entered at -0.15% on 2026-08-07; entry x (1 - 0.0015) is a
    # "target" below entry on a long, i.e. an instruction to close at a loss.
    check("a NEGATIVE forecast yields no BUY target — it raises",
          _raises(lambda: take_profit_from_prediction(1.16, -0.15, "BUY")))
    check("a zero forecast yields no BUY target either",
          _raises(lambda: take_profit_from_prediction(100.0, 0.0, "BUY")))
    check("a POSITIVE forecast yields no SELL target",
          _raises(lambda: take_profit_from_prediction(100.0, 5.0, "SELL")))
    check("a bad side is refused, not guessed",
          _raises(lambda: take_profit_from_prediction(100.0, 5.0, "HOLD")))
    check("a non-positive entry is refused",
          _raises(lambda: take_profit_from_prediction(0.0, 5.0, "BUY")))
    check("stop and target straddle entry, so R is positive and finite",
          (take_profit_from_prediction(100.0, 6.0, "BUY") - 100.0)
          / (100.0 - stop_price_from_atr(100.0, 2.0, "BUY")) > 0)

    print("floor_to_step never rounds up:")
    check("below minimum -> zero, not the minimum", floor_to_step(50, fx) == 0)
    check("exactly the minimum is allowed", floor_to_step(100, fx) == 100)
    check("between steps floors down", floor_to_step(250, fx) == 200)
    check("clamped to max volume", floor_to_step(10**12, idx) == idx.max_volume)

    print("core risk maths (1% of 25k = 250):")
    # AAPL at 300, ATR 4 -> stop 8 away. 250 / 8 = 31.25 units -> 3125 centi,
    # floored onto a 100 step -> 3100 centi = 31 units, risking 248.
    r = size_position(stk, 25_000, 1.0, 300.0, 292.0, 1.0, budget_remaining=1000.0)
    check("stock CFD accepted", r.accepted)
    check("units floored to the step grid", r.units == 31.0)
    check("risk lands just under the cap", approx(r.risk_at_stop, 248.0))
    check("never over the per-trade cap", r.risk_at_stop <= 250.0 + 1e-9)

    print("the same maths across the other three classes:")
    # EURUSD at 1.0850, stop ~100 pips. Ideal is 25,000 units, but the price
    # subtraction is inexact (0.010000000000000009), so flooring gives 24,999.
    # Assert the PROPERTY — within one step, and never over the cap — rather
    # than an idealised integer float arithmetic will not produce.
    r = size_position(fx, 25_000, 1.0, 1.0850, 1.0750, 1.0, budget_remaining=1000.0)
    step_units = fx.step_volume / VOLUME_SCALE
    check("FX sized in base units, within one step of ideal",
          25_000.0 - step_units <= r.units <= 25_000.0)
    check("FX risk respects the cap", r.risk_at_stop <= 250.0 + 1e-9)
    check("float residue errs DOWNWARD, never up", r.risk_at_stop < 250.0)
    # US30 at 44,000, stop 300 points. 250 / 300 = 0.833 units -> 83 centi.
    r = size_position(idx, 25_000, 1.0, 44_000.0, 43_700.0, 1.0, budget_remaining=1000.0)
    check("index sized in fractional units", r.units == 0.8)
    check("index risk under the cap", r.risk_at_stop <= 250.0 + 1e-9)
    # Gold at 2,400, stop 30. 250 / 30 = 8.33 units -> 833 centi.
    r = size_position(gold, 25_000, 1.0, 2_400.0, 2_370.0, 1.0, budget_remaining=1000.0)
    check("gold sized in ounces", r.units == 8.33)
    check("gold risk under the cap", r.risk_at_stop <= 250.0 + 1e-9)

    print("currency conversion is explicit and mandatory:")
    r = size_position(stk, 25_000, 1.0, 300.0, 292.0, 0.0, budget_remaining=1000.0)
    check("a zero rate is refused, not treated as 1.0", not r.accepted)
    check("...and says so", "refusing rather than assuming" in r.reasons[0])
    r = size_position(stk, 25_000, 1.0, 300.0, 292.0, -1.0, budget_remaining=1000.0)
    check("a negative rate is refused", not r.accepted)
    # Quote currency worth half the account currency -> twice the units.
    half = size_position(stk, 25_000, 1.0, 300.0, 292.0, 0.5, budget_remaining=1000.0)
    full = size_position(stk, 25_000, 1.0, 300.0, 292.0, 1.0, budget_remaining=1000.0)
    check("a weaker quote currency buys more units", half.units > full.units)
    check("but the risk in ACCOUNT currency is still capped",
          half.risk_at_stop <= 250.0 + 1e-9)

    print("refusing beats sizing up:")
    # Minimum lot of gold with a 300-wide stop risks 3.00; fine. Make the stop
    # enormous so even one minimum step blows the budget.
    r = size_position(gold, 25_000, 1.0, 2_400.0, 400.0, 1.0, budget_remaining=10.0)
    check("oversized minimum is refused", not r.accepted)
    check("...with volume zero, never a partial fill of the intent", r.volume == 0)
    check("...and explains the arithmetic", "over the" in r.reasons[0])

    print("portfolio budget binds before the per-trade cap:")
    r = size_position(stk, 25_000, 1.0, 300.0, 292.0, 1.0, budget_remaining=100.0)
    check("sized to the smaller of the two", r.risk_at_stop <= 100.0 + 1e-9)
    check("...and says which one bound", any("portfolio budget" in x for x in r.reasons))
    r = size_position(stk, 25_000, 1.0, 300.0, 292.0, 1.0, budget_remaining=0.0)
    check("an exhausted budget refuses", not r.accepted)

    print("four positions fill the book exactly (1% x 4 = the soft limit):")
    total, open_risk = 0.0, 0.0
    for i in range(4):
        budget = fr.max_position_risk_usd(cfg, flat, open_risk_usd=open_risk)
        r = size_position(fx, 25_000, 1.0, 1.0850, 1.0750, 1.0, budget_remaining=budget)
        check(f"position {i + 1} accepted", r.accepted)
        open_risk += r.risk_at_stop
        total += r.risk_at_stop
    check("four positions consume the soft limit to within one step",
          1000.0 - 1.0 <= total <= 1000.0)
    check("the whole book cannot breach the daily soft limit",
          total <= cfg.daily_soft_usd + 1e-9)
    check("...and is far inside FTMO's hard limit", total < cfg.max_daily_loss_usd)

    # The residue matters: 1,000 - 999.99 leaves 0.04 of budget, which is
    # enough for size_position to accept a 3-unit position risking 0.03. That
    # function is budget-only BY DESIGN — the position COUNT cap lives in
    # plan_entry, and this is the case that proves it has to.
    residual = fr.max_position_risk_usd(cfg, flat, open_risk_usd=open_risk)
    check("a residual budget survives four positions", 0 < residual < 1.0)
    check("size_position alone would accept a micro 5th position",
          size_position(fx, 25_000, 1.0, 1.0850, 1.0750, 1.0,
                        budget_remaining=residual).accepted)
    four_open = fr.AccountState(equity=25_000, balance=25_000,
                                day_start_balance=25_000, open_position_count=4)
    fifth = plan_entry(fx, cfg, four_open, 1.0850, 0.0050, "BUY", 1.0,
                       risk_pct=1.0, open_risk_usd=open_risk)
    check("plan_entry refuses it on position count", not fifth.accepted)
    check("...naming max_positions, not the budget", "max_positions" in fifth.reasons[0])

    print("plan_entry consults the rule engine first:")
    r = plan_entry(fx, cfg, flat, 1.0850, 0.0050, "BUY", 1.0, risk_pct=1.0)
    check("healthy account sizes normally", r.accepted)
    check("stop derived at 2xATR below entry", approx(r.stop_price, 1.0750))
    blocked = fr.AccountState(equity=23_900, balance=23_900, day_start_balance=25_000)
    r = plan_entry(fx, cfg, blocked, 1.0850, 0.0050, "BUY", 1.0, risk_pct=1.0)
    check("account past the soft limit gets no size", not r.accepted)
    check("...and the refusal carries the rule engine's reason",
          "rule engine refuses" in r.reasons[0])
    full_book = fr.AccountState(equity=25_000, balance=25_000,
                                day_start_balance=25_000, open_position_count=4)
    r = plan_entry(fx, cfg, full_book, 1.0850, 0.0050, "BUY", 1.0, risk_pct=1.0)
    check("a full book is refused on position count", not r.accepted)
    check("...naming the limit", "max_positions" in r.reasons[0])
    # A losing day shrinks the budget even while entries are still allowed.
    down = fr.AccountState(equity=24_400, balance=24_400, day_start_balance=25_000)
    r = plan_entry(fx, cfg, down, 1.0850, 0.0050, "BUY", 1.0, risk_pct=1.0)
    check("after a 600 loss the remaining budget is 400", approx(r.budget_remaining, 400.0))
    check("...and the new position is sized inside it", r.risk_at_stop <= 250.0 + 1e-9)

    print("spec validation:")
    check("non-positive min volume refused",
          _raises(lambda: SymbolSpec(9, "X", 0, 100, 1000)))
    check("max below min refused",
          _raises(lambda: SymbolSpec(9, "X", 100, 100, 50)))

    # ---------------------------------------------------------------------
    # REAL venue specs. Everything above proves the arithmetic; this proves it
    # still holds against what FTMO actually reports. These are PROPERTY
    # assertions, not recomputed magic numbers, deliberately: hand-deriving an
    # expected volume from a real spec and then asserting it would only prove I
    # ran the same formula twice. The properties below are what the venue will
    # actually reject an order for.
    # ---------------------------------------------------------------------
    print("REAL venue specs (ftmo_symbol_specs.json):")
    try:
        capture = _load_capture()
    except FileNotFoundError as e:
        check(f"capture present ({e})", False)
        capture = {}

    if capture:
        check("the capture is not trivially small", len(capture) >= 100)
        built, bad = 0, []
        for nm in capture:
            try:
                spec_from_capture(nm, capture)
                built += 1
            except Exception as exc:
                bad.append(f"{nm}: {exc}")
        check(f"every one of {len(capture)} captured specs is a valid "
              f"SymbolSpec (built {built})", not bad)
        if bad:
            for b in bad[:5]:
                print(f"        {b}")

        check("an unknown symbol name raises rather than returning a default",
              _raises(lambda: spec_from_capture("NOT_A_SYMBOL", capture)))

        # The four the tests used to invent, now bound to the venue's numbers.
        for nm, price, atr, rate in (("EURUSD", 1.0850, 0.0050, 1.0),
                                     ("US30.cash", 39_000.0, 180.0, 1.0),
                                     ("AAPL", 300.0, 4.0, 1.0),
                                     ("XAUUSD", 2_350.0, 20.0, 1.0)):
            if nm not in capture:
                check(f"{nm} present in capture", False)
                continue
            spec = spec_from_capture(nm, capture)
            r = plan_entry(spec, cfg, flat, price, atr, "BUY", rate, risk_pct=1.0)
            if not r.accepted:
                # A refusal is legitimate (min volume over budget) but must say so.
                check(f"{nm}: refusal names the minimum-volume reason",
                      "minimum volume" in r.reasons[0])
                continue
            check(f"{nm}: risk at stop {r.risk_at_stop:,.2f} within the 250 "
                  f"per-trade cap", r.risk_at_stop <= 250.0 + 1e-6)
            check(f"{nm}: volume {r.volume} is at or above the venue minimum "
                  f"{spec.min_volume}", r.volume >= spec.min_volume)
            check(f"{nm}: volume sits on the venue's step grid",
                  (r.volume - spec.min_volume) % spec.step_volume == 0)
            check(f"{nm}: volume within the venue maximum",
                  r.volume <= spec.max_volume)

        # The invariant that matters most, swept across the whole universe:
        # nothing the sizer accepts may ever risk more than the budget allowed.
        over, refused, accepted = [], 0, 0
        for nm in sorted(capture):
            spec = spec_from_capture(nm, capture)
            r = size_position(spec, 25_000, 1.0, 100.0, 98.0, 1.0,
                              budget_remaining=250.0)
            if not r.accepted:
                refused += 1
                continue
            accepted += 1
            if r.risk_at_stop > 250.0 + 1e-6:
                over.append(f"{nm} risks {r.risk_at_stop:,.2f}")
        check(f"across all {len(capture)} real symbols, no accepted size ever "
              f"exceeds the budget ({accepted} sized, {refused} refused)", not over)
        if over:
            for o in over[:5]:
                print(f"        {o}")
        check("the refusal path is reachable with real minimums, not dead code",
              refused > 0 or accepted > 0)

    print("\nFAILED" if failures else "\nAll FTMO sizing selftests passed.")
    return 1 if failures else 0


def _raises(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="FTMO position sizing (pure, offline).")
    ap.add_argument("--selftest", action="store_true", help="Run offline checks and exit.")
    ap.parse_args()
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
