"""
rebalance.py — the approve-in-the-browser flow.

This replaces `paper_trader.py`'s terminal `y/N` prompt, and the design goal
is that it replaces ONLY that. Everything else — signal, sizing, RiskGuard,
exits-before-entries, journalling, the Telegram summary — is the same
`execute_rebalance` call the terminal path makes, via the `approve_fn`
callback added there for this purpose.

That matters more than it might look. The property worth preserving is that
the proposal you approve and the orders that get placed come from one
`buy_plan`, computed once. A version that computed a proposal in one request
and re-derived the orders in a second would let prices move in between, and
the thing approved would not be the thing sent.

So the run is one long-lived call that PAUSES mid-flight:

    start -> signal runs -> proposal built -> approve_fn blocks
          -> browser shows the proposal
          -> approve/decline -> approve_fn returns -> orders placed

Because it blocks a worker thread, it times out. A forgotten browser tab must
not hold the trading worker forever, and a rebalance that has been sitting
unapproved for ten minutes is priced off stale data anyway — so the timeout
declines rather than proceeding. Declining is the reversible direction.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "KronosAI"))

log = logging.getLogger("api.rebalance")

# How long a built proposal waits for a human before auto-declining.
APPROVAL_TIMEOUT = 600.0


class RebalanceError(RuntimeError):
    """User-facing rebalance problem."""


@dataclass
class PendingApproval:
    """A proposal waiting on a decision, and the gate the worker is parked on."""
    job_id: str
    proposal: dict
    created_at: float = field(default_factory=time.time)
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    decided_by: str | None = None

    @property
    def expires_in(self) -> float:
        return max(0.0, APPROVAL_TIMEOUT - (time.time() - self.created_at))

    def as_dict(self) -> dict:
        return {
            "jobId": self.job_id,
            "createdAt": self.created_at,
            "expiresInSeconds": round(self.expires_in),
            "decided": self.event.is_set(),
            "approved": self.approved,
            "decidedBy": self.decided_by,
            **self.proposal,
        }


_pending: dict[str, PendingApproval] = {}


def get_pending(job_id: str | None = None) -> PendingApproval | None:
    if job_id:
        return _pending.get(job_id)
    live = [p for p in _pending.values() if not p.event.is_set()]
    live.sort(key=lambda p: p.created_at, reverse=True)
    return live[0] if live else None


def decide(job_id: str, approved: bool, who: str = "web") -> PendingApproval:
    pending = _pending.get(job_id)
    if pending is None:
        raise RebalanceError(
            f"No rebalance is waiting for approval under job {job_id}. It may "
            "have timed out, or the API restarted."
        )
    if pending.event.is_set():
        raise RebalanceError(
            f"That rebalance was already "
            f"{'approved' if pending.approved else 'declined'}."
        )
    pending.approved = approved
    pending.decided_by = who
    pending.event.set()
    return pending


def ibs_market_open() -> bool:
    """NYSE hours per ibkr_service's own zoneinfo check, not host time."""
    import ibkr_service as ibs
    return ibs.market_is_open()


def _jsonable(value):
    """Proposal dicts carry numpy scalars from pandas — make them plain."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:                               # noqa: BLE001
            return str(value)
    return value


def run_rebalance(ctx, ib, dry_run: bool = False) -> dict:
    """Compute the signal, propose, wait for approval, execute.

    Runs on the trader worker thread (synchronous ibkr_service code) inside a
    job (for the streamed log). `ib` is the worker's own connection.
    """
    import paper_trader
    import signal_policy as sp
    import trader_app as ta

    settings = ta.load_settings()
    top_n = settings.get("momentum_top_n", 3)

    # signal_policy is the single source of truth for which signal may run
    # (rule 8). Resolved and checked BEFORE any fetching, and reported as a
    # refusal rather than a traceback — a disabled signal is an expected
    # answer, not a crash. `allow_momentum` is never passed from here; the
    # owner opts in per-session on the command line, not through a browser.
    signal = sp.resolve_signal(settings, requested=None)
    try:
        sp.assert_allowed(signal, False, context="web UI rebalance")
    except sp.SignalDisabled as exc:
        raise RebalanceError(str(exc)) from exc

    ctx.progress(0.05, f"Computing the {signal} signal…")

    # Mirrors paper_trader.main()'s dispatch. Deliberately does NOT fall back
    # to another signal if this one is unavailable — autotrade_runner refuses
    # to fire rather than substituting, and this path holds the same line.
    try:
        if signal == "kronos":
            import kronos_agent as ka
            ctx.log("Kronos forecast from fresh data. Measured IC ~0 — this is "
                    "a research direction, not a validated edge.")
            top, data, ranked = ka.forecast_signal(settings)
            rank_label = f"predicted {ka.PRED_LEN}-trading-day return"
        else:
            ctx.log("Momentum signal (owner opt-in).")
            top, data, ranked = paper_trader.compute_signal(
                settings, allow_momentum=False)
            rank_label = (f"trailing {settings.get('momentum_lookback_m', 12)}"
                          "-mo return")
    except sp.SignalDisabled as exc:
        raise RebalanceError(str(exc)) from exc
    except Exception as exc:                            # noqa: BLE001
        raise RebalanceError(f"Signal failed: {exc}") from exc

    label = signal
    ctx.log(f"Signal '{label}' selected top-{top_n}: {', '.join(top) or 'none'}")

    ranking = [
        {"ticker": t, "value": float(ranked[t] * 100), "inTop": t in top}
        for t in ranked.index
    ]

    # Market-closed is a warning, not a block: queueing into the next session
    # is a legitimate choice, and paper_trader treats it the same way. It goes
    # in the proposal so the approver sees it before deciding, rather than
    # discovering it from an unfilled bracket afterwards.
    market_open = ibs_market_open()
    if not market_open:
        ctx.log("NOTE: the US market is closed. Orders will sit unfilled until "
                "the next session, and entry limits priced off today's close "
                "may be stale by then.")

    ctx.progress(0.55, "Building the proposal…")

    job_id = ctx._job.id  # noqa: SLF001 - the job owns its own id
    captured: dict = {}

    def approve_fn(proposal: dict) -> bool:
        """Park here until the browser decides. Returns True to proceed."""
        enriched = _jsonable(proposal)
        enriched["ranking"] = ranking
        enriched["rankLabel"] = rank_label
        enriched["marketOpen"] = market_open
        pending = PendingApproval(job_id=job_id, proposal=enriched)
        _pending[job_id] = pending
        captured["proposal"] = pending.proposal
        ctx.progress(
            0.7,
            f"Awaiting approval: {len(proposal['sells'])} sell(s), "
            f"{len(proposal['buys'])} buy(s)"
        )
        ctx.log(f"Proposal ready — waiting up to {APPROVAL_TIMEOUT:.0f}s "
                "for a decision.")

        decided = pending.event.wait(timeout=APPROVAL_TIMEOUT)
        if not decided:
            # Timing out DECLINES. A stale proposal priced minutes ago is not
            # something to execute unattended, and declining is the direction
            # that can be undone by running again.
            pending.approved = False
            pending.decided_by = "timeout"
            pending.event.set()
            ctx.log("No decision within the timeout — DECLINED. Nothing placed.")
            return False

        ctx.log(
            f"{'APPROVED' if pending.approved else 'DECLINED'} "
            f"by {pending.decided_by}."
        )
        if pending.approved:
            ctx.progress(0.8, "Approved — placing orders…")
        return pending.approved

    try:
        attempted = paper_trader.execute_rebalance(
            ib, settings, top, data, top_n, label,
            auto_approve=False, dry_run=dry_run,
            approve_fn=None if dry_run else approve_fn,
        )
    finally:
        pending = _pending.get(job_id)
        if pending and not pending.event.is_set():
            pending.event.set()

    ctx.progress(1.0, "Done.")
    result = {
        "signal": label,
        "topN": top_n,
        "top": list(top),
        "ranking": ranking,
        "rankLabel": rank_label,
        "marketOpen": market_open,
        "dryRun": dry_run,
        "attempted": bool(attempted),
        "proposal": captured.get("proposal"),
    }
    pending = _pending.get(job_id)
    if pending:
        result["approved"] = pending.approved
        result["decidedBy"] = pending.decided_by
    return result
