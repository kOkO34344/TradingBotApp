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
`autotrade_runner.py` (rule 7) and the unattended FTMO path (rule 9). Flag it
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
import sys
from dataclasses import dataclass
from pathlib import Path

import ftmo_rules as fr
import ftmo_service as svc
import ftmo_sizing as fz
import ftmo_session as fs

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

TOP_N = 4               # matches ftmo_sizing's max positions and the 1% budget
ATR_PERIOD = 14
BARS_NEEDED = 420       # Kronos LOOKBACK is 400; a little slack for gaps


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

    Same hysteresis `paper_trader.apply_rotation_margin` gives the IBKR path,
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
        if not better:
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
                top_n: int = TOP_N) -> dict:
    """Turn a ranking into concrete sized orders, or into a stated refusal.

    Asks the rule engine BEFORE sizing anything, so a blocked account produces
    the engine's own reason rather than a number nobody should act on — same
    ordering `ftmo_sizing.plan_entry` uses and the same reasoning as RiskGuard
    gating an order before it reaches the broker.
    """
    verdict = fr.evaluate(config, state)
    gap = rank_boundary_gap(ranked, top_n)
    out = {"can_open": verdict.can_open, "must_flatten": verdict.must_flatten,
           "reasons": list(verdict.reasons), "rank_gap": gap,
           "ranked": ranked, "entries": [], "exits": [], "skipped": []}

    target = apply_rotation_margin(held, ranked, margin_pct, top_n)
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
    for sym in target:
        if sym in held:
            continue
        cand = by_symbol[sym]
        spec = fz.spec_from_capture(sym, session.specs)
        budget = fr.max_position_risk_usd(config, state, open_risk_usd=open_risk)
        quote = session.quote(sym)
        entry = (quote.ask if quote and quote.ask else cand.last_close)
        stop = fz.stop_price_from_atr(entry, cand.atr, "BUY")
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
            "risk_at_stop": result.risk_at_stop,
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
    for i, c in enumerate(plan["ranked"], 1):
        mark = " *" if c.symbol in plan.get("target", []) else ""
        lines.append(f"{i:<5}{c.symbol:<13}{c.asset_class:<13}"
                     f"{c.predicted_return_pct:>9.2f}{c.atr:>12.5f}{mark}")
    lines.append("")
    if gap is not None:
        warn = "  <-- NARROW, selection is near a coin flip" if gap < 1.0 else ""
        lines.append(f"rank {TOP_N}/{TOP_N + 1} gap: {gap:.2f} pt{warn}")
    lines.append(f"target: {', '.join(plan.get('target', [])) or '(none)'}")
    if plan["exits"]:
        lines.append(f"exits:  {', '.join(plan['exits'])}")
    lines.append("")
    if plan["entries"]:
        lines.append("proposed entries:")
        for e in plan["entries"]:
            lines.append(f"  BUY {e['symbol']:<12} vol={e['volume']:<10} "
                         f"entry={e['entry_price']:<12.5f} "
                         f"stop={e['stop_price']:<12.5f} "
                         f"risk=${e['risk_at_stop']:,.2f}")
        lines.append(f"  total risk at stop: "
                     f"${sum(e['risk_at_stop'] for e in plan['entries']):,.2f}")
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

    print("a nonsensical stop is refused, not sized (2026-08-05 regression):")
    # ATR wildly larger than price is what a mis-scaled bar series looks like.
    # It must be reported as bad data, never costed as if it were risk.
    insane = [Candidate("EURUSD", "fx", 36.0, 2.694, 9.99)]
    p3 = plan_orders(_S(), cfg, healthy, insane, [])
    check("no entry is proposed from a negative stop", p3["entries"] == [])
    check("...and the skip says it is suspected bad data, not a market move",
          any("bad bar data" in s for s in p3["skipped"]))

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
