"""Background jobs with progress, ETA and cancellation (SPEC §8.2).

`JobManager` runs callables on worker threads. Listeners are called from those threads:
the UI must forward notifications to its own thread (e.g. with a queued Qt signal).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from easy_video_editor.core.ids import new_id
from easy_video_editor.core.progress import CancellationToken, CancelledError

log = logging.getLogger(__name__)

ETA_MIN_FRACTION = 0.02
"""ETA is only estimated once this much work is done, to avoid wild guesses."""


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


@dataclass
class Job:
    id: str
    title: str
    state: JobState = JobState.PENDING
    fraction: float | None = None
    message: str = ""
    error: str | None = None
    result: Any = None
    started_at: float | None = None
    finished_at: float | None = None
    token: CancellationToken = field(default_factory=CancellationToken, repr=False)
    future: Future[None] | None = field(default=None, repr=False)

    @property
    def eta_seconds(self) -> float | None:
        if self.state is not JobState.RUNNING or self.started_at is None:
            return None
        if self.fraction is None or self.fraction < ETA_MIN_FRACTION:
            return None
        elapsed = time.monotonic() - self.started_at
        return elapsed / self.fraction * (1.0 - self.fraction)


@dataclass(frozen=True)
class JobContext:
    """What a job function receives: a cancellation token and a progress callback."""

    job: Job
    _report: Callable[[Job, float | None, str], None]

    @property
    def token(self) -> CancellationToken:
        return self.job.token

    def progress(self, fraction: float | None, message: str = "") -> None:
        self._report(self.job, fraction, message)


JobFunction = Callable[[JobContext], Any]
JobListener = Callable[[Job], None]


class JobManager:
    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="eve-job")
        self._jobs: dict[str, Job] = {}
        self._listeners: list[JobListener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: JobListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @property
    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def submit(self, title: str, function: JobFunction) -> Job:
        job = Job(id=new_id("job"), title=title)
        with self._lock:
            self._jobs[job.id] = job
        self._notify(job)
        job.future = self._executor.submit(self._run, job, function)
        return job

    def cancel(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.token.cancel()
        if job.future is not None and job.future.cancel():
            self._finish(job, JobState.CANCELLED)

    def wait(self, job: Job, timeout: float | None = None) -> Job:
        if job.future is not None and not job.future.cancelled():
            job.future.result(timeout)
        return job

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = {key: job for key, job in self._jobs.items() if not job.state.finished}

    def shutdown(self, cancel: bool = True) -> None:
        if cancel:
            for job in self.jobs:
                job.token.cancel()
        self._executor.shutdown(wait=True, cancel_futures=cancel)

    def _run(self, job: Job, function: JobFunction) -> None:
        if job.token.cancelled:
            self._finish(job, JobState.CANCELLED)
            return
        job.state = JobState.RUNNING
        job.started_at = time.monotonic()
        self._notify(job)
        try:
            job.result = function(JobContext(job, self._report))
        except CancelledError:
            self._finish(job, JobState.CANCELLED)
        except Exception as error:
            log.exception("Job %r failed", job.title)
            job.error = str(error) or type(error).__name__
            self._finish(job, JobState.FAILED)
        else:
            job.fraction = 1.0
            self._finish(job, JobState.SUCCEEDED)

    def _report(self, job: Job, fraction: float | None, message: str) -> None:
        job.fraction = fraction
        if message:
            job.message = message
        self._notify(job)

    def _finish(self, job: Job, state: JobState) -> None:
        job.state = state
        job.finished_at = time.monotonic()
        self._notify(job)

    def _notify(self, job: Job) -> None:
        for listener in list(self._listeners):
            try:
                listener(job)
            except Exception:
                log.exception("Job listener failed")
