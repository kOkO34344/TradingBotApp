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

import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import ftmo_rules as fr          # noqa: E402
import ftmo_runner as runner     # noqa: E402
import ftmo_service as svc       # noqa: E402
import ftmo_session as fs        # noqa: E402
import ftmo_signal as sig        # noqa: E402

# Imported both ways on purpose: as a package under uvicorn, and as bare
# modules when this file is run directly for `--selftest` (sys.path[0] is
# then `api/`). Without the fallback the offline selftest — the thing that
# needs no server, no venue and no credentials — is the one path that cannot
# run.
try:
    from . import indicators_api, journal_api  # noqa: E402
except ImportError:                            # pragma: no cover - script mode
    import indicators_api                      # type: ignore  # noqa: E402
    import journal_api                         # type: ignore  # noqa: E402

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


# The chart's timeframe keys mapped to cTrader periods, with how many bars to
# pull for each. The keys match the IBKR chart's so the switcher, the stored
# preference and the URL all keep working across venues — only the transport
# underneath changed.
#
# There is no `duration` here and that is the real difference from `api/bars.py`.
# IBKR is asked for a span of time and decides how many bars that is; cTrader is
# asked for a bar COUNT. Pretending the two are the same interface would mean
# inventing a duration string the venue never sees.
CHART_PERIODS: dict[str, tuple[str, int]] = {
    "1m":  ("M1",  400),
    "5m":  ("M5",  400),
    "15m": ("M15", 400),
    "30m": ("M30", 400),
    "1h":  ("H1",  500),
    "4h":  ("H4",  500),
    "1d":  ("D1",  500),
}

DEFAULT_TIMEFRAME = "1d"


def _to_chart_bar(bar: dict) -> dict:
    """Rename `ts` to `time`, which is what the whole chart stack speaks.

    `ftmo_session.trendbars()` emits `ts`, and it is NOT changed to match:
    Kronos, `ftmo_signal` and the runner all read `ts`, and renaming a key
    under them to save a translation here would be the tail wagging the dog.

    Doing it at the API boundary also means `indicators_api` keeps working
    unchanged for both venues — it is written against `time`, so an FTMO bar
    reaching it as `ts` raised a bare `KeyError: 'time'` that surfaced in the
    browser as the useless message `{"detail": "'time'"}`.
    """
    out = dict(bar)
    out["time"] = out.pop("ts")
    return out


def asset_class_of(symbol: str) -> str:
    """This symbol's asset class, or "" when it isn't in the traded universe.

    Returns "" rather than guessing: the class drives what a chart badge
    claims an instrument IS, and the universe is the only thing that knows.
    All 202 venue symbols are chartable, but only the configured subset is
    classified — a chart of an unclassified symbol is fine, a mislabelled one
    is not.
    """
    try:
        specs = svc.load_symbol_specs()
        for sym, cls in sig.build_universe(specs, sig.load_universe()):
            if sym == symbol:
                return cls
    except (FileNotFoundError, ValueError):
        return ""
    return ""


def timeframe_list() -> list[dict]:
    """What the chart's timeframe switcher may offer for this venue."""
    return [{"key": k, "period": p, "count": n}
            for k, (p, n) in CHART_PERIODS.items()]


def resolve_period(timeframe: str) -> tuple[str, int]:
    """Accept a chart key ("1h") or a raw cTrader period ("H1").

    Both spellings are allowed because `bars()` already had callers passing
    `period="D1"` directly. Refusing one of them to tidy the interface would
    break a working path for cosmetic reasons.
    """
    key = (timeframe or "").strip()
    if key in CHART_PERIODS:
        return CHART_PERIODS[key]
    upper = key.upper()
    for period, count in CHART_PERIODS.values():
        if period == upper:
            return period, count
    raise ValueError(
        f"unknown timeframe {timeframe!r}; known: "
        f"{', '.join(CHART_PERIODS)} (or {', '.join(p for p, _ in CHART_PERIODS.values())})")


def bars(symbol: str, period: str = "D1", count: int | None = None,
         indicators: list[str] | None = None, levels: bool = False,
         markers: bool = False) -> dict:
    """Bars plus optional overlays for one FTMO symbol, in one round trip.

    Bundled for the same reason `/api/bars` bundles: separate calls would make
    the chart paint price before its overlays and flicker.

    The indicator and level math comes from `indicators_api`, which is a thin
    layer over `indicators.py` — the project's single source of truth for
    technical math. A CFD chart and a stock chart therefore show the same
    number for the same series, which is the whole point of that rule.

    Markers are filtered to `venue="ftmo"`. Without that filter an FTMO EURUSD
    chart could draw IBKR fills for a same-named instrument at different
    prices, which is a marker asserting something that never happened here.
    """
    session = get_session()
    if session is None:
        raise RuntimeError("FTMO session is not connected")
    resolved, default_count = resolve_period(period)
    rows = [_to_chart_bar(b)
            for b in session.trendbars(symbol, resolved, count or default_count)]
    spec = session.specs.get(symbol) or {}
    payload = {
        "symbol": symbol,
        # The venue's instrument name IS its label — unlike IBKR there is no
        # separate contract to resolve one from.
        "label": symbol,
        "kind": asset_class_of(symbol),
        # How many decimals this instrument prices in. The chart needs it
        # because FTMO spans 2-digit indices and 5-digit FX in one universe,
        # so a single hardcoded precision would round US30 fine and flatten
        # every EURUSD candle into a straight line.
        "digits": spec.get("digits"),
        "period": resolved,
        "timeframe": period,
        "bars": rows,
        "count": len(rows),
        "venue": "ftmo",
        # cTrader streams its own prints; there is no delayed-data
        # subscription tier in play here the way there is on the IBKR path.
        "delayed": False,
    }
    payload["indicators"] = (indicators_api.compute(rows, indicators)
                             if indicators else [])
    payload["levels"] = indicators_api.levels(rows) if levels else None
    payload["markers"] = (journal_api.markers_for(symbol, venue="ftmo")
                          if markers else [])
    return payload


def autotrade_state() -> dict:
    """Whether the FTMO runner is armed, plus the settings it would run with.

    Read straight from `trader_settings.json` through `ftmo_runner`'s own
    accessor rather than re-parsing the block here, so the browser cannot
    report a configuration different from the one the runner would actually
    use. The runner defaults every absent key to OFF and to the most
    conservative value, and that behaviour is what the UI shows.
    """
    cfg = runner.autotrade_config(runner.load_settings())
    state = runner.load_state()
    return {
        "enabled": cfg["enabled"],
        "riskPct": cfg["risk_pct"],
        "rotationMarginPct": cfg["margin_pct"],
        "topN": cfg["top_n"],
        "sampleCount": cfg["sample_count"],
        "product": cfg["product"],
        "bufferPct": cfg["buffer_pct"],
        "dayState": (state.to_json() if state else None),
    }


def set_autotrade(enabled: bool) -> dict:
    """Arm or disarm the FTMO runner.

    Deliberately independent of the IBKR autotrade toggle and of IB Gateway's
    health. Rule 9 retired IBKR for new orders, so gating this venue's switch
    on a dead Gateway would make it impossible to disarm FTMO for a reason
    that has nothing to do with FTMO.

    Writes raw JSON rather than going through `trader_app.load_settings()`,
    which merges DEFAULT_SETTINGS and would write a pile of unrelated defaults
    into the owner's config as a side effect of flipping one boolean. The write
    is atomic so a crash cannot leave a half-written config that reads as
    disabled by accident — or worse, as enabled.

    Both directions are journalled. Rule 6 is about orders, but "who armed the
    robot, and when" belongs in the same trail, especially for a path CLAUDE.md
    flags as a deliberate exception to rule 5.
    """
    path = runner.SETTINGS
    raw = json.loads(path.read_text())
    ftmo_block = dict(raw.get("ftmo") or {})
    autotrade = dict(ftmo_block.get("autotrade") or {})
    was = bool(autotrade.get("enabled", False))
    autotrade["enabled"] = bool(enabled)
    ftmo_block["autotrade"] = autotrade
    raw["ftmo"] = ftmo_block

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2))
    tmp.replace(path)

    if was != bool(enabled):
        runner.journal_ftmo(
            "NOTE",
            status="autotrade " + ("enabled" if enabled else "DISABLED"),
            detail=(f"FTMO autotrade turned {'ON' if enabled else 'OFF'} from "
                    f"the web UI. Signal: kronos. The rule engine, sizer and "
                    f"stop attachment stay enforced either way."))
        log.info("FTMO autotrade %s via web UI",
                 "enabled" if enabled else "disabled")
    return {"autotrade": autotrade_state(), "changed": was != bool(enabled)}


def plan(ctx, sample_count: int | None = None) -> dict:
    """What Kronos would trade on FTMO right now. Places NOTHING.

    The read-only twin of `ftmo_runner.run()` — same universe, same bars, same
    ranking, same rule engine and the same sizer, so the browser preview and
    the unattended run cannot propose different orders. Anything that only the
    preview knows how to compute would be a second implementation of the risk
    maths, which is the thing the whole `api/` layer exists to avoid.

    Runs inside a job thread: Kronos inference takes minutes and the session's
    methods all block.
    """
    cfg = runner.autotrade_config(runner.load_settings())
    samples = int(sample_count or cfg["sample_count"])

    ctx.log("Building the FTMO universe from the captured symbol specs…")
    specs = svc.load_symbol_specs()
    pairs = sig.build_universe(specs, sig.load_universe())
    symbols = [s for s, _ in pairs]
    classes = dict(pairs)
    ctx.log(f"{len(symbols)} symbols across {len(set(classes.values()))} classes")

    session = get_session()
    if session is None:
        raise RuntimeError(
            f"FTMO session is not connected: {_state.get('error') or 'unknown'}")

    ctx.progress(0.05, "Reading the account…")
    positions = session.refresh_positions()
    acct = session.account()
    now = datetime.now(runner.PRAGUE)
    config = runner.config_from(cfg)
    state_obj, notes = runner.advance_state(runner.load_state(),
                                            acct["balance"], now, config)
    for n in notes:
        ctx.log(n)
    account_state, unpriced = runner.build_account_state(session, positions,
                                                         state_obj)
    verdict = fr.evaluate(config, account_state)
    ctx.log(verdict.summary())

    ctx.progress(0.12, "Pulling daily bars from the venue…")
    sys.path.insert(0, str(BASE_DIR / "KronosAI"))
    import kronos_agent as ka                                 # noqa: PLC0415
    import pandas as pd                                       # noqa: PLC0415

    bars_by_symbol, frames, rejected = {}, {}, []
    for i, sym in enumerate(symbols):
        ctx.raise_if_cancelled()
        ctx.progress(0.12 + 0.28 * (i / max(1, len(symbols))), f"Bars: {sym}")
        try:
            session.assert_bars_match_quote(sym)
        except Exception as e:                                # noqa: BLE001
            rejected.append({"symbol": sym, "reason": str(e)})
            ctx.log(f"{sym}: {e}")
            continue
        rows = session.trendbars(sym, "D1", sig.BARS_NEEDED)
        if len(rows) < ka.LOOKBACK:
            rejected.append({"symbol": sym,
                             "reason": f"only {len(rows)} daily bars, "
                                       f"need {ka.LOOKBACK}"})
            continue
        bars_by_symbol[sym] = rows
        idx = pd.to_datetime([b["ts"] for b in rows], unit="s")
        frames[sym] = pd.DataFrame(
            {"open": [b["open"] for b in rows], "high": [b["high"] for b in rows],
             "low": [b["low"] for b in rows], "close": [b["close"] for b in rows],
             "volume": [b["volume"] for b in rows]}, index=idx)
    ctx.log(f"bars usable for {len(frames)}/{len(symbols)} symbols")
    if not frames:
        raise RuntimeError("No symbol had usable daily history — nothing to "
                           "forecast. Check the venue connection and the "
                           "symbol capture.")

    ctx.progress(0.45, f"Kronos forecast, sample_count={samples}…")
    t0 = time.time()
    _, _, pred_dfs = ka.forecast_frames(frames, sample_count=samples)
    ctx.log(f"forecast for {len(pred_dfs)} symbols in {time.time() - t0:.0f}s")

    ctx.progress(0.92, "Ranking and sizing…")
    ranked = sig.rank_candidates(pred_dfs, bars_by_symbol, classes)
    held = [p.symbol for p in positions]
    proposal = sig.plan_orders(session, config, account_state, ranked, held,
                               risk_pct=cfg["risk_pct"],
                               margin_pct=cfg["margin_pct"],
                               top_n=cfg["top_n"])

    ctx.progress(1.0, "Done.")
    return {
        "generatedAt": time.time(),
        "armed": cfg["enabled"],
        "sampleCount": samples,
        "topN": cfg["top_n"],
        "rotationMarginPct": cfg["margin_pct"],
        "verdict": _verdict_json(verdict),
        "account": {"balance": account_state.balance,
                    "equity": account_state.equity,
                    "dayStartBalance": account_state.day_start_balance,
                    "unpricedPositions": unpriced},
        "held": held,
        "target": proposal.get("target", []),
        "exits": proposal["exits"],
        "entries": proposal["entries"],
        "skipped": proposal["skipped"],
        "rankGap": proposal.get("rank_gap"),
        "gapIsNarrow": (proposal.get("rank_gap") is not None
                        and proposal["rank_gap"] < 1.0),
        "rejectedSymbols": rejected,
        "ranked": [{"symbol": c.symbol, "assetClass": c.asset_class,
                    "predictedReturnPct": c.predicted_return_pct,
                    "lastClose": c.last_close, "atr": c.atr}
                   for c in ranked],
    }


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


def all_symbols() -> list[dict]:
    """Every symbol the venue carries, for the chart's symbol search.

    Deliberately wider than `universe()`. That returns the ~14 symbols the
    RUNNER is configured to trade; all 202 are chartable, and a search box that
    could only find the ones already being traded would be useless for deciding
    what to trade next.

    `assetClass` is filled in only for symbols in the configured universe —
    everything else reports "", because the universe is the only thing that
    knows, and a guessed label on a chart is worse than no label.
    """
    try:
        specs = svc.load_symbol_specs()
    except FileNotFoundError:
        return []
    classes: dict[str, str] = {}
    try:
        classes = dict(sig.build_universe(specs, sig.load_universe()))
    except ValueError:
        pass
    return sorted(
        ({"symbol": name,
          "assetClass": classes.get(name, ""),
          "digits": spec.get("digits"),
          "quoteAsset": spec.get("quote_asset", "")}
         for name, spec in specs.items()),
        key=lambda r: (r["assetClass"] == "", r["symbol"]),
    )


def selftest() -> int:
    """Offline. No venue, no credentials, no session.

    Covers only the pure chart-payload helpers. Everything else here needs a
    live cTrader session by definition, and a selftest that mocks a broker
    mostly tests the mock.
    """
    failures = []

    def check(name, cond):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    print("timeframe resolution accepts both spellings:")
    check("a chart key resolves", resolve_period("1h")[0] == "H1")
    check("a raw cTrader period resolves", resolve_period("H1")[0] == "H1")
    check("the default is a day bar",
          resolve_period(DEFAULT_TIMEFRAME)[0] == "D1")
    check("case is not significant for a period", resolve_period("d1")[0] == "D1")
    check("every advertised timeframe resolves",
          all(resolve_period(t["key"])[0] == t["period"]
              for t in timeframe_list()))
    check("each timeframe asks for a positive bar count",
          all(t["count"] > 0 for t in timeframe_list()))
    try:
        resolve_period("1y")
        check("an unknown timeframe raises rather than defaulting", False)
    except ValueError:
        check("an unknown timeframe raises rather than defaulting", True)

    print("bars are renamed for the chart, not for the venue:")
    src = {"ts": 1786122000, "open": 1.1, "high": 1.2,
           "low": 1.0, "close": 1.15, "volume": 42}
    out = _to_chart_bar(src)
    check("`ts` becomes `time`", out["time"] == 1786122000)
    check("`ts` does not survive alongside it", "ts" not in out)
    check("OHLCV is untouched",
          (out["open"], out["high"], out["low"], out["close"], out["volume"])
          == (1.1, 1.2, 1.0, 1.15, 42))
    check("the caller's row is NOT mutated — ftmo_session's rows are reused "
          "by Kronos and the runner, which both read `ts`",
          src.get("ts") == 1786122000 and "time" not in src)

    print("a symbol is not given a class it has not earned:")
    check("a symbol outside the traded universe reports no class",
          asset_class_of("NOT_A_REAL_SYMBOL_XYZ") == "")

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All ftmo_api offline checks passed.")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="FTMO web read surface")
    ap.add_argument("--selftest", action="store_true",
                    help="run the offline checks and exit")
    parsed = ap.parse_args()
    if parsed.selftest:
        sys.exit(selftest())
    ap.print_help()
