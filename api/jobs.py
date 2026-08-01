"""
jobs.py — background jobs with streamed progress.

Kronos inference takes minutes for a 14-ticker watchlist, and a rebalance
proposal has to run that same signal before it can propose anything. Neither
fits in a request, so both run here: submitted, given an id, and polled or
streamed while they work.

Two rules this module exists to enforce:

  1. A job that has not finished has NO result. Callers get `status:
     "running"` and a log, never a partial result set that looks like an
     answer. Half a ranking is not a ranking.
  2. A failed job keeps its log. The most useful thing about a Kronos run
     that died is the output up to the point it died, and discarding it to
     return a clean error message would be the wrong trade.

Jobs run in a thread pool because the work is blocking CPU (torch inference,
yfinance downloads) and would otherwise stall the event loop that keeps the
IB connection alive.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("api.jobs")

MAX_LOG_LINES = 500
JOB_RETENTION = 60 * 60          # keep finished jobs for an hour
MAX_WORKERS = 2                  # torch inference is heavy; don't stack runs


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"       # queued | running | done | failed | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: float = 0.0        # 0..1, best effort
    message: str = ""
    result: Any = None
    error: str | None = None
    traceback: str | None = None
    params: dict = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    _cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return self.status in ("queued", "running")

    def as_dict(self, include_log: bool = True, include_result: bool = True) -> dict:
        out = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "elapsedSeconds": round(
                (self.finished_at or time.time()) - (self.started_at or self.created_at), 1
            ),
            "progress": round(self.progress, 3),
            "message": self.message,
            "params": self.params,
            "error": self.error,
        }
        if include_log:
            out["log"] = list(self.log)
        # A running job has no result, and must not appear to have one.
        out["result"] = self.result if (include_result and self.status == "done") else None
        return out


class JobContext:
    """Handed to the job function so it can report progress and log."""

    def __init__(self, job: Job):
        self._job = job

    def log(self, line: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')}  {line}"
        self._job.log.append(stamped)
        log.info("[%s] %s", self._job.kind, line)

    def progress(self, fraction: float, message: str = "") -> None:
        self._job.progress = max(0.0, min(1.0, fraction))
        if message:
            self._job.message = message
            self.log(message)

    @property
    def cancelled(self) -> bool:
        return self._job._cancel.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled("Cancelled by the user.")


class JobCancelled(RuntimeError):
    pass


class JobRegistry:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_WORKERS, thread_name_prefix="job")
        self._lock = threading.Lock()
        # Newest completed job per kind — what "show me the last forecast"
        # reads, so reopening the page doesn't mean re-running the model.
        self._latest: dict[str, str] = {}

    def submit(self, kind: str, fn: Callable[[JobContext], Any],
               params: dict | None = None) -> Job:
        self._sweep()
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params or {})
        with self._lock:
            self._jobs[job.id] = job

        def runner():
            job.status = "running"
            job.started_at = time.time()
            ctx = JobContext(job)
            try:
                job.result = fn(ctx)
                job.status = "done"
                job.progress = 1.0
                job.message = "Finished."
                with self._lock:
                    self._latest[kind] = job.id
            except JobCancelled as exc:
                job.status = "cancelled"
                job.error = str(exc)
                ctx.log(f"CANCELLED: {exc}")
            except Exception as exc:                    # noqa: BLE001
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.traceback = traceback.format_exc()
                # Keep the log — the output up to the failure is the useful part.
                ctx.log(f"FAILED: {job.error}")
                log.exception("Job %s (%s) failed", job.id, kind)
            finally:
                job.finished_at = time.time()

        self._executor.submit(runner)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def latest(self, kind: str) -> Job | None:
        job_id = self._latest.get(kind)
        return self._jobs.get(job_id) if job_id else None

    def running(self, kind: str | None = None) -> list[Job]:
        return [
            j for j in self._jobs.values()
            if j.running and (kind is None or j.kind == kind)
        ]

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or not job.running:
            return False
        job._cancel.set()
        job.message = "Cancelling…"
        return True

    def list(self, kind: str | None = None, limit: int = 20) -> list[Job]:
        jobs = [j for j in self._jobs.values() if kind is None or j.kind == kind]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def _sweep(self) -> None:
        """Drop old finished jobs, but never the newest per kind."""
        cutoff = time.time() - JOB_RETENTION
        keep = set(self._latest.values())
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.running or job_id in keep:
                    continue
                if (job.finished_at or job.created_at) < cutoff:
                    self._jobs.pop(job_id, None)

    async def wait(self, job_id: str, timeout: float = 600) -> Job:
        """Await completion — used by callers that genuinely need the result."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if not job.running:
                return job
            await asyncio.sleep(0.4)
        raise asyncio.TimeoutError(f"Job {job_id} did not finish within {timeout}s")

    def shutdown(self) -> None:
        for job in self._jobs.values():
            if job.running:
                job._cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)


registry = JobRegistry()
