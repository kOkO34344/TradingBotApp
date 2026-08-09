"""
main.py — FastAPI app for the TradingBotApp web UI.

Local only. It binds to 127.0.0.1 by default and is never deployed: it can
arm the unattended FTMO runner, so exposing it would put that switch on the
network. `web/README.md` says the same thing in the place someone is likely to
read it before running `--host 0.0.0.0`.

FTMO IS THE ONLY VENUE (2026-08-09). IBKR was retired in place on 2026-08-02
and removed entirely a week later — the modules, the routes, the screens and
its two launchd jobs are all gone. Nothing here dials IB Gateway, which is why
this file no longer carries a connection hub, a write worker, a paper-account
gate or a symbol resolver: those were all IBKR machinery. The history stays in
git and in `trade_journal.csv`, whose `venue=ibkr` rows are still read and
still displayed.

Route groups:
    /api/status        signal policy, journal counts, FTMO arm state
    /api/journal       trade_journal.csv, corrections annotated, both venues
    /api/indicators    catalog (series come from /api/ftmo/bars)
    /api/kronos        forecast + Monte Carlo jobs
    /api/backtests     recorded results and quoted findings
    /api/jobs          job status for the long-running routes above
    /api/ftmo/*        the venue: snapshot, bars, universe, plan, arm, timeline
    /ws/ftmo           one server-computed snapshot per second

Every response that carries market data also carries where it came from and
how old it is. That is not decoration: this project has twice been misled by a
record that looked authoritative and wasn't, so the UI is built to make
staleness and disagreement visible rather than smoothing them over.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import ftmo_runner as ftmo_runner_mod  # noqa: E402
import signal_policy  # noqa: E402
import trader_app as ta  # noqa: E402

from . import backtests_api  # noqa: E402
from . import ftmo_api  # noqa: E402
from . import indicators_api  # noqa: E402
from . import jobs  # noqa: E402
from . import journal_api  # noqa: E402
from . import kronos_api  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("api.main")


def _settings() -> dict:
    """Read trader_settings.json through trader_app, so defaults match."""
    return ta.load_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start nothing, connect to nothing.

    The FTMO session is lazy by design (see `ftmo_api.get_session`): importing
    this module must not open a broker connection, or `--selftest`, `pytest`
    and a plain `import api.main` would each acquire one as a side effect.
    Startup therefore has nothing to do — which is itself the point, since the
    IBKR version dialled a gateway here and spent months reporting a refused
    connection to every screen on every poll.
    """
    log.info("FTMO is the only venue; nothing is dialled at startup")
    try:
        yield
    finally:
        jobs.registry.shutdown()
        ftmo_api.shutdown()


app = FastAPI(title="TradingBotApp API", version="0.2.0", lifespan=lifespan)

# The Next.js dev server runs on 3000; only localhost origins are allowed,
# matching the local-only deployment decision.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _fail(exc: Exception) -> HTTPException:
    """Turn an internal error into something the UI can show verbatim."""
    if isinstance(exc, HTTPException):
        return exc                      # already shaped; don't relabel as 500
    if isinstance(exc, ConnectionError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, indicators_api.IndicatorError):
        return HTTPException(status_code=400, detail=str(exc))
    log.exception("Unhandled error")
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ status

@app.get("/api/status")
async def status():
    """Everything the app shell needs that isn't on the FTMO socket.

    Deliberately cheap and venue-independent: it reads settings and a CSV, so
    it answers while cTrader is unreachable. The shell uses it to render at
    all; anything that needs the venue comes over /ws/ftmo, which is allowed
    to be down.
    """
    settings = _settings()
    signal = settings.get("signal", signal_policy.DEFAULT_SIGNAL)

    # The runner's own window function, not a copy of the rule. A second
    # implementation of "16:30-11:30 Sofia, wrapping midnight, except Sunday"
    # is how the dashboard and the runner would come to disagree about whether
    # the robot is due to fire.
    try:
        now = datetime.now(ftmo_runner_mod.TRADING_TZ)
        in_window, window_reason = ftmo_runner_mod.within_trading_window(now)
    except Exception as exc:                            # noqa: BLE001
        in_window, window_reason = None, f"{type(exc).__name__}: {exc}"

    return {
        "venue": "ftmo",
        "signal": {
            "active": signal,
            "default": signal_policy.DEFAULT_SIGNAL,
            "disabled": sorted(signal_policy.DISABLED_SIGNALS),
        },
        "journal": journal_api.summary(),
        "tradingWindow": {"open": in_window, "reason": window_reason},
        "settings": {
            "riskPctPerTrade": settings.get("risk_pct_per_trade"),
            "benchmark": settings.get("benchmark"),
        },
    }


@app.get("/api/indicators/catalog")
async def indicator_catalog():
    return {"indicators": indicators_api.catalog()}


# ----------------------------------------------------------------- journal

@app.get("/api/journal")
async def journal(
    symbol: str | None = Query(None),
    event: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    """The full journal, both venues.

    IBKR's 46 rows are still served and still labelled `ibkr`. Rule 6 makes
    this file the audit trail, and an audit trail you prune when a venue is
    retired is not one — the removal is of the CODE, not of what it did.
    """
    rows = journal_api.load_rows()
    if symbol:
        rows = [r for r in rows if r.symbol.upper() == symbol.upper()]
    if event:
        wanted = {e.strip().upper() for e in event.split(",")}
        rows = [r for r in rows if r.event.upper() in wanted]
    rows = rows[-limit:]
    return {"rows": [r.as_dict() for r in reversed(rows)],
            "summary": journal_api.summary()}


# -------------------------------------------------------------------- FTMO

@app.get("/api/ftmo/snapshot")
async def ftmo_snapshot():
    """Account, rule-engine verdict and positions in ONE consistent read."""
    return await run_in_threadpool(ftmo_api.snapshot)


@app.get("/api/ftmo/connection")
async def ftmo_connection():
    return ftmo_api.connection_state()


@app.get("/api/ftmo/quotes")
async def ftmo_quotes():
    return {"quotes": await run_in_threadpool(ftmo_api.quotes)}


@app.get("/api/ftmo/universe")
async def ftmo_universe():
    return {"universe": await run_in_threadpool(ftmo_api.universe)}


@app.get("/api/ftmo/bars")
async def ftmo_bars(
    symbol: str,
    period: str = Query(ftmo_api.DEFAULT_TIMEFRAME,
                        description="Chart key (1m..1d) or cTrader period (M1..D1)"),
    count: int | None = Query(None, description="Bars to pull; per-timeframe default"),
    indicators: str | None = Query(None, description="Comma list, e.g. sma:20,rsi:14"),
    levels: bool = Query(False, description="Include swing support/resistance"),
    markers: bool = Query(True, description="Include this symbol's FTMO journal fills"),
):
    """Bars + overlays for one FTMO symbol. The venue's own prints, not a proxy.

    `symbol` is an FTMO instrument name (`EURUSD`, `US30.cash`). There is no
    contract resolution step: that resolver spoke IBKR contracts, knew nothing
    about CFDs, and went with the rest of the venue.
    """
    specs = [s.strip() for s in (indicators or "").split(",") if s.strip()]
    try:
        return await run_in_threadpool(
            ftmo_api.bars, symbol, period, count, specs, levels, markers)
    except ValueError as e:              # an unknown timeframe is the caller's
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/ftmo/symbols")
async def ftmo_symbols():
    """Every chartable FTMO instrument, not just the traded universe."""
    return {"symbols": await run_in_threadpool(ftmo_api.all_symbols)}


@app.get("/api/ftmo/timeframes")
async def ftmo_timeframes():
    return {"timeframes": ftmo_api.timeframe_list(),
            "default": ftmo_api.DEFAULT_TIMEFRAME}


@app.get("/api/ftmo/timeline")
async def ftmo_timeline():
    """Last night's session, slot by slot, for the night band.

    Reads the audit trail off disk — no venue, no session — so it answers
    while the broker is unreachable. That is deliberate: "what did the runner
    do overnight" is exactly the question you ask when the venue is down.
    """
    return await run_in_threadpool(ftmo_api.timeline)


class FtmoAutotradeToggle(BaseModel):
    enabled: bool


@app.get("/api/ftmo/autotrade")
async def ftmo_autotrade_state():
    """Whether the FTMO runner is armed, and what it would run with."""
    return await run_in_threadpool(ftmo_api.autotrade_state)


@app.post("/api/ftmo/autotrade")
async def ftmo_set_autotrade(payload: FtmoAutotradeToggle):
    """Arm or disarm unattended Kronos trading on FTMO.

    Deliberately not gated on the venue being reachable. The whole point of a
    kill switch is that it works when things are going wrong, and a switch you
    cannot reach then is not a switch. It edits `trader_settings.json`, which
    `ftmo_runner.py` re-reads on every firing, so the change takes effect from
    the next wakeup without touching launchd.
    """
    try:
        return await run_in_threadpool(ftmo_api.set_autotrade, payload.enabled)
    except Exception as exc:                                  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"Could not change FTMO autotrade: {exc}")


@app.post("/api/ftmo/plan")
async def ftmo_plan(sampleCount: int | None = None):
    """Start a job computing what Kronos would trade on FTMO right now.

    Read-only: it runs the identical pipeline the unattended runner uses and
    stops before placing anything. One at a time, for the same reason the
    Kronos route refuses concurrent runs — two batch inferences on this
    machine produce results that look comparable but were computed under
    different load.
    """
    existing = jobs.registry.running("ftmo-plan")
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An FTMO plan is already in progress (job {existing[0].id}).")
    job = jobs.registry.submit(
        "ftmo-plan",
        lambda ctx: ftmo_api.plan(ctx, sample_count=sampleCount),
        params={"sampleCount": sampleCount},
    )
    return job.as_dict()


@app.websocket("/ws/ftmo")
async def ftmo_stream(ws: WebSocket):
    """Push the FTMO snapshot on a timer.

    Polled rather than event-driven, deliberately. The equity that matters is
    balance + floating P&L across every open position, so it changes on every
    tick of every held symbol — forwarding raw ticks would push far more
    messages than the UI can use and still leave the browser to recompute
    equity, which is exactly the recomputation that must not live in the
    presentation layer. One consistent server-computed snapshot per second is
    both cheaper and harder to get wrong.

    The first frame is sent immediately so a client connecting during an
    outage learns about it at once instead of looking healthy for a second.
    """
    await ws.accept()
    try:
        while True:
            snap = await run_in_threadpool(ftmo_api.snapshot)
            await ws.send_json({"topic": "ftmo", "data": snap})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception:                                         # noqa: BLE001
        log.debug("FTMO WebSocket closed", exc_info=True)


# ----------------------------------------------------------------- kronos

class KronosRunRequest(BaseModel):
    tickers: list[str] | None = None
    draws: int = kronos_api.DEFAULT_DRAWS
    sampleCount: int = kronos_api.DEFAULT_SAMPLE_COUNT


class MonteCarloRequest(BaseModel):
    ticker: str
    paths: int = 12


@app.post("/api/kronos/run")
async def kronos_run(req: KronosRunRequest):
    """Start a forecast job. Returns immediately with a job id.

    Refuses to start a second run while one is in flight: two concurrent
    batch inferences on this machine would slow each other down and produce
    two results that look comparable but were computed under different load.
    """
    existing = jobs.registry.running("kronos")
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A Kronos run is already in progress (job {existing[0].id}). "
                   "Wait for it to finish or cancel it.")

    settings = _settings()
    tickers = [t.upper() for t in (req.tickers or settings.get("tickers", []))]
    if not tickers:
        raise HTTPException(status_code=400, detail="No tickers to forecast.")

    job = jobs.registry.submit(
        "kronos",
        lambda ctx: kronos_api.run_forecast(
            ctx, tickers, draws=req.draws, sample_count=req.sampleCount),
        params={"tickers": tickers, "draws": req.draws,
                "sampleCount": req.sampleCount},
    )
    return job.as_dict()


@app.post("/api/kronos/montecarlo")
async def kronos_montecarlo(req: MonteCarloRequest):
    existing = jobs.registry.running("kronos-mc")
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A Monte Carlo run is already in progress "
                   f"(job {existing[0].id}).")
    job = jobs.registry.submit(
        "kronos-mc",
        lambda ctx: kronos_api.monte_carlo(ctx, req.ticker, paths=req.paths),
        params={"ticker": req.ticker.upper(), "paths": req.paths},
    )
    return job.as_dict()


@app.get("/api/kronos/latest")
async def kronos_latest(kind: str = "kronos"):
    """The most recent completed run, so reopening the page is free."""
    job = jobs.registry.latest(kind)
    running = jobs.registry.running(kind)
    return {
        "job": job.as_dict() if job else None,
        "running": running[0].as_dict(include_result=False) if running else None,
    }


# -------------------------------------------------------------------- jobs

@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, log: bool = True):
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"No job {job_id}. Finished jobs are kept for an hour.")
    return job.as_dict(include_log=log)


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    if not jobs.registry.cancel(job_id):
        raise HTTPException(
            status_code=400, detail="That job is not running.")
    return {"cancelled": job_id}


@app.get("/api/jobs")
async def job_list(kind: str | None = None, limit: int = 20):
    return {
        "jobs": [j.as_dict(include_log=False, include_result=False)
                 for j in jobs.registry.list(kind, limit)]
    }


# --------------------------------------------------------------- backtests

@app.get("/api/backtests")
async def backtests():
    """Recorded backtest results.

    Computed results and quoted findings are returned as separate keys, and
    every quoted finding carries its source. Nothing here re-runs anything.
    """
    return {
        "results": backtests_api.load_results(),
        "findings": backtests_api.RECORDED_FINDINGS,
    }


@app.get("/api/backtests/report")
async def backtests_report():
    md = backtests_api.report_markdown()
    if md is None:
        raise HTTPException(status_code=404, detail="No backtest_report.md.")
    return {"markdown": md}


@app.get("/api/health")
async def health():
    """Liveness for the launcher script — never touches the venue."""
    return {"ok": True}
