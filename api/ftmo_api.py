#!/usr/bin/env python3
"""
api/ftmo_api.py — the FTMO venue's read surface for the web backend.

The counterpart to `ib_hub.py`. Same division of labour as the rest of `api/`:
this is a THIN WRAPPER. It owns no risk logic, no sizing and no thresholds —
`ftmo_rules` decides, `ftmo_sizing` sizes, `ftmo_session` transports, and this
file turns their output into JSON. The browser path and the terminal path must
not be able to disagree about a limit, which is the same reason the IBKR side
keeps order placement in `ibkr_service`.

BLOCKING, ON PURPOSE. `ftmo_session` runs Twisted on its own thread and every
one of its methods blocks the caller. FastAPI handlers therefore have to reach
it through `run_in_threadpool`, never directly from an async handler — calling
it inline would block the event loop for the length of a protobuf round trip
and stall every other request, including the ones the dashboard uses to show
that something is wrong.

WHY THE SESSION IS LAZY. Importing this module must not open a broker
connection: `--selftest`, `pytest` and a plain `import api.main` would all
acquire a live cTrader session as an import side effect. `get_session()`
connects on first use and every failure is reported as a state, never raised
into a handler, so a dashboard whose venue is down renders "disconnected"
rather than a 500.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import ftmo_rules as fr          # noqa: E402
import ftmo_service as svc       # noqa: E402
import ftmo_session as fs        # noqa: E402
import ftmo_signal as sig        # noqa: E402

log = logging.getLogger("api.ftmo")

_session: fs.FTMOSession | None = None
_lock = threading.Lock()
_state = {"status": "idle", "error": None, "connected_at": None}


def get_session(autostart: bool = True) -> fs.FTMOSession | None:
    """The shared session, started on first use. Never raises."""
    global _session
    with _lock:
        if _session is not None and _session.ready:
            return _session
        if not autostart:
            return None
        try:
            _state["status"] = "connecting"
            s = fs.FTMOSession()
            s.start()
            s.subscribe(_universe_symbols(s.specs))
            _session = s
            _state.update(status="connected", error=None,
                          connected_at=time.time())
            return _session
        except Exception as e:                                # noqa: BLE001
            log.warning("FTMO session failed to start: %s", e)
            _state.update(status="error", error=str(e).splitlines()[0])
            _session = None
            return None


def _universe_symbols(specs: dict) -> list[str]:
    try:
        return [s for s, _ in sig.build_universe(specs, sig.load_universe())]
    except Exception:                                         # noqa: BLE001
        return []


def connection_state() -> dict:
    s = _session
    return {"venue": "ftmo", "status": _state["status"], "error": _state["error"],
            "connected_at": _state["connected_at"],
            "account_id": s.account_id if s else None,
            "ready": bool(s and s.ready)}


def _account_state(session, positions) -> tuple[fr.AccountState, float]:
    """Build the rule engine's view, marking floating P&L at the EXIT side.

    Equity here is balance + floating P&L because that is what every FTMO
    limit is measured on. A position whose symbol has no quote yet contributes
    0.0 to floating P&L and is reported separately as `unpriced` — it is NOT
    silently marked at entry, which would present an unknown as a flat one.
    """
    acct = session.account()
    balance = acct["balance"]
    floating, unpriced = 0.0, 0
    for p in positions:
        q = session.quote(p.symbol)
        mark = q.exit_side_price(p.side) if q else None
        if mark is None or not p.entry_price:
            unpriced += 1
            continue
        units = p.volume / 100.0
        delta = (mark - p.entry_price) if p.side == "BUY" else (p.entry_price - mark)
        floating += delta * units
    equity = balance + floating
    return fr.AccountState(
        equity=equity, balance=balance, day_start_balance=balance,
        open_position_count=len(positions)), unpriced


def snapshot() -> dict:
    """Everything the dashboard needs in one call: account, limits, positions.

    One call rather than four because the numbers must be mutually consistent
    — an equity from one instant paired with a limit computed from another is
    how a dashboard shows a breach that is not real, or hides one that is.
    """
    session = get_session()
    if session is None:
        return {"connection": connection_state(), "account": None,
                "verdict": None, "positions": []}

    try:
        positions = session.refresh_positions()
        state, unpriced = _account_state(session, positions)
        verdict = fr.evaluate(fr.FTMOConfig(), state)
        rows = []
        for p in positions:
            q = session.quote(p.symbol)
            mark = q.exit_side_price(p.side) if q else None
            units = p.volume / 100.0
            pnl = None
            if mark is not None and p.entry_price:
                d = (mark - p.entry_price) if p.side == "BUY" else (p.entry_price - mark)
                pnl = d * units
            rows.append({
                "positionId": p.position_id, "symbol": p.symbol, "side": p.side,
                "volume": p.volume, "units": units,
                "entryPrice": p.entry_price, "stopLoss": p.stop_loss,
                "protected": p.protected, "mark": mark, "pnl": pnl,
                "quoteAgeS": q.age_s() if q else None,
            })
        return {
            "connection": connection_state(),
            "account": {"accountId": session.account_id,
                        "balance": state.balance, "equity": state.equity,
                        "floating": state.equity - state.balance,
                        "unpricedPositions": unpriced},
            "verdict": _verdict_json(verdict),
            "positions": rows,
        }
    except Exception as e:                                    # noqa: BLE001
        log.warning("FTMO snapshot failed: %s", e)
        _state.update(status="error", error=str(e).splitlines()[0])
        return {"connection": connection_state(), "account": None,
                "verdict": None, "positions": [], "error": str(e)}


def _verdict_json(v: fr.RuleVerdict) -> dict:
    """Every number the engine used, not just its conclusion.

    The dashboard shows progress toward each limit, so it needs the thresholds
    as well as the usage. Deliberately mirrors RuleVerdict field-for-field
    rather than recomputing anything here — recomputing a threshold in the
    presentation layer is how the browser and the terminal end up disagreeing
    about whether the account is safe.
    """
    return {
        "canOpen": v.can_open, "mustFlatten": v.must_flatten,
        "breached": v.breached, "reasons": list(v.reasons),
        "posture": ("BREACHED" if v.breached else "FLATTEN" if v.must_flatten
                    else "OK" if v.can_open else "BLOCKED"),
        "daily": {"used": v.daily_loss_used, "soft": v.daily_soft,
                  "flatten": v.daily_flatten, "hard": v.daily_hard},
        "drawdown": {"used": v.drawdown_used, "soft": v.drawdown_soft,
                     "flatten": v.drawdown_flatten, "hard": v.drawdown_hard,
                     "floorEquity": v.drawdown_floor_equity},
        "profit": {"usd": v.profit_usd, "targetUsd": v.profit_target_usd,
                   "targetReached": v.target_reached,
                   "minDaysMet": v.min_days_met,
                   "consistencyOk": v.consistency_ok, "canPass": v.can_pass},
    }


def quotes() -> list[dict]:
    session = get_session()
    if session is None:
        return []
    out = []
    for sym, spec in session.specs.items():
        q = session.quotes.get(spec["symbol_id"])
        if q is None:
            continue
        out.append({"symbol": sym, "bid": q.bid, "ask": q.ask,
                    "ageS": round(q.age_s(), 2)})
    return sorted(out, key=lambda r: r["symbol"])


def bars(symbol: str, period: str = "D1", count: int = 300) -> dict:
    session = get_session()
    if session is None:
        raise RuntimeError("FTMO session is not connected")
    rows = session.trendbars(symbol, period, count)
    return {"symbol": symbol, "period": period, "bars": rows}


def universe() -> list[dict]:
    """The configured, tradeable universe with its per-symbol venue specs."""
    try:
        specs = svc.load_symbol_specs()
    except FileNotFoundError:
        return []
    try:
        pairs = sig.build_universe(specs, sig.load_universe())
    except ValueError as e:
        return [{"error": str(e)}]
    return [{"symbol": s, "assetClass": c,
             "minVolume": specs[s]["min_volume"],
             "stepVolume": specs[s]["step_volume"],
             "digits": specs[s]["digits"],
             "quoteAsset": specs[s]["quote_asset"]} for s, c in pairs]
