import threading
from collections.abc import Iterator

import pytest

from easy_video_editor.core.jobs import Job, JobContext, JobManager, JobState
from easy_video_editor.core.progress import CancellationToken, CancelledError, sub_progress


@pytest.fixture
def manager() -> Iterator[JobManager]:
    jobs = JobManager(max_workers=1)
    yield jobs
    jobs.shutdown()


def test_token() -> None:
    token = CancellationToken()
    token.raise_if_cancelled()
    token.cancel()
    assert token.cancelled
    with pytest.raises(CancelledError):
        token.raise_if_cancelled()


def test_sub_progress_maps_and_clamps() -> None:
    seen: list[float | None] = []
    report = sub_progress(lambda fraction, message="": seen.append(fraction), 0.5, 1.0)
    report(0.0)
    report(0.5)
    report(2.0)
    report(None)
    assert seen == [0.5, 0.75, 1.0, None]


def test_successful_job_reports_progress(manager: JobManager) -> None:
    states: list[JobState] = []
    manager.subscribe(lambda job: states.append(job.state))

    def work(ctx: JobContext) -> str:
        ctx.progress(0.5, "Halfway")
        assert ctx.job.eta_seconds is not None
        return "done"

    job = manager.wait(manager.submit("Work", work))
    assert (job.state, job.result, job.fraction, job.message) == (
        JobState.SUCCEEDED,
        "done",
        1.0,
        "Halfway",
    )
    assert states[0] is JobState.PENDING and states[-1] is JobState.SUCCEEDED
    assert job.eta_seconds is None


def test_failed_job_keeps_error(manager: JobManager) -> None:
    def work(ctx: JobContext) -> None:
        raise ValueError("bad input")

    job = manager.wait(manager.submit("Work", work))
    assert (job.state, job.error) == (JobState.FAILED, "bad input")


def test_running_job_can_be_cancelled(manager: JobManager) -> None:
    started = threading.Event()

    def work(ctx: JobContext) -> None:
        started.set()
        while not ctx.token.wait(0.01):
            pass
        ctx.token.raise_if_cancelled()

    job = manager.submit("Long", work)
    started.wait(5)
    manager.cancel(job.id)
    assert manager.wait(job).state is JobState.CANCELLED


def test_pending_job_can_be_cancelled(manager: JobManager) -> None:
    release = threading.Event()
    blocker = manager.submit("Blocker", lambda ctx: release.wait(5))
    pending = manager.submit("Pending", lambda ctx: "never")
    manager.cancel(pending.id)
    release.set()
    manager.wait(blocker)
    assert pending.state is JobState.CANCELLED
    assert pending.result is None


def test_clear_finished(manager: JobManager) -> None:
    manager.wait(manager.submit("Quick", lambda ctx: None))
    manager.clear_finished()
    assert manager.jobs == []


def test_eta_needs_some_progress() -> None:
    job = Job(id="j", title="t", state=JobState.RUNNING, started_at=0.0, fraction=0.01)
    assert job.eta_seconds is None
