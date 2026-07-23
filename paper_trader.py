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

Signal: momentum rotation (default, top-N of watchlist by trailing N-month
return — the strategy that's actually earned Phase 3), or Kronos
(--signal kronos, KronosAI/kronos_agent.py's forecast ranking). Both
return the same (top, data, ranked) shape, so everything downstream of
signal selection — sizing, approval, execution, journaling — is identical
regardless of which produced `top`. Kronos was walk-forward backtested
2026-07-23 (see CLAUDE.md empirical findings): near-zero information
coefficient (0.036) and a 50% directional hit rate — no measurable
forecasting skill detected in the one honest post-cutoff window
available. It's kept as an opt-in signal for reference/re-testing, not
because it's shown any edge; momentum stays the default.

Sizing: qty = floor((NetLiquidation * risk_pct_per_trade%) / (2*ATR)),
clamped so qty*price never exceeds RiskGuard's max_order_notional_usd. This
ties position size to stop distance (2x daily ATR-14), not the other way
around, per the knowledge base's risk rules.

Usage:
  python3 paper_trader.py                    full run: momentum signal, propose, ask, execute
  python3 paper_trader.py --signal kronos    same, but ranked by Kronos's forecast instead
  python3 paper_trader.py --dry-run          connect + compute + print only, no
                                              orders, no approval prompt
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import ibkr_service as ibs
import indicators as ind
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


def compute_signal(settings: dict):
    """Fresh momentum ranking: top-N of watchlist by trailing N-month return.
    Always re-fetches through today (force=True) — ranking off a stale
    price_data/ cache would drive real paper orders off old prices."""
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
    """NetLiquidation in the account's base currency, converted to USD if
    needed — the watchlist is US equities, so all sizing math (price, ATR,
    stops) is USD regardless of what currency the account itself holds."""
    net_liq, currency = None, None
    for v in ib.accountSummary():
        if v.tag == "NetLiquidation":
            net_liq, currency = float(v.value), v.currency
            break
    if net_liq is None:
        raise RuntimeError("Could not read NetLiquidation from account summary.")
    if currency in ("USD", "BASE"):
        return net_liq
    rate = ibs.market_price(ib, ibs.forex_pair(f"{currency}USD"))
    print(f"(account base currency is {currency}; converted at {currency}USD={rate:.4f})")
    return net_liq * rate


def execute_rebalance(ib, settings: dict, top: list, data: dict, top_n: int, signal_label: str,
                      auto_approve: bool = False, dry_run: bool = False) -> bool:
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

    Returns True if any orders were attempted (placed or declined),
    False if there was nothing to do."""
    tickers = settings["tickers"]
    guard = ibs.RiskGuard()
    net_liq = get_net_liquidation_usd(ib)
    print(f"NetLiquidation: ${net_liq:,.2f} (USD-equivalent)")

    held = get_current_holdings(ib, tickers)
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
        if input("\nApprove this rebalance? [y/N] ").strip().lower() != "y":
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


def main():
    ap = argparse.ArgumentParser(
        description="Phase 3 paper-trading loop: rule-based rebalance with mandatory human approval.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Connect read-only, compute and print the proposal, place no orders, ask nothing.")
    ap.add_argument("--signal", choices=["momentum", "kronos"], default="momentum",
                    help="Ranking source (default: momentum, the validated strategy). "
                         "kronos = KronosAI/kronos_agent.py's forecast ranking — backtested, no "
                         "measurable edge found (IC 0.036, 50% hit rate), opt-in only.")
    args = ap.parse_args()

    settings = ta.load_settings()
    tickers = settings["tickers"]
    top_n = settings.get("momentum_top_n", 3)

    if args.signal == "kronos":
        sys.path.insert(0, str(Path(__file__).parent / "KronosAI"))
        import kronos_agent as ka
        print("Computing Kronos forecast signal from fresh data "
              "(backtested, no measurable edge found — opted in via --signal kronos)...")
        top, data, ranked = ka.forecast_signal(settings)
        rank_label = f"predicted {ka.PRED_LEN}-trading-day return"
    else:
        print("Computing momentum signal from fresh data...")
        top, data, ranked = compute_signal(settings)
        rank_label = f"trailing {settings.get('momentum_lookback_m', 12)}-mo return"

    print(f"\nRanking ({rank_label}):")
    for t in ranked.index:
        marker = "  <= TOP" if t in top else ""
        print(f"  {t:6s} {ranked[t] * 100:+7.2f}%{marker}")
    if not top:
        print("\nAll candidates ranked negative (dual filter) — target is 100% cash.")

    port = settings.get("ibkr_port", 4002)
    print(f"\nConnecting to IBKR paper on port {port}...")
    ib = ibs.connect(port=port, client_id=settings.get("ibkr_client_id", 9))
    try:
        acct = ibs.verify_paper_account(ib)
        print(f"Connected. Paper account: {acct}")
        ib.reqMarketDataType(3)  # delayed data — this paper account has no live-data subscription
        execute_rebalance(ib, settings, top, data, top_n, signal_label=args.signal,
                          auto_approve=False, dry_run=args.dry_run)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
