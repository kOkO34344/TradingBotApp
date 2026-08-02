#!/usr/bin/env python3
"""
paper_trader.py — Phase 3: rule-based paper trading with mandatory human
approval. No LLM in this loop — the signal is pure rules, computed at
machine speed; a human approves every rebalance before anything reaches
the broker.

Flow: connect (paper-guarded) -> compute a signal from FRESH daily data ->
diff against live IBKR positions -> print the full proposed rebalance ->
explicit y/n -> execute (exits first, then entries, with ATR-based stops
sized from RiskGuard's risk budget) -> everything journals to
trade_journal.csv via ibkr_service.

Signal: KRONOS is the default and the project's main signal
(KronosAI/kronos_agent.py's forecast ranking) — owner decision, 2026-07-28.
Momentum rotation is DISABLED and will not compute without an explicit
`--signal momentum --allow-momentum`; see signal_policy.py for the gate and
for the honest note on what the evidence actually says (momentum is the only
family that earned Phase 3; Kronos's measured IC is ~0). Both signals return
the same (top, data, ranked) shape, so everything downstream of signal
selection — sizing, approval, execution, journaling — is identical regardless
of which produced `top`.

Sizing: qty = floor((NetLiquidation * risk_pct_per_trade%) / (2*ATR)),
clamped so qty*price never exceeds RiskGuard's max_order_notional_usd. This
ties position size to stop distance (2x daily ATR-14), not the other way
around, per the knowledge base's risk rules.

Usage:
  python3 paper_trader.py                    full run: Kronos signal, propose, ask, execute
  python3 paper_trader.py --dry-run          connect + compute + print only, no
                                              orders, no approval prompt
  python3 paper_trader.py --signal momentum --allow-momentum
                                             momentum, owner opt-in only (refused
                                             without the second flag)
"""

import argparse
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

import ibkr_service as ibs
import indicators as ind
import signal_policy as sp
import trader_app as ta

STOP_ATR_MULT = 2.0
ENTRY_LIMIT_BUFFER = 0.005  # 0.5% marketable buffer so the bracket entry actually fills

# Own cache dir, deliberately separate from trader_app's price_data/. This
# fetch is a short-window force=True refetch for live ranking; sharing
# price_data/ silently truncated the backtest scripts' long-history cache
# for the whole watchlist on 2026-07-21 (caught 2026-07-23 debugging the
# broad-universe momentum retest — 10 mega-caps found truncated to ~2yr).
LIVE_DATA_DIR = Path(__file__).parent / "price_data_live"
LIVE_DATA_DIR.mkdir(exist_ok=True)


def compute_signal(settings: dict, allow_momentum: bool = False):
    """Fresh momentum ranking: top-N of watchlist by trailing N-month return.
    Always re-fetches through today (force=True) — ranking off a stale
    price_data/ cache would drive real paper orders off old prices.

    DISABLED unless allow_momentum=True. Momentum does not run again until the
    owner asks for it in that session (2026-07-28); the gate lives here rather
    than at the call sites so no caller can reach the computation by forgetting
    to check. See signal_policy.py.
    """
    sp.assert_allowed("momentum", allow_momentum, context="paper_trader.compute_signal")
    top_n = settings.get("momentum_top_n", 3)
    lookback = settings.get("momentum_lookback_m", 12)
    dual = settings.get("risk_engine", False)
    tickers = settings["tickers"]

    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=lookback * 31 + 400)).strftime("%Y-%m-%d")

    data = {}
    for t in tickers:
        try:
            data[t] = ta.fetch(t, start, today, force=True, cache_dir=LIVE_DATA_DIR)
        except Exception as e:
            print(f"WARNING: could not fetch {t}: {e}", file=sys.stderr)

    if len(data) < top_n + 1:
        raise RuntimeError(f"Only {len(data)}/{len(tickers)} tickers fetched — need at least {top_n + 1}.")

    closes = pd.DataFrame({t: df["Close"] for t, df in data.items()}).dropna(how="all")
    monthly = closes.resample("ME").last()
    mom = monthly.pct_change(lookback)
    ranked = mom.iloc[-1].dropna().sort_values(ascending=False)
    if len(ranked) == 0:
        raise RuntimeError("Not enough history to compute trailing momentum yet.")

    top = list(ranked.index[:top_n])
    if dual:
        top = [t for t in top if ranked[t] > 0]
    return top, data, ranked


def get_current_holdings(ib, tickers) -> dict:
    """{symbol: Position} for watchlist tickers currently held (paper)."""
    held = {}
    for p in ib.positions():
        sym = p.contract.symbol
        if sym in tickers and p.position != 0:
            held[sym] = p
    return held


DEFAULT_ROTATION_MARGIN_PCT = 1.0


def rank_boundary_gap(ranked, top_n: int) -> float | None:
    """Percentage points between rank N and rank N+1. None if there is no N+1.

    This is the number that decides whether a Kronos top-N is a decision or a
    coin flip, so it gets printed with every proposal — CLAUDE.md's guidance
    was "check the gap before approving", which only works if the gap is on
    screen.
    """
    if len(ranked) <= top_n or top_n <= 0:
        return None
    return float(ranked.iloc[top_n - 1] - ranked.iloc[top_n]) * 100.0


def apply_rotation_margin(ranked, top_n: int, held_symbols, margin_pct: float) -> list:
    """Top-N with hysteresis: an incumbent keeps its slot unless a challenger
    beats it by more than `margin_pct` percentage points. Pure function.

    Kronos is a SAMPLING forecaster, so its per-ticker output moves between
    runs on identical data. Measured 2026-07-28: GOOGL came out +2.69% /
    -3.72% / +4.38% across three consecutive runs, and two `--dry-run`s thirty
    minutes apart produced different top-3s — [AMZN, MSFT, GOOGL] then
    [AMZN, MSFT, DIS], because GOOGL and DIS sat ~1 point apart and simply
    swapped ranks 3/4. That is not cosmetic: run 1 proposed BUY MSFT + BUY
    GOOGL (~$50k) and SELL DIS; run 2 proposed BUY MSFT only and HOLD DIS.
    Which orders got placed depended on which sampling draw you happened to
    run.

    A raw top-N sort has no way to express "these two are tied", so it
    manufactures a decision out of noise and pays real spread and commission
    to act on it. The margin makes ties resolve to DO NOTHING, which is both
    the cheaper error and the honest one — we have no evidence Kronos can
    distinguish two names one point apart (IC 0.036, hit rate 50.0%).

    The default margin is calibrated to the observed spread above, not to
    theory: ~1 point is the scale on which this ranking is known to be noise.
    Set `rotation_margin_pct` in trader_settings.json to change it; 0 restores
    the old strict-rank behaviour.

    Note this only ever SUPPRESSES churn — it can keep an incumbent that a
    strict sort would drop, but it can never introduce a name the strict sort
    didn't already rank above an incumbent. It also cannot keep more than
    `top_n` names: incumbents defend slots, they don't add them.
    """
    strict = list(ranked.index[:top_n])
    if margin_pct <= 0 or len(ranked) <= top_n:
        return strict

    held = {s for s in held_symbols if s in ranked.index}
    if not held:
        return strict

    margin = margin_pct / 100.0
    keep, challengers = [], []
    for sym in strict:
        (keep if sym in held else challengers).append(sym)

    # Incumbents ranked below the cut defend their slot against the weakest
    # challenger that displaced them, cheapest-to-defend first.
    defenders = [s for s in ranked.index[top_n:] if s in held]
    for defender in defenders:
        if not challengers:
            break
        weakest = challengers[-1]
        if (ranked[weakest] - ranked[defender]) <= margin:
            challengers.pop()
            keep.append(defender)

    # Preserve the ranking's own order in the returned list.
    out = [s for s in ranked.index if s in set(keep) | set(challengers)]
    return out[:top_n]


def cancel_open_orders_for(ib, symbol: str, timeout_s: float = 10.0) -> list:
    """Cancel any working orders (e.g. the stop leg of a bracket) tied to
    this symbol and wait for confirmation before the caller flattens it —
    otherwise a stale stop can fire against a position that's already gone."""
    trades = [t for t in ib.openTrades() if t.contract.symbol == symbol]
    for t in trades:
        ib.cancelOrder(t.order)
    waited = 0.0
    while waited < timeout_s and any(
        t.contract.symbol == symbol and not t.isDone() for t in ib.openTrades()
    ):
        ib.sleep(0.5)
        waited += 0.5
    return trades


def size_position(net_liq: float, price: float, atr_val: float, settings: dict, guard) -> int:
    """qty derived from the stop distance and the risk budget per trade,
    then clamped to RiskGuard's max order notional — size follows the stop,
    never the other way around."""
    if price <= 0 or atr_val <= 0 or pd.isna(atr_val):
        return 0
    risk_pct = settings.get("risk_pct_per_trade", 2.0) / 100
    stop_dist = STOP_ATR_MULT * atr_val
    risk_budget = net_liq * risk_pct
    qty = int(risk_budget // stop_dist)
    max_notional = guard.limits["max_order_notional_usd"]
    # clamp against the buffered entry price (what RiskGuard actually checks
    # notional against for the bracket order), not the raw market price —
    # otherwise a qty that looks fine at market price can still get blocked.
    entry_price = price * (1 + ENTRY_LIMIT_BUFFER)
    if qty * entry_price > max_notional:
        qty = int(max_notional // entry_price)
    return max(qty, 0)


def get_net_liquidation_usd(ib) -> float:
    """NetLiquidation converted to USD — the watchlist is US equities, so all
    sizing math (price, ATR, stops) is USD whatever currency the account holds.

    Converts using IBKR's own `ExchangeRate` account value rather than a live
    FX quote. This used to call `market_price(forex_pair("EURUSD"))`, which
    needs a market-data line and therefore fails whenever one isn't available
    — observed 2026-07-25 as error 10197 "No market data during competing live
    session", which took down `--dry-run` and would have taken down every
    hourly autotrade firing. `ExchangeRate` arrives on the account channel, so
    it needs no subscription and no data line.

    `ExchangeRate` for currency C is the value of 1 C in the account's BASE
    currency, so USD = BASE / rate_usd. That direction is NOT assumed — it is
    checked at runtime against an independent FX quote from yfinance, because
    getting it backwards misstates equity by ~29% on this account (1.137 vs
    0.879) and would silently mis-size every order. A failed check raises;
    sizing must never run on an unverified number.

    The independent source is deliberately yfinance rather than IBKR's own
    cash-balance arithmetic. That identity (sum(cash_C * rate_C) == cash_BASE)
    was tried first and rejected: its sensitivity scales with how much
    non-base cash the account holds, and on this account an inverted rate
    still reconciled to within 0.26% — comfortably inside any tolerance loose
    enough to survive rounding. yfinance separates the two hypotheses by 29%.
    It adds no new failure mode: paper_trader has already downloaded every
    ticker's history through yfinance before reaching this point.
    """
    net_liq, rates = {}, {}
    for v in ib.accountValues():
        try:
            val = float(v.value)
        except (TypeError, ValueError):
            continue
        if v.tag == "NetLiquidation":
            net_liq[v.currency] = val
        elif v.tag == "ExchangeRate":
            rates[v.currency] = val

    if not net_liq:
        raise RuntimeError("Could not read NetLiquidation from account values.")
    if "USD" in net_liq:
        return net_liq["USD"]  # nothing to convert

    base_ccy = next((c for c in net_liq if c != "BASE"), None)
    base_value = net_liq.get("BASE", net_liq.get(base_ccy))
    if base_value is None or base_ccy is None:
        raise RuntimeError(f"Could not identify base currency from {sorted(net_liq)}.")

    rate_usd = rates.get("USD")
    if not rate_usd or rate_usd <= 0:
        raise RuntimeError(
            "No usable USD ExchangeRate in account values — cannot convert "
            "NetLiquidation to USD, and sizing must not run on an unconverted number."
        )

    usd = base_value / rate_usd
    _verify_fx_direction(base_ccy, base_value, usd)
    print(f"(NetLiquidation {base_value:,.2f} {base_ccy} -> ${usd:,.2f} USD "
          f"via IBKR ExchangeRate {rate_usd:.6f}; no market-data line needed)")
    return usd


def _verify_fx_direction(base_ccy: str, base_value: float, usd_value: float,
                          tolerance: float = 0.05) -> None:
    """Raise unless `usd_value` matches an independent {base_ccy}USD quote.

    Catches an inverted ExchangeRate, which is otherwise invisible: both
    directions produce a plausible-looking number. See
    get_net_liquidation_usd's docstring for why this doesn't use IBKR's own
    cash arithmetic.
    """
    import yfinance as yf

    try:
        fx = yf.download(f"{base_ccy}USD=X", period="5d", progress=False, auto_adjust=True)
        if isinstance(fx.columns, pd.MultiIndex):
            fx.columns = fx.columns.get_level_values(0)
        ref = float(fx["Close"].dropna().iloc[-1])
    except Exception as e:
        raise RuntimeError(
            f"Could not fetch a reference {base_ccy}USD rate to verify the direction of "
            f"IBKR's ExchangeRate ({e}). Refusing to size orders on an unverified "
            f"conversion."
        )

    expected = base_value * ref
    if expected == 0 or abs(usd_value - expected) / abs(expected) > tolerance:
        raise RuntimeError(
            f"FX direction check FAILED: converted NetLiquidation to ${usd_value:,.2f} "
            f"but {base_ccy}USD={ref:.4f} implies ${expected:,.2f} "
            f"({abs(usd_value - expected) / abs(expected) * 100:.1f}% off). IBKR's "
            f"ExchangeRate may be inverted relative to what this code assumes. Refusing "
            f"to size orders on it."
        )


def execute_rebalance(ib, settings: dict, top: list, data: dict, top_n: int, signal_label: str,
                      auto_approve: bool = False, dry_run: bool = False,
                      approve_fn=None, ranked=None) -> bool:
    """Diff `top` against current IBKR holdings, size buys off fresh ATR,
    then execute exits-then-entries through RiskGuard/bracket orders.

    Shared by paper_trader.py's interactive y/n flow (auto_approve=False)
    and autotrade_runner.py's unattended flow (auto_approve=True) — same
    sizing, same RiskGuard limits, same journal/Telegram either way. Only
    the approval step differs; nothing about risk enforcement does.

    `data[t]` just needs to be OHLCV with a "Close"/"High"/"Low" column
    ind.atr() can read — daily for the monthly momentum/Kronos signal,
    hourly for autotrade_runner.py's faster cadence (see
    autotrade_signals.py) — the ATR window is however many bars are in
    `data[t]`, whatever that bar size means for the caller.

    `approve_fn` replaces the terminal y/n prompt with a callback, for
    front-ends that can't read stdin — currently the web UI's approve
    screen. It is handed the same proposal dict the prompt describes and
    returns True to proceed. This is the same kind of extension point
    `auto_approve` already is, and it exists for the same reason: so a
    second approval surface reuses this function rather than growing its
    own copy of the sizing and risk logic, which could then drift.

    Note what it deliberately preserves — the proposal shown to the approver
    and the orders subsequently placed come from ONE `buy_plan`, computed
    once above. An approval screen that re-priced between showing and
    sending would not be an approval of what was shown.

    Returns True if any orders were attempted (placed or declined),
    False if there was nothing to do."""
    tickers = settings["tickers"]
    guard = ibs.RiskGuard()
    net_liq = get_net_liquidation_usd(ib)
    print(f"NetLiquidation: ${net_liq:,.2f} (USD-equivalent)")

    held = get_current_holdings(ib, tickers)

    # Rotation margin: ties at the N/N+1 boundary resolve to holding, not to
    # a coin-flip trade. Applied HERE rather than in the signal functions
    # because this is the only place that knows what is currently held — and
    # applied inside the shared function so the human, autotrade and browser
    # paths cannot end up with different churn behaviour.
    margin_pct = float(settings.get("rotation_margin_pct", DEFAULT_ROTATION_MARGIN_PCT))
    gap = None
    if ranked is not None:
        gap = rank_boundary_gap(ranked, top_n)
        adjusted = apply_rotation_margin(ranked, top_n, held.keys(), margin_pct)
        if adjusted != list(top):
            kept = [s for s in adjusted if s not in top]
            dropped = [s for s in top if s not in adjusted]
            print(f"\nRotation margin ({margin_pct:g} pt): holding {', '.join(kept)} "
                  f"instead of rotating into {', '.join(dropped)} — the gap is "
                  f"inside the sampling noise, so this is not a signal.")
            top = adjusted

    sells = [sym for sym in held if sym not in top]
    holds = [t for t in top if t in held]
    buys = [t for t in top if t not in held]

    buy_plan = {}
    for t in buys:
        price = ibs.market_price(ib, ibs.stock(t))
        atr_val = float(ind.atr(data[t]).iloc[-1])
        qty = size_position(net_liq, price, atr_val, settings, guard)
        entry = round(price * (1 + ENTRY_LIMIT_BUFFER), 2)
        stop = round(price - STOP_ATR_MULT * atr_val, 2)
        buy_plan[t] = {"price": price, "atr": atr_val, "qty": qty, "entry": entry, "stop": stop}

    print("\n=== Proposed rebalance ===")
    if gap is not None:
        verdict = ("WIDE — the boundary is a real separation" if gap > margin_pct
                   else "NARROW — rank N/N+1 is within sampling noise")
        print(f"  rank {top_n}/{top_n + 1} gap: {gap:.2f} pt  [{verdict}]")
    if not sells and not buys:
        print("No changes — current holdings already match the target.")
    for sym in sells:
        p = held[sym]
        print(f"  SELL  {sym:6s}  {abs(p.position):.0f} sh  (dropped from top-{top_n})")
    for t in holds:
        print(f"  HOLD  {t:6s}  {held[t].position:.0f} sh  (still in target)")
    for t in buys:
        bp = buy_plan[t]
        flag = "  [size=0, will be blocked by RiskGuard]" if bp["qty"] <= 0 else ""
        print(f"  BUY   {t:6s}  {bp['qty']} sh @ ~{bp['entry']:.2f}  "
              f"stop {bp['stop']:.2f} (2xATR={bp['atr']:.2f}){flag}")

    if dry_run:
        print("\n[dry-run: no orders placed, no approval requested]")
        return False

    if not sells and not buys:
        return False

    if not auto_approve:
        if approve_fn is not None:
            proposal = {
                "sells": [{"symbol": s, "quantity": abs(held[s].position)} for s in sells],
                "holds": [{"symbol": t, "quantity": held[t].position} for t in holds],
                "buys": [{"symbol": t, **buy_plan[t]} for t in buys],
                "top": list(top),
                "top_n": top_n,
                "signal": signal_label,
                "net_liq_usd": net_liq,
            }
            approved = bool(approve_fn(proposal))
        else:
            approved = input("\nApprove this rebalance? [y/N] ").strip().lower() == "y"
        if not approved:
            print("Declined — no orders placed.")
            for sym in sells:
                ibs.journal("PROPOSAL", ibs.stock(sym), "SELL", abs(held[sym].position),
                            status="declined", detail="owner declined rebalance")
            for t in buys:
                bp = buy_plan[t]
                ibs.journal("PROPOSAL", ibs.stock(t), "BUY", bp["qty"], bp["entry"], bp["stop"],
                            status="declined", detail="owner declined rebalance")
            return True
    else:
        print("\n[auto-approved — no human prompt, RiskGuard still fully enforced]")

    exec_summary_lines = []

    # --- exits first: free up max_open_positions headroom before entries ---
    for sym in sells:
        p = held[sym]
        qty = abs(p.position)
        contract = ibs.stock(sym)
        cancel_open_orders_for(ib, sym)
        trade = ibs.place_market_order(ib, contract, qty, action="SELL", guard=guard,
                                        allow_no_stop=True, opening=False)
        if trade:
            ibs.wait_for_status(ib, trade)
            print(f"  SELL {sym}: {trade.orderStatus.status}")
            exec_summary_lines.append(f"SELL {sym} {qty:.0f}sh: {trade.orderStatus.status}")

    # --- entries ---
    for t in buys:
        bp = buy_plan[t]
        if bp["qty"] <= 0:
            print(f"  BUY {t}: skipped, computed size is 0")
            continue
        trades = ibs.place_bracket_order(ib, ibs.stock(t), bp["qty"], "BUY",
                                          entry_limit=bp["entry"], stop_price=bp["stop"],
                                          guard=guard)
        if trades:
            ibs.wait_for_status(ib, trades[0])
            print(f"  BUY {t}: {trades[0].orderStatus.status}")
            exec_summary_lines.append(
                f"BUY {t} {bp['qty']}sh @ ~{bp['entry']:.2f} "
                f"(stop {bp['stop']:.2f}): {trades[0].orderStatus.status}"
            )

    print("\nDone. Full record in trade_journal.csv.")
    if exec_summary_lines:
        mode = "auto-approved" if auto_approve else "approved"
        ibs.send_telegram(
            f"\U0001f504 Paper trader rebalance executed ({signal_label} signal, {mode})\n\n"
            + "\n".join(exec_summary_lines)
        )
    return True


def selftest() -> int:
    """Offline checks for the rotation-margin logic. No IBKR, no network.

    Reproduces the 2026-07-28 instability directly: the two real top-3s that
    came out of identical data thirty minutes apart must collapse to the same
    decision once the margin is applied.
    """
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def series(pairs):
        return pd.Series(dict(pairs)).sort_values(ascending=False)

    print("rank_boundary_gap:")
    r = series([("A", 0.05), ("B", 0.04), ("C", 0.03), ("D", 0.01)])
    check("gap is rank N minus rank N+1, in points",
          abs(rank_boundary_gap(r, 3) - 2.0) < 1e-9)
    check("no N+1 -> None", rank_boundary_gap(series([("A", 0.05)]), 3) is None)

    print("apply_rotation_margin — the real 2026-07-28 runs:")
    # Run 1 ranked GOOGL 3rd (+1.71) and DIS 4th (+1.59); run 2 swapped them
    # (GOOGL +0.89, DIS +2.26). Holding DIS, a strict sort sells it on run 1
    # and holds it on run 2 — the same account, the same data, opposite trades.
    run1 = series([("AMZN", 0.040), ("MSFT", 0.030), ("GOOGL", 0.0171), ("DIS", 0.0159)])
    run2 = series([("AMZN", 0.040), ("MSFT", 0.030), ("DIS", 0.0226), ("GOOGL", 0.0089)])
    held = {"DIS"}
    check("strict top-3 disagrees between the two runs",
          list(run1.index[:3]) != list(run2.index[:3]))
    m1 = apply_rotation_margin(run1, 3, held, 1.0)
    m2 = apply_rotation_margin(run2, 3, held, 1.0)
    check("margin makes both runs agree", m1 == m2)
    check("margin holds the incumbent rather than coin-flipping", "DIS" in m1)
    check("margin never grows the book past top_n", len(m1) == 3)

    print("apply_rotation_margin — it must not freeze the portfolio:")
    clear = series([("AMZN", 0.09), ("MSFT", 0.08), ("NVDA", 0.07), ("DIS", 0.001)])
    out = apply_rotation_margin(clear, 3, {"DIS"}, 1.0)
    check("a genuinely beaten incumbent is still dropped", "DIS" not in out)
    check("the challenger takes the slot", "NVDA" in out)

    print("apply_rotation_margin — degenerate inputs:")
    check("margin 0 restores strict ranking",
          apply_rotation_margin(run1, 3, held, 0.0) == list(run1.index[:3]))
    check("holding nothing is just the strict ranking",
          apply_rotation_margin(run1, 3, set(), 1.0) == list(run1.index[:3]))
    check("holding a name outside the ranking is ignored",
          apply_rotation_margin(run1, 3, {"TSLA"}, 1.0) == list(run1.index[:3]))
    check("no N+1 to defend against is just the strict ranking",
          apply_rotation_margin(series([("A", 0.05), ("B", 0.04)]), 3, {"B"}, 1.0)
          == ["A", "B"])

    print("FAILED" if failures else "\nAll rotation-margin selftests passed.")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(
        description="Phase 3 paper-trading loop: rule-based rebalance with mandatory human approval.")
    ap.add_argument("--selftest", action="store_true",
                    help="Run offline rotation-margin checks and exit (no IBKR connection).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Connect read-only, compute and print the proposal, place no orders, ask nothing.")
    ap.add_argument("--signal", choices=list(sp.KNOWN_SIGNALS), default=None,
                    help=f"Ranking source (default: {sp.DEFAULT_SIGNAL}, the project's main "
                         "signal). momentum is disabled and additionally requires "
                         "--allow-momentum.")
    ap.add_argument("--allow-momentum", action="store_true",
                    help="Owner opt-in to run the disabled momentum signal in THIS invocation. "
                         "Do not pass this unless the owner asked for momentum in this session.")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    settings = ta.load_settings()
    tickers = settings["tickers"]
    top_n = settings.get("momentum_top_n", 3)
    signal = sp.resolve_signal(settings, requested=args.signal)

    # Check before any fetching or connecting, and report it as a refusal
    # rather than a traceback — this is an expected answer, not a crash.
    try:
        sp.assert_allowed(signal, args.allow_momentum, context="paper_trader")
    except sp.SignalDisabled as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2

    if signal == "kronos":
        sys.path.insert(0, str(Path(__file__).parent / "KronosAI"))
        import kronos_agent as ka
        print("Computing Kronos forecast signal from fresh data "
              "(project's main signal; measured IC ~0 — this is a research "
              "direction, not a validated edge)...")
        top, data, ranked = ka.forecast_signal(settings)
        rank_label = f"predicted {ka.PRED_LEN}-trading-day return"
    else:
        # Raises SignalDisabled unless --allow-momentum was passed. Let it
        # propagate: refusing loudly beats quietly trading a different signal.
        print("Computing momentum signal from fresh data (owner opt-in)...")
        top, data, ranked = compute_signal(settings, allow_momentum=args.allow_momentum)
        rank_label = f"trailing {settings.get('momentum_lookback_m', 12)}-mo return"

    print(f"\nRanking ({rank_label}):")
    for t in ranked.index:
        marker = "  <= TOP" if t in top else ""
        print(f"  {t:6s} {ranked[t] * 100:+7.2f}%{marker}")
    if not top:
        print("\nAll candidates ranked negative (dual filter) — target is 100% cash.")

    # A rebalance placed into a closed market doesn't fail cleanly: the bracket
    # sits unfilled, wait_for_status times out on a non-terminal status, and the
    # journal records something ambiguous. Warn and make the human confirm —
    # not a hard block, since queueing deliberately is a legitimate choice.
    if not args.dry_run and not ibs.market_is_open():
        now_ny = datetime.now(ZoneInfo("America/New_York"))
        print(f"\n*** MARKET IS CLOSED — {now_ny:%Y-%m-%d %H:%M %Z (%A)}. "
              f"NYSE trades 09:30-16:00 ET on weekdays. ***")
        print("Orders placed now will sit unfilled until the next session, and "
              "entry limits priced off today's close may be stale by then.")
        if input("Place orders anyway? [y/N]: ").strip().lower() != "y":
            print("Aborted — nothing placed.")
            return 0

    port = settings.get("ibkr_port", 4002)
    print(f"\nConnecting to IBKR paper on port {port}"
          f"{' (read-only)' if args.dry_run else ''}...")
    # --dry-run connects readonly so TWS itself rejects order placement, rather
    # than relying on this path simply not reaching the order calls.
    ib = ibs.connect(port=port, client_id=settings.get("ibkr_client_id", 9),
                     readonly=args.dry_run)
    try:
        acct = ibs.verify_paper_account(ib)
        print(f"Connected. Paper account: {acct}")
        ib.reqMarketDataType(3)  # delayed data — this paper account has no live-data subscription
        execute_rebalance(ib, settings, top, data, top_n, signal_label=signal,
                          auto_approve=False, dry_run=args.dry_run, ranked=ranked)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main() or 0)
