#!/usr/bin/env python3
"""
ftmo_rules.py — FTMO Challenge rule engine. Pure logic, no network, no I/O.

This is the safety layer for the FTMO venue, and it is deliberately the first
thing built and the only thing that can be fully verified without credentials.
A bug here does not cost a trade, it costs the account: FTMO's limits are hard
fails with no appeal and no recovery.

WHAT IT DOES: takes a snapshot of account state and answers three separate
questions that are easy to conflate and must not be.

  1. May I OPEN a new position right now?
  2. Must I FLATTEN everything right now?
  3. Could this phase PASS in its current state?

(3) is not a trading permission. A phase that cannot pass yet — too few
trading days, or a best day that breaks the consistency rule — is a reason to
keep trading, not to stop. Modelling it as a block would be exactly backwards.

WHY EQUITY, NOT BALANCE. Every FTMO loss limit is measured on equity, meaning
balance plus floating P&L on open positions. So the account can breach a limit
with no order placed and nothing realised — an overnight gap on a position you
already hold is sufficient. This is the single biggest difference from
ibkr_service.RiskGuard, whose daily-loss breaker reads IBKR's realised
RealizedPnL and is only consulted when an order is being placed. That design
provably cannot see this failure (see CLAUDE.md, the 2026-07-23 GOOGL close),
which is why the FTMO path gets a continuous monitor rather than a pre-trade
gate.

THE THREE THRESHOLDS. FTMO publishes one number per limit; this engine derives
three from it, because stopping exactly at the published number leaves nothing
for slippage, spread or a gap:

    soft    = limit x (1 - buffer)       -> stop OPENING (default 4% of a 5%)
    flatten = limit x (1 - buffer/2)     -> CLOSE EVERYTHING (default 4.5%)
    breach  = limit                      -> the account is already failed

Blocking entries alone is not protection when floating P&L counts, which is
why the flatten tier exists between the two.

PRODUCT DIFFERENCES that change the code, not just constants:

  2-Step: 5% daily, 10% max loss STATIC (floor computed once from initial
          capital), 4 minimum trading days, 10% target then 5% on Verification.
  1-Step: 3% daily, 10% max loss END-OF-DAY TRAILING (floor follows the highest
          closing balance and only ever rises), no minimum trading days, plus
          the Best Day Rule.

The trailing floor updates on DAY BOUNDARIES using the day's closing balance,
never intraday. An intraday spike that gives back before the close must not
raise the floor — getting that wrong would tighten the limit against you on
the basis of profit you never kept. See roll_day().

Offline selftest:  python3 ftmo_rules.py --selftest
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, date
from zoneinfo import ZoneInfo

# FTMO is Prague-based and resets at 00:00 CE(S)T. Europe/Prague tracks the
# CET/CEST switch on its own — do NOT hardcode a UTC offset, and do not use
# host time. Same reasoning as ibkr_service.market_is_open() using
# America/New_York rather than this machine's EEST.
FTMO_TZ = ZoneInfo("Europe/Prague")

PRODUCTS = ("2step", "1step")
PHASES = ("challenge", "verification", "funded")

# Published FTMO objectives, as percentages of initial capital.
_SPEC = {
    "2step": {
        "max_daily_loss_pct": 5.0,
        "max_total_loss_pct": 10.0,
        "trailing_drawdown": False,
        "min_trading_days": 4,
        "best_day_rule": False,
        "targets": {"challenge": 10.0, "verification": 5.0, "funded": 0.0},
    },
    "1step": {
        "max_daily_loss_pct": 3.0,
        "max_total_loss_pct": 10.0,
        "trailing_drawdown": True,
        "min_trading_days": 0,
        "best_day_rule": True,
        "targets": {"challenge": 10.0, "verification": 0.0, "funded": 0.0},
    },
}

# Best Day Rule: the single best day may not exceed this share of the total
# profit made on positive days.
BEST_DAY_MAX_SHARE = 0.50


@dataclass(frozen=True)
class FTMOConfig:
    """Which product, which phase, how much capital, how much headroom.

    `buffer_pct` is the fraction of each published limit held back as reserve.
    0.20 means the bot stops opening at 80% of the limit and flattens at 90%,
    so on a $25,000 2-Step account the $1,250 daily limit becomes "stop opening
    at $1,000, flatten at $1,125, and $1,250 is a number we never reach".
    """
    product: str = "2step"
    phase: str = "challenge"
    initial_capital: float = 25_000.0
    buffer_pct: float = 0.20
    stop_at_target: bool = True

    def __post_init__(self):
        if self.product not in PRODUCTS:
            raise ValueError(f"product must be one of {PRODUCTS}, got {self.product!r}")
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}, got {self.phase!r}")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0.0 <= self.buffer_pct < 1.0:
            raise ValueError("buffer_pct must be in [0, 1)")

    @property
    def _spec(self) -> dict:
        return _SPEC[self.product]

    @property
    def uses_trailing_drawdown(self) -> bool:
        return self._spec["trailing_drawdown"]

    @property
    def has_best_day_rule(self) -> bool:
        return self._spec["best_day_rule"]

    @property
    def min_trading_days(self) -> int:
        return self._spec["min_trading_days"]

    @property
    def max_daily_loss_usd(self) -> float:
        return self.initial_capital * self._spec["max_daily_loss_pct"] / 100.0

    @property
    def max_total_loss_usd(self) -> float:
        return self.initial_capital * self._spec["max_total_loss_pct"] / 100.0

    @property
    def profit_target_usd(self) -> float:
        return self.initial_capital * self._spec["targets"][self.phase] / 100.0

    # --- the three thresholds, derived from each published limit ---
    def _soft(self, limit: float) -> float:
        return limit * (1.0 - self.buffer_pct)

    def _flatten(self, limit: float) -> float:
        return limit * (1.0 - self.buffer_pct / 2.0)

    @property
    def daily_soft_usd(self) -> float:
        return self._soft(self.max_daily_loss_usd)

    @property
    def daily_flatten_usd(self) -> float:
        return self._flatten(self.max_daily_loss_usd)

    @property
    def total_soft_usd(self) -> float:
        return self._soft(self.max_total_loss_usd)

    @property
    def total_flatten_usd(self) -> float:
        return self._flatten(self.max_total_loss_usd)


@dataclass(frozen=True)
class AccountState:
    """A snapshot of the FTMO account, as the monitor observes it.

    `equity` is balance + floating P&L and is what every limit is measured on.
    `day_start_balance` is the BALANCE recorded at 00:00 CE(S)T today — FTMO
    computes the daily limit from that, not from equity at midnight and not
    from the running balance.
    `highest_eod_balance` is the highest CLOSING balance of any completed day,
    used only by the trailing product. Advance it with roll_day(), never
    intraday.
    `daily_profits` is realised profit per COMPLETED day, oldest first, used
    for the Best Day Rule and nothing else.
    """
    equity: float
    balance: float
    day_start_balance: float
    highest_eod_balance: float = 0.0
    open_position_count: int = 0
    trading_days: int = 0
    daily_profits: tuple[float, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuleVerdict:
    """What the engine concluded, with every number it used to conclude it.

    Carries the metrics as well as the decision so the rule-engine log can
    record WHY, not just what — the whole point of a separate audit log is
    being able to reconstruct a decision after the account is gone.
    """
    can_open: bool
    must_flatten: bool
    breached: bool
    reasons: tuple[str, ...]

    daily_loss_used: float
    daily_soft: float
    daily_flatten: float
    daily_hard: float

    drawdown_used: float
    drawdown_soft: float
    drawdown_flatten: float
    drawdown_hard: float
    drawdown_floor_equity: float

    profit_usd: float
    profit_target_usd: float
    target_reached: bool
    min_days_met: bool
    consistency_ok: bool
    can_pass: bool

    def summary(self) -> str:
        state = ("BREACHED" if self.breached else
                 "FLATTEN" if self.must_flatten else
                 "OPEN OK" if self.can_open else "NO NEW ENTRIES")
        return (f"[{state}] daily {self.daily_loss_used:,.2f}/{self.daily_soft:,.2f}"
                f" (hard {self.daily_hard:,.2f}) | drawdown {self.drawdown_used:,.2f}/"
                f"{self.drawdown_soft:,.2f} (hard {self.drawdown_hard:,.2f}) | "
                f"profit {self.profit_usd:,.2f}/{self.profit_target_usd:,.2f}")


def ftmo_day(moment: datetime) -> date:
    """Which FTMO trading day a moment belongs to (00:00 CE(S)T boundary).

    A naive datetime is rejected rather than assumed to be local: guessing the
    zone here would silently shift the daily reset by hours, and the daily
    limit is the fastest way to fail an account.
    """
    if moment.tzinfo is None:
        raise ValueError("ftmo_day() needs a timezone-aware datetime — a naive one "
                         "would silently assume a zone and move the 00:00 CE(S)T reset")
    return moment.astimezone(FTMO_TZ).date()


def drawdown_reference(config: FTMOConfig, state: AccountState) -> float:
    """The equity level the max-loss limit is measured down from.

    Static products measure from initial capital forever. Trailing products
    measure from the highest closing balance ever recorded, and never lower —
    `max()` against initial capital is what makes the floor one-directional
    even if a caller hands in a stale or zero high-water mark.
    """
    if not config.uses_trailing_drawdown:
        return config.initial_capital
    return max(config.initial_capital, state.highest_eod_balance)


def best_day_share(daily_profits) -> float | None:
    """Best day's share of total profit from positive days. None if no profit yet.

    FTMO's consistency rule caps this at 50%. Note a single profitable day
    always scores 1.0 — that is correct and not an edge-case bug: one big day
    and nothing else is precisely the pattern the rule exists to reject.
    """
    positives = [p for p in daily_profits if p > 0]
    if not positives:
        return None
    total = sum(positives)
    if total <= 0:
        return None
    return max(positives) / total


def roll_day(config: FTMOConfig, state: AccountState, closing_balance: float,
             day_profit: float, opened_a_position: bool) -> AccountState:
    """Advance the state across a 00:00 CE(S)T boundary. Pure.

    This is where the trailing high-water mark moves, and ONLY here. Updating
    it from intraday equity would ratchet the floor up on unrealised profit
    that was later given back, tightening the limit against the account on the
    basis of money it never kept.

    `opened_a_position` drives the trading-day count, because FTMO counts a day
    on which a position was OPENED, not merely held.
    """
    return AccountState(
        equity=closing_balance,
        balance=closing_balance,
        day_start_balance=closing_balance,
        highest_eod_balance=max(state.highest_eod_balance, closing_balance),
        open_position_count=state.open_position_count,
        trading_days=state.trading_days + (1 if opened_a_position else 0),
        daily_profits=tuple(state.daily_profits) + (day_profit,),
    )


def evaluate(config: FTMOConfig, state: AccountState) -> RuleVerdict:
    """The whole rule engine. Pure function of (config, state)."""
    reasons: list[str] = []

    # ---- daily loss, measured on equity from this morning's balance ----
    daily_used = max(0.0, state.day_start_balance - state.equity)
    daily_hard = config.max_daily_loss_usd
    daily_soft = config.daily_soft_usd
    daily_flat = config.daily_flatten_usd

    # ---- total loss / drawdown, measured from the product's reference ----
    reference = drawdown_reference(config, state)
    dd_used = max(0.0, reference - state.equity)
    dd_hard = config.max_total_loss_usd
    dd_soft = config.total_soft_usd
    dd_flat = config.total_flatten_usd
    floor_equity = reference - dd_hard

    # ---- phase progress (NOT trading permissions) ----
    profit = state.equity - config.initial_capital
    target = config.profit_target_usd
    target_reached = target <= 0 or profit >= target
    min_days_met = state.trading_days >= config.min_trading_days
    share = best_day_share(state.daily_profits)
    consistency_ok = True
    if config.has_best_day_rule and share is not None:
        consistency_ok = share <= BEST_DAY_MAX_SHARE + 1e-12
    can_pass = target_reached and min_days_met and consistency_ok

    # ---- verdict ----
    breached = daily_used >= daily_hard or dd_used >= dd_hard
    if daily_used >= daily_hard:
        reasons.append(f"BREACH: daily loss {daily_used:,.2f} >= limit {daily_hard:,.2f}")
    if dd_used >= dd_hard:
        reasons.append(f"BREACH: drawdown {dd_used:,.2f} >= limit {dd_hard:,.2f} "
                       f"(equity {state.equity:,.2f} <= floor {floor_equity:,.2f})")

    must_flatten = False
    if state.open_position_count > 0:
        if daily_used >= daily_flat:
            must_flatten = True
            reasons.append(f"FLATTEN: daily loss {daily_used:,.2f} >= flatten threshold "
                           f"{daily_flat:,.2f}")
        if dd_used >= dd_flat:
            must_flatten = True
            reasons.append(f"FLATTEN: drawdown {dd_used:,.2f} >= flatten threshold "
                           f"{dd_flat:,.2f}")
    # A breach is terminal: there is nothing left to protect, but leaving
    # positions open on a failed account serves no purpose either.
    if breached and state.open_position_count > 0:
        must_flatten = True

    can_open = True
    if breached:
        can_open = False
    if daily_used >= daily_soft:
        can_open = False
        reasons.append(f"NO NEW ENTRIES: daily loss {daily_used:,.2f} >= soft limit "
                       f"{daily_soft:,.2f}")
    if dd_used >= dd_soft:
        can_open = False
        reasons.append(f"NO NEW ENTRIES: drawdown {dd_used:,.2f} >= soft limit "
                       f"{dd_soft:,.2f}")
    if config.stop_at_target and target > 0 and profit >= target:
        can_open = False
        reasons.append(f"TARGET REACHED: profit {profit:,.2f} >= target {target:,.2f} — "
                       f"holding the pass rather than risking it back")

    if not reasons:
        reasons.append("ok")

    return RuleVerdict(
        can_open=can_open, must_flatten=must_flatten, breached=breached,
        reasons=tuple(reasons),
        daily_loss_used=daily_used, daily_soft=daily_soft,
        daily_flatten=daily_flat, daily_hard=daily_hard,
        drawdown_used=dd_used, drawdown_soft=dd_soft,
        drawdown_flatten=dd_flat, drawdown_hard=dd_hard,
        drawdown_floor_equity=floor_equity,
        profit_usd=profit, profit_target_usd=target,
        target_reached=target_reached, min_days_met=min_days_met,
        consistency_ok=consistency_ok, can_pass=can_pass,
    )


def max_position_risk_usd(config: FTMOConfig, state: AccountState,
                          open_risk_usd: float = 0.0) -> float:
    """How much NEW risk the book can take, in USD at the stop.

    The portfolio constraint chosen for this account: the sum of every open
    position's stop distance must stay inside the daily soft limit, so a
    simultaneous stop-out across the whole book lands ON the soft limit rather
    than through the hard one. Without this, N positions sized independently
    can each be within their per-trade cap and still breach the day together —
    which is exactly what a correlated multi-asset book produces.

    Returns 0.0 when there is no headroom, never a negative number.
    """
    headroom = config.daily_soft_usd - max(0.0, open_risk_usd)
    # Already-realised loss today eats into the same budget.
    headroom -= max(0.0, state.day_start_balance - state.equity)
    return max(0.0, headroom)


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline checks. No network, no credentials, no FTMO account needed."""
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    cfg2 = FTMOConfig(product="2step", phase="challenge", initial_capital=25_000.0)
    cfg1 = FTMOConfig(product="1step", phase="challenge", initial_capital=25_000.0)

    print("published limits translate to dollars:")
    check("2-Step daily 5% of 25k = 1250", abs(cfg2.max_daily_loss_usd - 1250) < 1e-9)
    check("2-Step total 10% of 25k = 2500", abs(cfg2.max_total_loss_usd - 2500) < 1e-9)
    check("2-Step target 10% of 25k = 2500", abs(cfg2.profit_target_usd - 2500) < 1e-9)
    check("1-Step daily 3% of 25k = 750", abs(cfg1.max_daily_loss_usd - 750) < 1e-9)
    check("Verification target is 5%, not 10%",
          abs(FTMOConfig(product="2step", phase="verification",
                         initial_capital=25_000.0).profit_target_usd - 1250) < 1e-9)
    check("funded phase has no profit target",
          FTMOConfig(product="2step", phase="funded").profit_target_usd == 0.0)

    print("the three thresholds:")
    check("2-Step soft daily = 1000 (4% of 25k)", abs(cfg2.daily_soft_usd - 1000) < 1e-9)
    check("2-Step flatten daily = 1125 (4.5%)", abs(cfg2.daily_flatten_usd - 1125) < 1e-9)
    check("2-Step soft drawdown = 2000 (8%)", abs(cfg2.total_soft_usd - 2000) < 1e-9)
    check("zero buffer collapses soft onto the hard limit",
          FTMOConfig(initial_capital=25_000.0, buffer_pct=0.0).daily_soft_usd == 1250)

    print("daily loss is measured on EQUITY, so floating P&L counts:")
    flat_ok = AccountState(equity=25_000, balance=25_000, day_start_balance=25_000)
    check("flat account can open", evaluate(cfg2, flat_ok).can_open)
    # Balance untouched, but an open position is 1,050 underwater — past the
    # 1,000 soft limit, still inside the 1,125 flatten threshold.
    floating = AccountState(equity=23_950, balance=25_000, day_start_balance=25_000,
                            open_position_count=1)
    v = evaluate(cfg2, floating)
    check("unrealised loss past soft blocks new entries", not v.can_open)
    check("...but between soft and flatten, positions are left alone",
          not v.must_flatten)
    check("...and it is not yet a breach", not v.breached)
    check("balance alone would have seen nothing", floating.balance == 25_000)
    # 1,200 underwater: past flatten (1,125), still inside the hard 1,250.
    deeper = replace(floating, equity=23_800)
    v = evaluate(cfg2, deeper)
    check("unrealised loss past flatten closes the book", v.must_flatten)
    check("...and that is still not a breach", not v.breached)

    print("breach detection:")
    dead = AccountState(equity=23_750, balance=23_750, day_start_balance=25_000)
    check("daily loss exactly at the limit is a breach", evaluate(cfg2, dead).breached)
    check("a breach also stops opening", not evaluate(cfg2, dead).can_open)
    near = AccountState(equity=23_751, balance=23_751, day_start_balance=25_000)
    check("one dollar inside the limit is not a breach", not evaluate(cfg2, near).breached)

    print("flatten tier only fires when something is open:")
    no_pos = AccountState(equity=23_875, balance=23_875, day_start_balance=25_000,
                          open_position_count=0)
    check("nothing open -> no flatten instruction", not evaluate(cfg2, no_pos).must_flatten)
    check("nothing open -> still blocks new entries", not evaluate(cfg2, no_pos).can_open)

    print("static vs trailing drawdown:")
    # Account ran up to 27,000 at a close, then fell back to 25,500.
    up = AccountState(equity=25_500, balance=25_500, day_start_balance=25_500,
                      highest_eod_balance=27_000)
    check("static reference stays at initial capital",
          drawdown_reference(cfg2, up) == 25_000)
    check("trailing reference follows the high-water close",
          drawdown_reference(cfg1, up) == 27_000)
    check("static: 25,500 is above initial, so zero drawdown",
          evaluate(cfg2, up).drawdown_used == 0.0)
    check("trailing: same equity is 1,500 below its high-water mark",
          abs(evaluate(cfg1, up).drawdown_used - 1500) < 1e-9)
    check("trailing floor never drops below initial - limit",
          drawdown_reference(cfg1, AccountState(equity=1, balance=1, day_start_balance=1,
                                                highest_eod_balance=0.0)) == 25_000)

    print("roll_day advances the high-water mark only at the close:")
    s0 = AccountState(equity=26_800, balance=25_000, day_start_balance=25_000,
                      highest_eod_balance=25_000)
    check("intraday equity spike has NOT moved the mark", s0.highest_eod_balance == 25_000)
    s1 = roll_day(cfg1, s0, closing_balance=25_400, day_profit=400.0,
                  opened_a_position=True)
    check("close of 25,400 sets the mark, not the 26,800 spike",
          s1.highest_eod_balance == 25_400)
    check("day start rolls to the closing balance", s1.day_start_balance == 25_400)
    check("trading day counted when a position was opened", s1.trading_days == 1)
    s2 = roll_day(cfg1, s1, closing_balance=25_100, day_profit=-300.0,
                  opened_a_position=False)
    check("a losing day does NOT lower the mark", s2.highest_eod_balance == 25_400)
    check("a day with no new position is not a trading day", s2.trading_days == 1)
    check("daily profits accumulate in order", s2.daily_profits == (400.0, -300.0))

    print("minimum trading days is a pass condition, not a trading block:")
    few = AccountState(equity=27_500, balance=27_500, day_start_balance=27_500,
                       trading_days=2)
    v = evaluate(cfg2, few)
    check("target hit but only 2 of 4 days -> cannot pass yet", not v.can_pass)
    check("...and that must NOT be reported as a breach", not v.breached)
    check("...nor force a flatten", not v.must_flatten)
    enough = replace(few, trading_days=4)
    check("4 trading days -> can pass", evaluate(cfg2, enough).can_pass)
    check("2-Step requires 4 days", cfg2.min_trading_days == 4)
    check("1-Step requires none", cfg1.min_trading_days == 0)

    print("Best Day Rule (1-Step only):")
    check("one big day alone scores 1.0", best_day_share((500.0,)) == 1.0)
    check("no profitable days -> undefined", best_day_share((-10.0, -5.0)) is None)
    check("even split scores 0.5", abs(best_day_share((250.0, 250.0)) - 0.5) < 1e-12)
    check("losses are excluded from the denominator",
          abs(best_day_share((300.0, 300.0, -1000.0)) - 0.5) < 1e-12)
    # Below target on purpose, so this isolates the consistency rule from
    # stop_at_target — conflating the two hid a real distinction the first
    # time these tests were written.
    lumpy = AccountState(equity=26_000, balance=26_000, day_start_balance=26_000,
                         trading_days=6, daily_profits=(900.0, 50.0, 50.0))
    check("1-Step: lumpy profits fail consistency", not evaluate(cfg1, lumpy).consistency_ok)
    check("1-Step: lumpy profits therefore cannot pass", not evaluate(cfg1, lumpy).can_pass)
    check("1-Step: but trading is NOT blocked by it", evaluate(cfg1, lumpy).can_open)
    check("2-Step ignores the Best Day Rule entirely",
          evaluate(cfg2, lumpy).consistency_ok)
    even = replace(lumpy, daily_profits=(340.0, 330.0, 330.0))
    check("1-Step: evenly-earned profit passes consistency",
          evaluate(cfg1, even).consistency_ok)
    # Consistency is the ONLY thing standing between this state and a pass.
    at_target = AccountState(equity=27_500, balance=27_500, day_start_balance=27_500,
                             trading_days=6, daily_profits=(2200.0, 100.0, 200.0))
    check("2-Step: target + days met -> passes despite lumpy profits",
          evaluate(cfg2, at_target).can_pass)
    check("1-Step: same state blocked purely by the Best Day Rule",
          not evaluate(cfg1, at_target).can_pass
          and evaluate(cfg1, at_target).target_reached)

    print("profit target stops further risk when configured:")
    won = AccountState(equity=27_600, balance=27_600, day_start_balance=27_600,
                       trading_days=4)
    check("target reached -> no new entries", not evaluate(cfg2, won).can_open)
    check("target reached -> can pass", evaluate(cfg2, won).can_pass)
    check("target reached is not a flatten", not evaluate(cfg2, won).must_flatten)
    keep_going = FTMOConfig(product="2step", phase="challenge",
                            initial_capital=25_000.0, stop_at_target=False)
    check("stop_at_target=False keeps trading past the target",
          evaluate(keep_going, won).can_open)

    print("portfolio risk budget:")
    fresh = AccountState(equity=25_000, balance=25_000, day_start_balance=25_000)
    check("empty book has the full soft limit to spend",
          abs(max_position_risk_usd(cfg2, fresh) - 1000) < 1e-9)
    check("750 already at risk leaves 250",
          abs(max_position_risk_usd(cfg2, fresh, open_risk_usd=750) - 250) < 1e-9)
    check("a full book leaves nothing",
          max_position_risk_usd(cfg2, fresh, open_risk_usd=1000) == 0.0)
    check("never negative",
          max_position_risk_usd(cfg2, fresh, open_risk_usd=99_999) == 0.0)
    down = AccountState(equity=24_600, balance=24_600, day_start_balance=25_000)
    check("today's realised loss eats the same budget",
          abs(max_position_risk_usd(cfg2, down) - 600) < 1e-9)
    check("4 positions x 250 (1% of 25k) exactly fills the budget",
          abs(max_position_risk_usd(cfg2, fresh) - 4 * 250) < 1e-9)

    print("the 00:00 CE(S)T day boundary:")
    check("naive datetime is refused, not guessed",
          _raises(lambda: ftmo_day(datetime(2026, 8, 2, 23, 0))))
    # 23:30 UTC on 2 Aug is 01:30 on 3 Aug in Prague (CEST, UTC+2).
    late = datetime(2026, 8, 2, 23, 30, tzinfo=ZoneInfo("UTC"))
    check("late-UTC moment belongs to the NEXT Prague day",
          ftmo_day(late) == date(2026, 8, 3))
    # 21:00 UTC on 2 Aug is 23:00 the same day in Prague.
    early = datetime(2026, 8, 2, 21, 0, tzinfo=ZoneInfo("UTC"))
    check("21:00 UTC is still the same Prague day", ftmo_day(early) == date(2026, 8, 2))
    # Winter: CET is UTC+1, so the boundary moves.
    winter = datetime(2026, 1, 15, 23, 30, tzinfo=ZoneInfo("UTC"))
    check("winter CET boundary handled without hardcoding an offset",
          ftmo_day(winter) == date(2026, 1, 16))
    ny = datetime(2026, 8, 2, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    check("a New York evening is already tomorrow in Prague",
          ftmo_day(ny) == date(2026, 8, 3))

    print("config validation:")
    check("unknown product refused", _raises(lambda: FTMOConfig(product="3step")))
    check("unknown phase refused", _raises(lambda: FTMOConfig(phase="bonus")))
    check("non-positive capital refused", _raises(lambda: FTMOConfig(initial_capital=0)))
    check("buffer of 1.0 refused", _raises(lambda: FTMOConfig(buffer_pct=1.0)))

    print("\nFAILED" if failures else "\nAll FTMO rule-engine selftests passed.")
    return 1 if failures else 0


def _raises(fn) -> bool:
    try:
        fn()
    except (ValueError, TypeError):
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="FTMO rule engine (pure logic, offline).")
    ap.add_argument("--selftest", action="store_true", help="Run offline checks and exit.")
    ap.add_argument("--show", action="store_true",
                    help="Print the derived limits for a config and exit.")
    ap.add_argument("--product", choices=PRODUCTS, default="2step")
    ap.add_argument("--phase", choices=PHASES, default="challenge")
    ap.add_argument("--capital", type=float, default=25_000.0)
    ap.add_argument("--buffer", type=float, default=0.20)
    args = ap.parse_args()

    if args.selftest or not args.show:
        return selftest()

    c = FTMOConfig(product=args.product, phase=args.phase,
                   initial_capital=args.capital, buffer_pct=args.buffer)
    print(f"FTMO {args.product} / {args.phase} / ${c.initial_capital:,.0f} "
          f"/ buffer {c.buffer_pct:.0%}")
    print(f"  daily loss    hard ${c.max_daily_loss_usd:,.2f}  "
          f"flatten ${c.daily_flatten_usd:,.2f}  soft ${c.daily_soft_usd:,.2f}")
    print(f"  max loss      hard ${c.max_total_loss_usd:,.2f}  "
          f"flatten ${c.total_flatten_usd:,.2f}  soft ${c.total_soft_usd:,.2f}"
          f"   ({'trailing' if c.uses_trailing_drawdown else 'static'})")
    print(f"  profit target ${c.profit_target_usd:,.2f}")
    print(f"  min trading days {c.min_trading_days}"
          f"   best-day rule: {'yes' if c.has_best_day_rule else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
