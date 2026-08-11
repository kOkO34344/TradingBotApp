#!/usr/bin/env python3
"""
ftmo_signal.py — Kronos ranking -> sized, stop-protected FTMO orders.

The join between the research side and the venue side. `ftmo_rules` decides
whether trading is allowed, `ftmo_sizing` decides how big, `ftmo_session`
talks to the broker; this module decides WHAT, and refuses to hand anything
downstream that those three would reject.

READ THIS BEFORE TRUSTING ANY OUTPUT OF THIS FILE.

Kronos has **no demonstrated edge on any asset class this project has ever
measured.** All four classes were IC-screened on 2026-08-03 and all four
failed (|t| <= 1.55 in every direction), and the matched momentum baseline
failed all four as well. Rule 9 says Kronos may only trade a class that has
passed its own screen, so on the evidence NOTHING here should fire.

It runs anyway, on the owner's explicit instruction of 2026-08-05, given with
that evidence stated. **That is a THIRD deliberate exception to rule 5, after
the retired IBKR runner (rule 7) and the unattended FTMO path (rule 9). Flag it
that way; it is not precedent, and it is not a validated strategy.** What
autonomy removes is the human approval step — never a limit. Every order still
passes the rule engine, the sizer's per-trade and per-portfolio caps, and the
stop validation in `ftmo_session`.

THE UNIVERSE IS USD-QUOTED ONLY, AND THAT IS A CORRECTNESS CONSTRAINT, NOT A
PREFERENCE. `ftmo_sizing.size_position` takes `quote_to_account_rate` with no
default and refuses a non-positive one, because this project has already
misstated equity by ~29% by inverting an FX rate. Every symbol below is quoted
in USD on a USD account, so the rate is exactly 1.0 and is *verified* from the
captured spec rather than assumed. Adding `GER40.cash` (EUR) or `USDJPY` (JPY)
means sourcing a live conversion rate first; `build_universe()` refuses them
rather than quietly passing 1.0.

Offline selftest:  python3 ftmo_signal.py --selftest
Dry run (live data, places NOTHING):  python3 ftmo_signal.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import ftmo_rules as fr
import ftmo_service as svc
import ftmo_sizing as fz
import ftmo_session as fs
import signal_policy as sp

BASE_DIR = Path(__file__).resolve().parent
SETTINGS = BASE_DIR / "trader_settings.json"

# The multi-asset basket: every class represented, every symbol USD-quoted.
# Chosen 2026-08-05 from the real 202-symbol capture, not from memory of what
# a broker "usually" offers.
DEFAULT_UNIVERSE = {
    "indices":     ["US30.cash", "US500.cash"],
    "fx":          ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],
    "commodities": ["XAUUSD", "XAGUSD", "USOIL.cash", "NATGAS.cash"],
    "crypto":      ["BTCUSD", "ETHUSD", "SOLUSD", "LTCUSD"],
}

# The venue's own grouping, mapped onto THIS project's asset classes.
#
# Not 1:1, and the exception is the whole reason this table exists: category 13
# mixes equity indices (US30.cash, US500.cash) with energy (USOIL.cash,
# HEATOIL.c). Rule 9's gate is per ASSET CLASS, so taking the venue's grouping
# at face value would file an index trade under the commodities screen and vice
# versa — a mislabelled trade against a gate is worse than no label.
#
# Anything whose category is unknown is REFUSED rather than bucketed into a
# default. A symbol we cannot classify is a symbol we cannot check a screen
# for, and rule 9 is not satisfiable by guessing.
CATEGORY_CLASS = {12: "stocks", 10: "crypto", 8: "commodities",
                  9: "commodities", 15: "commodities", 11: "fx", 16: "fx"}

# Category 13 has to be split by name. Kept explicit and small rather than
# pattern-matched on ".cash": UKOIL.cash and US500.cash share that suffix and
# are not the same asset class.
CATEGORY_13_INDICES = {"US30.cash", "US500.cash", "US100.cash", "US2000.cash"}

# ~12 months of daily bars — the same trailing-return quantity momentum
# rotation's 18.5% CAGR was measured on. See momentum_rank() for why matching
# the quantity does NOT mean inheriting the result.
MOMENTUM_LOOKBACK_BARS = 252

TOP_N = 4               # candidate slots; the portfolio budget truncates them
ATR_PERIOD = 14
BARS_NEEDED = 420       # Kronos LOOKBACK is 400; a little slack for gaps

# MUST equal kronos_agent.PRED_LEN. Duplicated rather than imported because
# `import kronos_agent` pulls torch (~2 GB), and ftmo_runner's selftests assert
# that nothing loads the model before the arm check and the trading window —
# importing it here to read one integer would defeat both. The selftest reads
# kronos_agent's SOURCE and fails if the two drift apart, so this is a checked
# copy rather than a remembered one.
FORECAST_HORIZON_BARS = 5


@dataclass(frozen=True)
class Candidate:
    symbol: str
    asset_class: str
    predicted_return_pct: float
    last_close: float
    atr: float


def load_universe(settings_path: Path | None = None) -> dict:
    """The configured universe, or the default basket.

    Config lives under `ftmo.universe` in trader_settings.json so the traded
    set is an explicit, reviewable edit rather than a constant buried here.
    """
    path = settings_path or SETTINGS
    try:
        cfg = json.loads(path.read_text()).get("ftmo", {}).get("universe")
    except (OSError, json.JSONDecodeError):
        cfg = None
    return cfg or DEFAULT_UNIVERSE


# What `ftmo.universe_source` may say, and what each means.
#
#   "capture"  every USD-quoted, ENABLED, classifiable symbol the venue itself
#              reported — ~101 of the 202 in ftmo_symbol_specs.json
#   "default"  the hand-written 14-symbol basket in DEFAULT_UNIVERSE
#
# An explicit `ftmo.universe` in trader_settings.json OVERRIDES both, because a
# hand-written list is the most specific statement of intent available and the
# config is meant to be the reviewable record of what may be traded.
UNIVERSE_SOURCES = ("capture", "default")
DEFAULT_UNIVERSE_SOURCE = "capture"


def resolve_universe(specs: dict, settings_path: Path | None = None
                     ) -> tuple[dict, str]:
    """The universe to trade, and a one-line account of where it came from.

    Returns (universe, provenance). The provenance string is logged and
    audited, never dropped: this decides what the bot may buy, and "which set
    of symbols was this firing even looking at" must be answerable from the log
    afterwards rather than reconstructed from whichever settings file happened
    to be on disk at the time.

    Precedence, most specific first:

      1. `ftmo.universe` — an explicit hand-written mapping. Wins outright.
         An explicitly EMPTY mapping is honoured and flows through to
         `build_universe`, which raises. That is deliberate and predates this
         function: emptying the config is how someone turns the bot off, and
         having that resurrect a full basket would be the worst possible
         reading of it.
      2. `ftmo.universe_source` — "capture" (the default) or "default".
      3. Nothing configured — "capture".

    Owner instruction, 2026-08-11: forecast everything the FTMO account can
    actually trade, not the 14-symbol basket. `universe_from_capture` already
    existed for exactly this and had never been wired into the runner, so the
    change here is a routing decision rather than new derivation logic.

    An unrecognised `universe_source` RAISES rather than falling back. A
    typo'd source silently reverting to 14 symbols is precisely the class of
    quiet mismatch this project keeps paying for.
    """
    path = settings_path or SETTINGS
    try:
        ftmo_cfg = json.loads(path.read_text()).get("ftmo", {})
    except (OSError, json.JSONDecodeError):
        ftmo_cfg = {}

    explicit = ftmo_cfg.get("universe")
    if explicit is not None:
        n = sum(len(v) for v in explicit.values()) if explicit else 0
        return explicit, f"explicit ftmo.universe in settings ({n} symbols)"

    source = ftmo_cfg.get("universe_source", DEFAULT_UNIVERSE_SOURCE)
    if source not in UNIVERSE_SOURCES:
        raise ValueError(
            f"ftmo.universe_source={source!r} is not one of "
            f"{', '.join(UNIVERSE_SOURCES)}. Refusing to guess: a typo here "
            f"would silently shrink the traded universe.")

    if source == "default":
        n = sum(len(v) for v in DEFAULT_UNIVERSE.values())
        return DEFAULT_UNIVERSE, f"DEFAULT_UNIVERSE basket ({n} symbols)"

    derived = universe_from_capture(specs)
    n = sum(len(v) for v in derived.values())
    breakdown = ", ".join(f"{k} {len(v)}" for k, v in sorted(derived.items()))
    return derived, (f"venue symbol capture ({n} of {len(specs)} symbols "
                     f"tradeable and USD-quoted: {breakdown})")


def universe_from_capture(specs: dict) -> dict:
    """Derive the tradeable universe from the venue's own symbol capture.

    Returns {asset_class: [symbols]}, the same shape `ftmo.universe` takes in
    trader_settings.json, so it is a drop-in for the hand-written basket.

    Runtime-derived rather than hardcoded (owner request, 2026-08-08): when the
    venue's listing changes, re-running `ftmo_service.py --symbols` changes the
    universe, instead of a constant here drifting out of step with what can
    actually be traded.

    **Only USD-quoted symbols are included, so this returns ~102 of 202.**
    That is not a filter choice made here — `build_universe` refuses a non-USD
    quote outright because sizing needs a real conversion rate and will not
    assume 1.0. Including them would produce a universe that raises on every
    run.

    Symbols are also dropped if `trading_mode` is not ENABLED, or if their
    category is unknown. An unclassifiable symbol cannot be checked against a
    rule 9 screen, and a default bucket would silently mislabel it.
    """
    out: dict[str, list] = {}
    for sym, spec in sorted(specs.items()):
        if spec.get("quote_asset") != "USD":
            continue
        if spec.get("trading_mode") not in (None, "ENABLED"):
            continue
        cid = spec.get("category_id")
        if cid == 13:
            cls = "indices" if sym in CATEGORY_13_INDICES else "commodities"
        else:
            cls = CATEGORY_CLASS.get(cid)
        if cls is None:
            continue
        out.setdefault(cls, []).append(sym)
    return out


def build_universe(specs: dict, universe: dict | None = None) -> list[tuple[str, str]]:
    """Flatten to [(symbol, asset_class)], refusing anything unsafe to size.

    Refuses rather than skips-with-a-warning on a non-USD quote: a silently
    dropped symbol is a universe that is quietly smaller than the config says,
    and the whole point of the config is that it is the reviewable record of
    what may be traded.
    """
    # `is None` and NOT `or`: an explicitly empty universe means "trade
    # nothing", which must raise rather than silently fall back to the default
    # basket. Emptying the config is how someone turns this off, and having
    # that resurrect a full basket would be the worst possible reading of it.
    universe = DEFAULT_UNIVERSE if universe is None else universe
    out, problems = [], []
    for asset_class, symbols in universe.items():
        for sym in symbols:
            spec = specs.get(sym)
            if spec is None:
                problems.append(f"{sym}: not in the symbol capture")
                continue
            if spec.get("quote_asset") != "USD":
                problems.append(
                    f"{sym}: quoted in {spec.get('quote_asset')}, not USD — "
                    f"sizing needs a real conversion rate, and this module "
                    f"refuses to assume 1.0")
                continue
            if spec.get("trading_mode") not in (None, "ENABLED"):
                problems.append(f"{sym}: tradingMode={spec.get('trading_mode')}")
                continue
            out.append((sym, asset_class))
    if problems:
        raise ValueError("universe is not tradeable as configured:\n  " +
                         "\n  ".join(problems))
    if not out:
        raise ValueError("universe is empty")
    return out


def atr_from_bars(bars: list[dict], period: int = ATR_PERIOD) -> float:
    """True-range ATR over the last `period` bars.

    Deliberately the full true range (including gaps against the previous
    close) rather than high-low. On a 24/7 crypto CFD the difference is small;
    on an index CFD that gaps over a weekend it is exactly the risk the stop
    exists to cover, and a high-low ATR would size the position too big.
    """
    if len(bars) < period + 1:
        raise ValueError(f"need {period + 1} bars for ATR, got {len(bars)}")
    trs = []
    for prev, cur in zip(bars[-(period + 1):-1], bars[-period:]):
        trs.append(max(cur["high"] - cur["low"],
                       abs(cur["high"] - prev["close"]),
                       abs(cur["low"] - prev["close"])))
    return sum(trs) / len(trs)


def rank_candidates(pred_dfs: dict, bars_by_symbol: dict,
                    classes: dict) -> list[Candidate]:
    """Predicted % change from last close to the end of the forecast."""
    out = []
    for sym, pred in pred_dfs.items():
        bars = bars_by_symbol[sym]
        last_close = bars[-1]["close"]
        if last_close <= 0:
            continue
        predicted = float(pred["close"].iloc[-1])
        out.append(Candidate(
            symbol=sym, asset_class=classes.get(sym, "?"),
            predicted_return_pct=(predicted - last_close) / last_close * 100.0,
            last_close=last_close, atr=atr_from_bars(bars)))
    out.sort(key=lambda c: c.predicted_return_pct, reverse=True)
    return out


def momentum_rank(bars_by_symbol: dict, lookback: int = MOMENTUM_LOOKBACK_BARS,
                  allow_momentum: bool = False) -> list[tuple[str, float]]:
    """Trailing-return ranking over venue bars. [(symbol, return), ...] desc.

    GATED. Raises `SignalDisabled` unless `allow_momentum=True` (rule 8). The
    gate is at the top of the computation rather than at the call sites, for
    the same reason the retired IBKR signal path put it there: a gate a
    caller has to remember is a gate that gets forgotten.

    **Read this before treating the output as "the 18.5% CAGR strategy".**
    Momentum rotation earned that number on a MONTHLY rebalance of a 12-month
    trailing return over US large caps. This computes the same *quantity* from
    daily venue bars, but the FTMO runner fires ~20 times a day, so the signal
    is being sampled roughly 600x more often than the cadence it was measured
    at. Same arithmetic, different strategy. Nothing here inherits that CAGR.

    Symbols with fewer than `lookback + 1` bars are omitted rather than ranked
    off a shorter window — comparing a 252-bar return against a 90-bar one puts
    them on the same list at different scales, which is a ranking bug that
    looks like a signal.
    """
    sp.assert_allowed("momentum", allow_momentum,
                      context="ftmo_signal.momentum_rank")
    out = []
    for sym, bars in bars_by_symbol.items():
        if bars is None or len(bars) < lookback + 1:
            continue
        closes = [b["close"] for b in bars] if isinstance(bars, list) else \
            list(bars["Close"] if "Close" in bars else bars["close"])
        past, now = closes[-(lookback + 1)], closes[-1]
        if past <= 0:
            continue
        out.append((sym, now / past - 1.0))
    return sorted(out, key=lambda kv: kv[1], reverse=True)


def apply_kronos_veto(momentum_ranked: list[tuple[str, float]],
                      kronos_by_symbol: dict, top_n: int = TOP_N
                      ) -> tuple[list[str], list[dict]]:
    """The AND gate. Momentum picks; Kronos vetoes any pick it forecasts <= 0.

    Owner decision, 2026-08-08. Returns (selected, vetoed) — the vetoes are
    returned rather than dropped so the runner can journal WHY a momentum pick
    did not trade. A filter whose rejections are invisible cannot be audited
    against the trades it prevented.

    Kronos can only ever REMOVE. It never promotes a symbol momentum did not
    pick, and it never reorders momentum's ranking. That asymmetry is the point
    of this design: momentum is the signal with measured edge and stays the
    decision-maker; Kronos is a false-positive filter and nothing more.

    **The gate is only worth having if Kronos's directional call beats a coin
    flip at this horizon.** At 20 days it was 50.0%; hourly it was 46.4%. If
    5-day lands at ~50% too, this removes momentum picks at RANDOM, which makes
    the hybrid strictly worse than momentum alone rather than safer. That is a
    question for the IC screen, not for this function.

    A symbol with NO Kronos forecast is vetoed, not waved through: the gate is
    an AND, and a missing operand cannot satisfy it.
    """
    selected, vetoed = [], []
    for sym, mom in momentum_ranked:
        if len(selected) >= top_n:
            break
        pred = kronos_by_symbol.get(sym)
        if pred is None:
            vetoed.append({"symbol": sym, "momentum": mom, "kronos": None,
                           "reason": "no Kronos forecast — an AND gate cannot "
                                     "pass on a missing operand"})
            continue
        if pred <= 0:
            vetoed.append({"symbol": sym, "momentum": mom, "kronos": pred,
                           "reason": f"Kronos forecasts {pred:+.2f}% — "
                                     f"disagrees with momentum's long"})
            continue
        selected.append(sym)
    return selected, vetoed


# How many candidates one asset class may contribute to the selection pool.
#
# 1 means the classes compete through their own leaders only: the pool becomes
# {best crypto, best stock, best index, best FX pair, best commodity} and a
# top_n of 4 necessarily spans four different classes.
#
# WHY (owner instruction, 2026-08-11, immediately after the universe went from
# 14 symbols to 101): ranking 101 symbols on predicted PERCENTAGE return is a
# contest scored on amplitude, and amplitude is what the noisiest instrument
# has most of. The first live dry-run on the full universe returned GALUSD
# +47.67%, VECUSD +25.51%, IMXUSD +18.11%, MANUSD +16.65% as its top four —
# every one a crypto priced in fractions of a cent, ahead of every index,
# every FX pair and all 46 stock CFDs. A five-day +47% forecast on a
# micro-cap alt-coin is not a stronger signal than +2% on an index; it is the
# same nothing measured on a wider ruler.
#
# 0 (or None) disables the cap and restores pure global ranking.
DEFAULT_MAX_PER_CLASS = 1


def cap_per_class(ranked: list[Candidate],
                  max_per_class: int | None = DEFAULT_MAX_PER_CLASS
                  ) -> list[Candidate]:
    """Rank WITHIN each asset class, then let the class leaders compete.

    Returns `ranked` filtered so no asset class contributes more than
    `max_per_class` candidates, preserving the original global order. Since
    `rank_candidates` already sorts by predicted return descending, taking the
    first `max_per_class` occurrences of each class IS that class's own
    internal ranking — no second sort, and no separate notion of "score" that
    could drift from the one everything else uses.

    That last point is the reason this is a FILTER and not a re-scoring.
    `apply_rotation_margin` compares raw predicted-return differences against
    `margin_pct`, which is calibrated to an observed ~1-point sampling spread
    and not to theory. Normalising returns into z-scores or percentiles would
    have silently changed the units that margin is measured in, and this
    project has already shipped one inverted-hysteresis bug that traded live
    for a day. A filter leaves every downstream comparison in the units it was
    calibrated in; only the POOL those comparisons run over gets smaller.

    A held position that is no longer its class's leader drops out of the pool
    and will therefore be rotated out. That is the cap doing its job rather
    than a bug: holding two cryptos is exactly the concentration it exists to
    prevent. Exits do not consult this — `plan_orders` computes them from
    `held` and the target, and rule 3 keeps every exit path ungated.
    """
    if not max_per_class or max_per_class < 1:
        return list(ranked)
    seen: dict[str, int] = {}
    out = []
    for cand in ranked:
        n = seen.get(cand.asset_class, 0)
        if n >= max_per_class:
            continue
        seen[cand.asset_class] = n + 1
        out.append(cand)
    return out


def rank_boundary_gap(ranked: list[Candidate], n: int = TOP_N) -> float | None:
    """Predicted-return gap between rank N and rank N+1.

    Printed with every proposal because this project has measured Kronos's
    top-N flipping between two runs on identical data when that gap is around
    a point. A narrow gap means the selection is a coin flip, not a decision.
    """
    if len(ranked) <= n:
        return None
    return ranked[n - 1].predicted_return_pct - ranked[n].predicted_return_pct


def apply_rotation_margin(held: list[str], ranked: list[Candidate],
                          margin_pct: float, n: int = TOP_N) -> list[str]:
    """Incumbents keep their slot unless beaten by more than `margin_pct`.

    Same hysteresis the retired IBKR rotation used (removed 2026-08-09),
    and for the same measured reason: two runs 30 minutes apart on identical
    closed-market data produced different top-3s because two names sat ~1
    point apart. This suppresses churn; it does NOT create edge. The IC is
    still ~0 — the margin only stops us paying spread to act on noise.
    """
    by_symbol = {c.symbol: c for c in ranked}
    challengers = [c for c in ranked if c.symbol not in held]
    keepers = [s for s in held if s in by_symbol]
    keepers.sort(key=lambda s: by_symbol[s].predicted_return_pct, reverse=True)

    chosen = []
    for sym in keepers:
        if len(chosen) >= n:
            break
        incumbent = by_symbol[sym]
        better = [c for c in challengers
                  if c.predicted_return_pct - incumbent.predicted_return_pct
                  > margin_pct]
        # `>= n`, NOT `if not better`. An incumbent should only lose its slot
        # when enough challengers beat it by the margin to actually push it out
        # of the top N — a single better challenger does not, because there are
        # n slots.
        #
        # The old `if not better` inverted the hysteresis it was built to
        # provide: an incumbent was HARDER to keep than a newcomer at the same
        # rank, so the mechanism meant to suppress churn was causing it, paying
        # spread both ways ~20 times a day. Reproduced live on 2026-08-07 21:32
        # — the runner sold ETHUSD (predicted +9.51%) to buy EURUSD (predicted
        # -0.15%), rank 4 out and rank 5 in.
        #
        # It also broke `margin_pct=0`, which is documented as "restore strict
        # ranking": with `if not better`, any challenger ahead by any amount
        # evicted the incumbent regardless of rank. The old selftest asserting
        # that passed only because its fixture never had a challenger below the
        # incumbent's rank.
        if len(better) < n:
            chosen.append(sym)
    for c in challengers:
        if len(chosen) >= n:
            break
        if c.symbol not in chosen:
            chosen.append(c.symbol)
    return chosen[:n]


def plan_orders(session, config: fr.FTMOConfig, state: fr.AccountState,
                ranked: list[Candidate], held: list[str],
                risk_pct: float = 1.0, margin_pct: float = 1.0,
                top_n: int = TOP_N,
                horizon_bars: int = FORECAST_HORIZON_BARS,
                max_per_class: int | None = DEFAULT_MAX_PER_CLASS) -> dict:
    """Turn a ranking into concrete sized orders, or into a stated refusal.

    Asks the rule engine BEFORE sizing anything, so a blocked account produces
    the engine's own reason rather than a number nobody should act on — same
    ordering `ftmo_sizing.plan_entry` uses and the same reasoning as RiskGuard
    gating an order before it reaches the broker.
    """
    verdict = fr.evaluate(config, state)
    # Rank within class FIRST, then let the leaders compete. The gap, the
    # rotation margin and the target must all see the SAME pool — computing
    # the boundary gap on the full ranking while selecting from a capped one
    # would print a number describing a decision nobody made.
    pool = cap_per_class(ranked, max_per_class)
    gap = rank_boundary_gap(pool, top_n)
    out = {"can_open": verdict.can_open, "must_flatten": verdict.must_flatten,
           "reasons": list(verdict.reasons), "rank_gap": gap,
           "ranked": ranked, "pool": [c.symbol for c in pool],
           "max_per_class": max_per_class,
           "entries": [], "exits": [], "skipped": []}

    target = apply_rotation_margin(held, pool, margin_pct, top_n)
    out["target"] = target
    out["exits"] = [s for s in held if s not in target]

    if verdict.must_flatten:
        out["entries"] = []
        out["skipped"].append("rule engine says FLATTEN — no entries proposed")
        return out
    if not verdict.can_open:
        out["skipped"].append(
            f"rule engine refuses new entries: {'; '.join(verdict.reasons)}")
        return out

    by_symbol = {c.symbol: c for c in ranked}
    open_risk = 0.0
    # One multiple for the whole plan: the stop has to be sized against the
    # horizon the TARGET is drawn from, and every candidate in this plan came
    # out of the same forecast. Computing it per symbol would invite a future
    # edit to vary it per instrument, which is where curve-fitting starts.
    stop_mult = fz.stop_atr_mult_for_horizon(horizon_bars)
    out["stop_atr_mult"] = stop_mult
    out["horizon_bars"] = horizon_bars
    for sym in target:
        if sym in held:
            continue
        cand = by_symbol[sym]
        spec = fz.spec_from_capture(sym, session.specs)
        budget = fr.max_position_risk_usd(config, state, open_risk_usd=open_risk)
        quote = session.quote(sym)
        entry = (quote.ask if quote and quote.ask else cand.last_close)
        stop = fz.stop_price_from_atr(entry, cand.atr, "BUY", mult=stop_mult)
        # Validate the stop HERE, not only at the venue. On 2026-08-05 a
        # mis-scaled ATR produced a stop of -17.29 on an instrument trading at
        # 2.69, and the sizer happily costed it at $199.86 of "risk" and
        # proposed the order. size_position only asks whether the stop
        # DISTANCE fits the budget; it has no opinion on whether the resulting
        # price is a real price. An unsizeable stop is a data problem, so it
        # is reported as a skip rather than sizing something smaller.
        try:
            fs.validate_stop("BUY", entry, stop)
        except fs.SessionError as e:
            out["skipped"].append(
                f"{sym}: refusing — {e} (ATR {cand.atr:g} vs price {entry:g}; "
                f"suspect bad bar data, not a market move)")
            continue
        # Every entry carries a target as well as a stop (owner decision,
        # 2026-08-08), and the target is Kronos's own predicted return. An
        # entry WITHOUT a derivable target is dropped rather than sent naked:
        # the only way the derivation fails is a forecast pointing against the
        # trade, and buying something the model expects to fall is not a
        # position this path should open regardless of the target question.
        try:
            take_profit = fz.take_profit_from_prediction(
                entry, cand.predicted_return_pct, "BUY")
            fs.validate_take_profit("BUY", entry, take_profit)
        except (ValueError, fs.SessionError) as e:
            out["skipped"].append(
                f"{sym}: refusing — {e} (ranked into the top {top_n} on a "
                f"forecast of {cand.predicted_return_pct:+.2f}%)")
            continue
        result = fz.size_position(spec, state.equity, risk_pct, entry, stop,
                                  quote_to_account_rate=1.0,
                                  budget_remaining=budget)
        if not result.accepted:
            out["skipped"].append(f"{sym}: {result.summary()}")
            continue
        open_risk += result.risk_at_stop
        out["entries"].append({
            "symbol": sym, "asset_class": cand.asset_class, "side": "BUY",
            "volume": result.volume, "units": result.units,
            "entry_price": entry, "stop_price": stop,
            "take_profit_price": take_profit,
            "risk_at_stop": result.risk_at_stop,
            "reward_at_target": result.risk_at_stop * (
                (take_profit - entry) / (entry - stop)) if entry > stop else 0.0,
            "predicted_return_pct": cand.predicted_return_pct,
            "atr": cand.atr,
        })
    return out


def format_plan(plan: dict) -> str:
    lines = []
    gap = plan.get("rank_gap")
    lines.append(f"rule engine: can_open={plan['can_open']} "
                 f"must_flatten={plan['must_flatten']}")
    if plan["reasons"]:
        lines.append(f"  reasons: {'; '.join(plan['reasons'])}")
    lines.append("")
    lines.append(f"{'rank':<5}{'symbol':<13}{'class':<13}{'pred %':>9}{'ATR':>12}")
    pool = set(plan.get("pool") or [c.symbol for c in plan["ranked"]])
    for i, c in enumerate(plan["ranked"], 1):
        if c.symbol in plan.get("target", []):
            mark = " *"          # selected
        elif c.symbol in pool:
            mark = " +"          # class leader, eligible, not selected
        else:
            mark = ""            # capped out by a higher-ranked name in its class
        lines.append(f"{i:<5}{c.symbol:<13}{c.asset_class:<13}"
                     f"{c.predicted_return_pct:>9.2f}{c.atr:>12.5f}{mark}")
    lines.append("")
    cap = plan.get("max_per_class")
    if cap:
        lines.append(
            f"ranked WITHIN asset class: max {cap} per class, so the pool is "
            f"{len(pool)} class leader(s) out of {len(plan['ranked'])} "
            f"forecast.  * = selected, + = eligible, blank = a higher-ranked "
            f"name in the same class took the slot")
        lines.append(f"pool: {', '.join(plan.get('pool') or [])}")
        lines.append("")
    if gap is not None:
        warn = "  <-- NARROW, selection is near a coin flip" if gap < 1.0 else ""
        lines.append(f"rank {TOP_N}/{TOP_N + 1} gap: {gap:.2f} pt{warn}"
                     + ("  (measured across the capped pool)" if cap else ""))
    lines.append(f"target: {', '.join(plan.get('target', [])) or '(none)'}")
    if plan["exits"]:
        lines.append(f"exits:  {', '.join(plan['exits'])}")
    lines.append("")
    if plan["entries"]:
        mult = plan.get("stop_atr_mult")
        horizon = plan.get("horizon_bars")
        if mult is not None and horizon is not None:
            lines.append(f"stop geometry: {mult:.2f} x ATR, scaled to the "
                         f"{horizon}-bar forecast horizon")
        lines.append("proposed entries:")
        for e in plan["entries"]:
            rr = ((e["take_profit_price"] - e["entry_price"])
                  / (e["entry_price"] - e["stop_price"])
                  if e["entry_price"] > e["stop_price"] else float("nan"))
            lines.append(f"  BUY {e['symbol']:<12} vol={e['volume']:<10} "
                         f"entry={e['entry_price']:<12.5f} "
                         f"stop={e['stop_price']:<12.5f} "
                         f"tp={e['take_profit_price']:<12.5f} "
                         f"risk=${e['risk_at_stop']:,.2f} "
                         f"({rr:.1f}R)")
        lines.append(f"  total risk at stop: "
                     f"${sum(e['risk_at_stop'] for e in plan['entries']):,.2f}")
        lines.append(f"  total reward at target: "
                     f"${sum(e['reward_at_target'] for e in plan['entries']):,.2f}")
    else:
        lines.append("proposed entries: NONE")
    for s in plan["skipped"]:
        lines.append(f"  skipped: {s}")
    return "\n".join(lines)


# ------------------------------------------------------------------- live run

def dry_run(sample_count: int = 10) -> int:
    """Full live path with real bars and real quotes. Places NOTHING."""
    sys.path.insert(0, str(BASE_DIR / "KronosAI"))
    import kronos_agent as ka
    import pandas as pd

    specs = svc.load_symbol_specs()
    pairs = build_universe(specs, load_universe())
    symbols = [s for s, _ in pairs]
    classes = dict(pairs)
    print(f"universe: {len(symbols)} symbols, all USD-quoted\n")

    session = fs.FTMOSession(specs=specs)
    session.start()
    print(f"connected, account {session.account_id}")
    acct = session.account()
    session.subscribe(symbols)

    bars_by_symbol, frames = {}, {}
    for sym in symbols:
        bars = session.trendbars(sym, "D1", BARS_NEEDED)
        if len(bars) < ka.LOOKBACK:
            print(f"  {sym}: only {len(bars)} daily bars, need {ka.LOOKBACK}"
                  f" — skipped")
            continue
        bars_by_symbol[sym] = bars
        idx = pd.to_datetime([b["ts"] for b in bars], unit="s")
        frames[sym] = pd.DataFrame(
            {"open": [b["open"] for b in bars], "high": [b["high"] for b in bars],
             "low": [b["low"] for b in bars], "close": [b["close"] for b in bars],
             "volume": [b["volume"] for b in bars]}, index=idx)
    print(f"bars pulled for {len(frames)}/{len(symbols)} symbols\n")
    if not frames:
        print("no symbol had enough daily history — nothing to forecast")
        session.stop()
        return 1

    _, _, pred_dfs = ka.forecast_frames(frames, sample_count=sample_count)
    ranked = rank_candidates(pred_dfs, bars_by_symbol, classes)

    positions = session.refresh_positions()
    held = [p.symbol for p in positions]
    equity = acct["balance"]  # no floating P&L with a flat book
    state = fr.AccountState(equity=equity, balance=acct["balance"],
                            day_start_balance=acct["balance"],
                            open_position_count=len(positions))
    plan = plan_orders(session, fr.FTMOConfig(), state, ranked, held)
    print(format_plan(plan))
    print("\nDRY RUN — nothing was placed.")
    session.stop()
    return 0


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def raises(fn, needle=""):
        try:
            fn()
        except Exception as e:
            return needle.lower() in str(e).lower()
        return False

    specs = {
        "EURUSD": {"symbol_id": 1, "digits": 5, "min_volume": 100000,
                   "step_volume": 100000, "max_volume": 10 ** 10,
                   "quote_asset": "USD", "trading_mode": "ENABLED"},
        "XAUUSD": {"symbol_id": 2, "digits": 2, "min_volume": 100,
                   "step_volume": 100, "max_volume": 10 ** 9,
                   "quote_asset": "USD", "trading_mode": "ENABLED"},
        "GER40.cash": {"symbol_id": 3, "digits": 2, "min_volume": 1,
                       "step_volume": 1, "max_volume": 10 ** 6,
                       "quote_asset": "EUR", "trading_mode": "ENABLED"},
        "HALTED": {"symbol_id": 4, "digits": 2, "min_volume": 1,
                   "step_volume": 1, "max_volume": 10 ** 6,
                   "quote_asset": "USD", "trading_mode": "CLOSE_ONLY_MODE"},
    }

    print("universe construction:")
    ok = build_universe(specs, {"fx": ["EURUSD"], "commodities": ["XAUUSD"]})
    check("valid symbols pass through with their class",
          ok == [("EURUSD", "fx"), ("XAUUSD", "commodities")])
    check("a non-USD quote is REFUSED, not silently dropped",
          raises(lambda: build_universe(specs, {"idx": ["GER40.cash"]}),
                 "not USD"))
    check("...and the refusal explains why 1.0 is not assumed",
          raises(lambda: build_universe(specs, {"idx": ["GER40.cash"]}),
                 "refuses to assume"))
    check("an unknown symbol is refused",
          raises(lambda: build_universe(specs, {"fx": ["NOPE"]}),
                 "not in the symbol capture"))
    check("a non-tradeable symbol is refused",
          raises(lambda: build_universe(specs, {"x": ["HALTED"]}),
                 "CLOSE_ONLY"))
    check("an empty universe is refused",
          raises(lambda: build_universe(specs, {}), "empty"))
    check("the shipped default basket is entirely USD-quoted",
          all(cls and syms for cls, syms in DEFAULT_UNIVERSE.items()))
    check("the default basket covers all four classes",
          set(DEFAULT_UNIVERSE) == {"indices", "fx", "commodities", "crypto"})

    print("ATR uses true range, not high-low:")
    flat = [{"high": 10, "low": 9, "close": 9.5} for _ in range(20)]
    check("steady bars give the high-low range", abs(atr_from_bars(flat) - 1.0) < 1e-9)
    gapped = [{"high": 10, "low": 9, "close": 9.5} for _ in range(19)]
    gapped.append({"high": 20, "low": 19, "close": 19.5})
    check("a gap against the prior close widens ATR (a high-low ATR would miss it)",
          atr_from_bars(gapped) > 1.0)
    check("too few bars raises rather than guessing",
          raises(lambda: atr_from_bars(flat[:3]), "need"))

    print("rank boundary gap:")
    def cand(sym, pct):
        return Candidate(sym, "fx", pct, 100.0, 1.0)
    wide = [cand("A", 10), cand("B", 8), cand("C", 6), cand("D", 4), cand("E", 0.5)]
    check("gap is rank N minus rank N+1", abs(rank_boundary_gap(wide, 4) - 3.5) < 1e-9)
    check("no N+1 -> None, not zero", rank_boundary_gap(wide[:4], 4) is None)

    print("rotation margin (hysteresis, not edge):")
    ranked = [cand("NEW", 5.4), cand("HELD", 5.0), cand("X", 1.0), cand("Y", 0.5),
              cand("Z", 0.1)]
    keep = apply_rotation_margin(["HELD"], ranked, margin_pct=1.0, n=2)
    check("an incumbent beaten by less than the margin keeps its slot",
          "HELD" in keep)
    ranked2 = [cand("NEW", 7.0), cand("HELD", 5.0), cand("X", 1.0)]
    drop = apply_rotation_margin(["HELD"], ranked2, margin_pct=1.0, n=1)
    check("an incumbent beaten by MORE than the margin is dropped",
          drop == ["NEW"])
    check("zero margin restores strict ranking",
          apply_rotation_margin(["HELD"], ranked, 0.0, 1) == ["NEW"])
    check("a held symbol that vanished from the ranking is not resurrected",
          "GONE" not in apply_rotation_margin(["GONE"], ranked, 1.0, 2))

    print("plan_orders asks the rule engine first:")
    cfg = fr.FTMOConfig()

    class _S:
        specs = {"EURUSD": {"symbol_id": 1, "digits": 5, "min_volume": 100000,
                            "step_volume": 100000, "max_volume": 10 ** 10,
                            "quote_asset": "USD", "trading_mode": "ENABLED"}}
        def quote(self, sym):
            return fs.Quote(symbol_id=1, bid=1.0849, ask=1.0850, ts=0)

    ranked3 = [Candidate("EURUSD", "fx", 3.0, 1.0850, 0.0050)]
    breached = fr.AccountState(equity=23_000, balance=23_000,
                               day_start_balance=25_000)
    p = plan_orders(_S(), cfg, breached, ranked3, [])
    check("a blocked account proposes no entries", p["entries"] == [])
    check("...and says why, in the engine's own words",
          any("rule engine" in s for s in p["skipped"]))

    healthy = fr.AccountState(equity=25_000, balance=25_000,
                              day_start_balance=25_000)
    p2 = plan_orders(_S(), cfg, healthy, ranked3, [])
    check("a healthy account proposes a sized entry", len(p2["entries"]) == 1)
    if p2["entries"]:
        e = p2["entries"][0]
        check("the entry carries a stop below entry (long)",
              e["stop_price"] < e["entry_price"])
        check("risk at stop is inside the 1% per-trade cap",
              e["risk_at_stop"] <= 250.0 + 1e-6)
        check("volume is on the venue step grid",
              e["volume"] % 100000 == 0)
        check("the stop would pass ftmo_session's own validation",
              fs.validate_stop("BUY", e["entry_price"], e["stop_price"]) is None)
        check("the entry carries a take-profit above entry (long)",
              e["take_profit_price"] > e["entry_price"])
        check("the target is the forecast, not an R multiple",
              abs(e["take_profit_price"]
                  - e["entry_price"] * 1.03) < 1e-9)
        check("the target would pass ftmo_session's own validation",
              fs.validate_take_profit("BUY", e["entry_price"],
                                      e["take_profit_price"]) is None)
        check("stop and target straddle the entry",
              e["stop_price"] < e["entry_price"] < e["take_profit_price"])
        check("reward at target is reported alongside risk",
              e["reward_at_target"] > 0)

    print("every entry carries a target — one without is not an entry:")
    check("no planned entry may lack a take-profit",
          all("take_profit_price" in e and e["take_profit_price"] > 0
              for e in p2["entries"]))

    print("a forecast pointing the WRONG way is dropped (2026-08-07 EURUSD):")
    # EURUSD ranked into the top 4 on a predicted -0.15% and was bought. There
    # is no take-profit on the profitable side of that trade, and rather than
    # send it without one the entry is dropped. This is a BEHAVIOUR CHANGE:
    # before 2026-08-08 this candidate produced an order.
    negative = [Candidate("EURUSD", "fx", -0.15, 1.0850, 0.0050)]
    p4 = plan_orders(_S(), cfg, healthy, negative, [])
    check("a negatively-forecast candidate proposes no entry",
          p4["entries"] == [])
    check("...and the skip names the forecast that caused it",
          any("-0.15%" in s for s in p4["skipped"]))
    zero = [Candidate("EURUSD", "fx", 0.0, 1.0850, 0.0050)]
    check("a zero forecast is dropped too, not treated as flat-but-fine",
          plan_orders(_S(), cfg, healthy, zero, [])["entries"] == [])

    print("rotation margin: hysteresis KEEPS incumbents (2026-08-07 bug):")
    # The exact live shape: ETHUSD held at rank 4, EURUSD challenging at rank 5.
    # The old code sold the +9.51% incumbent to buy the -0.15% challenger.
    live_ranked = [cand("SOLUSD", 27.7), cand("NATGAS.cash", 26.0),
                   cand("LTCUSD", 9.6), cand("ETHUSD", 9.51),
                   cand("EURUSD", -0.15)]
    tgt = apply_rotation_margin(["ETHUSD"], live_ranked, 1.0, 4)
    check("the held incumbent KEEPS its slot (it did not, before)",
          "ETHUSD" in tgt)
    check("...and the worse-ranked challenger does not take it",
          "EURUSD" not in tgt)
    check("the target is still exactly top_n",  len(tgt) == 4)
    # Compared as a SET: the function returns keepers first, so the order
    # differs from the raw ranking while the selection is identical. Every
    # consumer tests `target` for membership (`out["exits"]`, the entry loop),
    # so order carries no meaning — asserting on it tests the implementation
    # rather than the behaviour.
    check("margin=0 really does restore strict ranking now",
          set(apply_rotation_margin(["ETHUSD"], live_ranked, 0.0, 4))
          == {c.symbol for c in live_ranked[:4]})
    # The mechanism must still DROP a genuinely beaten incumbent.
    beaten = [cand("A", 30.0), cand("B", 29.0), cand("C", 28.0),
              cand("D", 27.0), cand("HELD", 1.0)]
    check("an incumbent beaten by n challengers IS dropped — this is "
          "hysteresis, not a ratchet",
          "HELD" not in apply_rotation_margin(["HELD"], beaten, 1.0, 4))
    check("an incumbent beaten by fewer than n challengers is kept",
          "HELD" in apply_rotation_margin(
              ["HELD"], [cand("A", 30.0), cand("HELD", 1.0),
                         cand("B", 0.5), cand("C", 0.4)], 1.0, 4))

    print("momentum stays gated on this venue too (rule 8):")
    bars = {"A": [{"close": 100.0}] * 200 + [{"close": 100.0 + i}
                                             for i in range(60)],
            "B": [{"close": 100.0}] * 200 + [{"close": 100.0 - i * 0.1}
                                             for i in range(60)]}
    check("momentum_rank refuses without the explicit opt-in",
          raises(lambda: momentum_rank(bars), "DISABLED"))
    check("...and computes with it",
          len(momentum_rank(bars, lookback=252, allow_momentum=True)) == 2)
    ranked_m = momentum_rank(bars, lookback=252, allow_momentum=True)
    check("the riser outranks the faller", ranked_m[0][0] == "A")
    check("a symbol with too little history is omitted, not short-windowed",
          momentum_rank({"S": [{"close": 1.0}] * 100}, lookback=252,
                        allow_momentum=True) == [])

    print("the AND gate: momentum picks, Kronos vetoes on sign:")
    mom = [("A", 0.40), ("B", 0.30), ("C", 0.20), ("D", 0.10), ("E", 0.05)]
    sel, vetoed = apply_kronos_veto(
        mom, {"A": 2.1, "B": -0.4, "C": 5.8, "D": -1.2, "E": 3.0}, top_n=4)
    check("picks Kronos agrees with are kept", "A" in sel and "C" in sel)
    check("picks Kronos forecasts negative are dropped",
          "B" not in sel and "D" not in sel)
    check("Kronos never PROMOTES a symbol momentum did not pick",
          set(sel) <= {s for s, _ in mom})
    check("...and never reorders momentum's ranking",
          sel == [s for s, _ in mom if s in sel])
    check("every veto is reported with its reason, not silently dropped",
          len(vetoed) == 2 and all(v["reason"] for v in vetoed))
    check("a missing Kronos forecast is a VETO, not a pass",
          apply_kronos_veto([("Z", 0.5)], {}, top_n=4)[0] == [])
    check("a zero forecast is a veto too — it does not agree with a long",
          apply_kronos_veto([("Z", 0.5)], {"Z": 0.0}, top_n=4)[0] == [])
    check("the gate never returns more than top_n",
          len(apply_kronos_veto(mom, {s: 9.0 for s, _ in mom}, top_n=2)[0]) == 2)
    check("a total disagreement yields NO trade, not a fallback to momentum",
          apply_kronos_veto(mom, {s: -1.0 for s, _ in mom}, top_n=4)[0] == [])

    print("the universe is derived from the capture, not hardcoded:")
    real = json.loads((BASE_DIR / "ftmo_symbol_specs.json").read_text())["symbols"]
    derived = universe_from_capture(real)
    flat_derived = build_universe(real, derived)
    check("every derived symbol survives build_universe's own validation",
          len(flat_derived) == sum(len(v) for v in derived.values()))
    check("no non-USD symbol is included (build_universe would refuse it)",
          all(real[s]["quote_asset"] == "USD"
              for syms in derived.values() for s in syms))
    check("a DISABLED symbol is excluded (FETUSD, the live example)",
          "FETUSD" not in {s for v in derived.values() for s in v})
    # Category 13 mixes indices with energy. Getting this wrong files a trade
    # against the wrong rule 9 screen, which is why it is asserted both ways.
    check("US500.cash is classified as an INDEX, not a commodity",
          "US500.cash" in derived["indices"])
    check("USOIL.cash shares category 13 but is a COMMODITY",
          "USOIL.cash" in derived["commodities"])
    check("UKOIL.cash is not swept into indices by its .cash suffix",
          "UKOIL.cash" in derived["commodities"])
    check("stock CFDs are their own class, inheriting the stock evidence",
          "AAPL" in derived["stocks"])
    check("an unknown category is dropped, never bucketed into a default",
          universe_from_capture(
              {"WAT": {"quote_asset": "USD", "trading_mode": "ENABLED",
                       "category_id": 999}}) == {})
    check("the result is the same shape ftmo.universe takes in settings",
          all(isinstance(v, list) for v in derived.values()))

    print("ranking WITHIN asset class caps concentration (2026-08-11):")
    # The exact shape from the first live dry-run on the 101-symbol universe:
    # four micro-cap cryptos sweeping the board ahead of every other class.
    swept = [
        Candidate("GALUSD", "crypto", 47.67, 0.00021, 0.00008),
        Candidate("VECUSD", "crypto", 25.51, 0.00042, 0.00017),
        Candidate("IMXUSD", "crypto", 18.11, 0.0121, 0.00481),
        Candidate("MANUSD", "crypto", 16.65, 0.0054, 0.00214),
        Candidate("GME", "stocks", 3.10, 24.0, 1.20),
        Candidate("SUGAR.c", "commodities", 1.90, 18.0, 0.40),
        Candidate("US100.cash", "indices", 0.80, 22000.0, 667.0),
        Candidate("EURUSD", "fx", 0.20, 1.15, 0.0056),
    ]
    uncapped = cap_per_class(swept, 0)
    check("a cap of 0 disables the mechanism entirely",
          [c.symbol for c in uncapped] == [c.symbol for c in swept])
    check("...and None does the same",
          [c.symbol for c in cap_per_class(swept, None)]
          == [c.symbol for c in swept])

    capped = cap_per_class(swept, 1)
    check("a cap of 1 leaves exactly one candidate per class",
          len({c.asset_class for c in capped}) == len(capped))
    check("...and it is each class's BEST, not an arbitrary member",
          [c.symbol for c in capped]
          == ["GALUSD", "GME", "SUGAR.c", "US100.cash", "EURUSD"])
    check("...preserving the global order among the leaders",
          [c.predicted_return_pct for c in capped]
          == sorted((c.predicted_return_pct for c in capped), reverse=True))
    check("the four-crypto sweep is reduced to one crypto",
          sum(1 for c in capped if c.asset_class == "crypto") == 1)

    check("a cap of 2 allows two per class and no more",
          all(sum(1 for c in cap_per_class(swept, 2)
                  if c.asset_class == cls) <= 2
              for cls in {c.asset_class for c in swept}))
    check("...and takes the top two of the class, in order",
          [c.symbol for c in cap_per_class(swept, 2)][:2]
          == ["GALUSD", "VECUSD"])
    check("a cap larger than any class changes nothing",
          [c.symbol for c in cap_per_class(swept, 99)]
          == [c.symbol for c in swept])
    check("an empty ranking survives the cap",
          cap_per_class([], 1) == [])

    # The whole point, asserted end to end rather than on the helper alone:
    # the same candidates that produced an all-crypto top-4 must now produce a
    # target spanning four different classes.
    #
    # Run against a BREACHED state deliberately. `plan_orders` computes the
    # pool, the gap and the target BEFORE it consults can_open, so a breached
    # account exercises the whole selection path and stops short of sizing —
    # which is what is under test here and avoids needing venue specs for
    # eight invented symbols.
    p_swept = plan_orders(_S(), cfg, breached, swept, [], top_n=4,
                          max_per_class=0)
    check("WITHOUT the cap the target is all crypto (the 2026-08-11 result)",
          {c.asset_class for c in swept
           if c.symbol in p_swept["target"]} == {"crypto"})
    p_capped = plan_orders(_S(), cfg, breached, swept, [], top_n=4,
                           max_per_class=1)
    check("WITH the cap the target spans four distinct classes",
          len({c.asset_class for c in swept
               if c.symbol in p_capped["target"]}) == 4)
    check("...and still leads with the strongest name overall",
          p_capped["target"][0] == "GALUSD")
    check("the plan reports the pool it actually selected from",
          p_capped["pool"] == ["GALUSD", "GME", "SUGAR.c", "US100.cash",
                               "EURUSD"])
    check("the full ranking is still reported for display",
          len(p_capped["ranked"]) == len(swept))
    # A gap measured on the uncapped list would describe a decision nobody
    # made: rank 4/5 there is MANUSD vs GME, and neither is a boundary case
    # once the cap is on.
    # Pool is [GALUSD 47.67, GME 3.10, SUGAR.c 1.90, US100.cash 0.80,
    # EURUSD 0.20], so rank 4/5 is US100.cash - EURUSD = 0.60. On the UNCAPPED
    # list rank 4/5 is MANUSD 16.65 - GME 3.10 = 13.55 — a number describing a
    # decision nobody made.
    check("the boundary gap is measured across the CAPPED pool",
          abs(p_capped["rank_gap"] - 0.60) < 1e-9)
    check("...and the uncapped gap really is the different number",
          abs(p_swept["rank_gap"] - 13.55) < 1e-9)

    print("resolve_universe picks a source and says which (2026-08-11):")
    import tempfile

    def _settings(blob: dict) -> Path:
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(blob, fh)
        fh.close()
        return Path(fh.name)

    u_default, why_default = resolve_universe(
        real, _settings({"ftmo": {"universe_source": "default"}}))
    check("universe_source=default returns the 14-symbol basket",
          u_default == DEFAULT_UNIVERSE)
    check("...and says so in the provenance", "DEFAULT_UNIVERSE" in why_default)

    u_cap, why_cap = resolve_universe(
        real, _settings({"ftmo": {"universe_source": "capture"}}))
    check("universe_source=capture returns the venue-derived universe",
          u_cap == derived)
    check("...and the provenance names the capture",
          "symbol capture" in why_cap)

    u_none, why_none = resolve_universe(real, _settings({"ftmo": {}}))
    check("NO universe_source defaults to the capture, not the basket",
          u_none == derived and sum(len(v) for v in u_none.values()) > 14)
    check("a missing settings file also defaults to the capture",
          resolve_universe(real, Path("/nonexistent/settings.json"))[0] == derived)

    # An explicit hand-written list is the most specific statement of intent
    # available and must beat the derived one, or the config stops being the
    # reviewable record of what may be traded.
    u_exp, why_exp = resolve_universe(
        real, _settings({"ftmo": {"universe": {"fx": ["EURUSD"]},
                                  "universe_source": "capture"}}))
    check("an explicit ftmo.universe OVERRIDES universe_source",
          u_exp == {"fx": ["EURUSD"]})
    check("...and the provenance says it came from settings",
          "explicit" in why_exp)

    # Emptying the config is how someone turns the bot off. It must reach
    # build_universe and raise, never resurrect a basket.
    u_empty, _ = resolve_universe(
        real, _settings({"ftmo": {"universe": {}}}))
    check("an explicitly EMPTY universe is preserved, not replaced",
          u_empty == {})
    check("...and still raises at build_universe",
          raises(lambda: build_universe(real, u_empty), "empty"))

    check("an unrecognised universe_source RAISES rather than falling back",
          raises(lambda: resolve_universe(
              real, _settings({"ftmo": {"universe_source": "captrue"}})),
              "not one of"))

    # Guards the whole point of the 2026-08-11 change. If a future edit routes
    # the runner back to the 14-symbol basket by accident, this fails loudly
    # rather than the bot quietly forecasting a seventh of the account.
    check("the capture universe is many times the size of the old basket",
          sum(len(v) for v in derived.values())
          >= 4 * sum(len(v) for v in DEFAULT_UNIVERSE.values()))

    print("a nonsensical stop is refused, not sized (2026-08-05 regression):")
    # ATR wildly larger than price is what a mis-scaled bar series looks like.
    # It must be reported as bad data, never costed as if it were risk.
    insane = [Candidate("EURUSD", "fx", 36.0, 2.694, 9.99)]
    p3 = plan_orders(_S(), cfg, healthy, insane, [])
    check("no entry is proposed from a negative stop", p3["entries"] == [])
    check("...and the skip says it is suspected bad data, not a market move",
          any("bad bar data" in s for s in p3["skipped"]))

    print("the stop is scaled to the forecast horizon (2026-08-09):")
    # Read the source rather than importing it: `import kronos_agent` pulls
    # torch, and ftmo_runner's selftests assert the model is not loaded before
    # the arm check. A duplicated constant that nothing verifies is exactly the
    # kind of drift that makes a stop silently wrong, so it is verified here.
    ka_src = (BASE_DIR / "KronosAI" / "kronos_agent.py").read_text()
    m = re.search(r"^PRED_LEN\s*=\s*(\d+)", ka_src, re.M)
    check("kronos_agent.PRED_LEN is still parseable from source", m is not None)
    check(f"FORECAST_HORIZON_BARS ({FORECAST_HORIZON_BARS}) still matches "
          f"kronos_agent.PRED_LEN ({m.group(1) if m else '?'})",
          m is not None and int(m.group(1)) == FORECAST_HORIZON_BARS)
    check("...and no torch was imported to check it", "torch" not in sys.modules)

    p4 = plan_orders(_S(), cfg, healthy, ranked3, [], horizon_bars=5)
    p20 = plan_orders(_S(), cfg, healthy, ranked3, [], horizon_bars=20)
    check("the plan reports the multiple it used",
          abs(p4["stop_atr_mult"] - 1.0) < 1e-9
          and abs(p20["stop_atr_mult"] - 2.0) < 1e-9)
    check("the 20-bar plan reproduces the pre-2026-08-09 stop exactly",
          abs(p20["entries"][0]["stop_price"]
              - (1.0850 - 2.0 * 0.0050)) < 1e-9)
    check("a shorter horizon stops nearer entry on identical candidates",
          p4["entries"][0]["stop_price"] > p20["entries"][0]["stop_price"])
    check("...and the target is untouched by the horizon — only the stop moved",
          abs(p4["entries"][0]["take_profit_price"]
              - p20["entries"][0]["take_profit_price"]) < 1e-9)
    check("...so the reward-to-risk ratio improves",
          (p4["entries"][0]["reward_at_target"] / p4["entries"][0]["risk_at_stop"])
          > (p20["entries"][0]["reward_at_target"] / p20["entries"][0]["risk_at_stop"]))
    check("a nearer stop still never risks more than the per-trade cap — "
          "tightening buys SIZE, not extra risk",
          p4["entries"][0]["risk_at_stop"] <= 250.0 + 1e-6)
    check("...and it does buy size: half the distance, twice the volume",
          p4["entries"][0]["volume"] > p20["entries"][0]["volume"])

    print("\nFAILED" if failures else "\nAll ftmo_signal offline selftests passed.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Kronos ranking -> sized FTMO orders.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--selftest", action="store_true",
                   help="Offline checks; no network, no credentials.")
    g.add_argument("--dry-run", action="store_true",
                   help="Live bars and quotes, full plan, places NOTHING.")
    ap.add_argument("--sample-count", type=int, default=10)
    args = ap.parse_args()
    if args.dry_run:
        return dry_run(sample_count=args.sample_count)
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
