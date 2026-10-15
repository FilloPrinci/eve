"""Qt side of the background job system: thread-safe notifications and the job list panel."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.core.jobs import Job, JobContext, JobManager, JobState

SuccessCallback = Callable[[Any], None]
ErrorCallback = Callable[[str], None]


class JobRunner(QObject):
    """Runs functions on worker threads and calls back on the UI thread."""

    jobChanged = Signal(object)
    """Emitted on the UI thread with the `Job` whenever its state or progress changes."""
    _changed = Signal(object)

    def __init__(self, manager: JobManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._callbacks: dict[str, tuple[SuccessCallback | None, ErrorCallback | None]] = {}
        self._changed.connect(self._on_changed, Qt.ConnectionType.QueuedConnection)
        manager.subscribe(self._changed.emit)

    def run(
        self,
        title: str,
        function: Callable[[JobContext], Any],
        on_success: SuccessCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> Job:
        job = self.manager.submit(title, function)
        self._callbacks[job.id] = (on_success, on_error)
        return job

    def cancel(self, job_id: str) -> None:
        self.manager.cancel(job_id)

    @property
    def busy(self) -> bool:
        return any(not job.state.finished for job in self.manager.jobs)

    def _on_changed(self, job: Job) -> None:
        self.jobChanged.emit(job)
        if not job.state.finished or job.id not in self._callbacks:
            return
        on_success, on_error = self._callbacks.pop(job.id)
        if job.state is JobState.SUCCEEDED and on_success:
            on_success(job.result)
        elif job.state is JobState.FAILED and on_error:
            on_error(job.error or "Unknown error")


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d} left" if seconds >= 60 else f"{seconds} s left"


class JobRow(QWidget):
    def __init__(self, job: Job, runner: JobRunner) -> None:
        super().__init__()
        self.job_id = job.id
        self.title = QLabel(job.title)
        self.status = QLabel()
        self.bar = QProgressBar()
        self.bar.setMaximumHeight(14)
        self.cancel = QPushButton(self.tr("Cancel"))
        self.cancel.clicked.connect(lambda: runner.cancel(self.job_id))
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.status)
        top.addWidget(self.cancel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addLayout(top)
        layout.addWidget(self.bar)
        self.update_from(job)

    def update_from(self, job: Job) -> None:
        if job.fraction is None:
            self.bar.setRange(0, 0)  # indeterminate: activity indicator
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(round(job.fraction * 1000))
        labels = {
            JobState.PENDING: self.tr("Waiting"),
            JobState.SUCCEEDED: self.tr("Done"),
            JobState.FAILED: self.tr("Failed: {error}").replace("{error}", job.error or ""),
            JobState.CANCELLED: self.tr("Cancelled"),
        }
        text = labels.get(
            job.state, " ".join(filter(None, [job.message, format_eta(job.eta_seconds)]))
        )
        self.status.setText(text)
        self.cancel.setEnabled(not job.state.finished)
        if job.state.finished:
            self.bar.setRange(0, 1000)
            self.bar.setValue(1000 if job.state is JobState.SUCCEEDED else 0)


class JobsPanel(QWidget):
    """List of background jobs with progress, ETA and cancel buttons."""

    def __init__(self, runner: JobRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.list = QListWidget()
        clear = QPushButton(self.tr("Clear finished"))
        clear.clicked.connect(self.clear_finished)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addWidget(clear)
        self._rows: dict[str, tuple[QListWidgetItem, JobRow]] = {}
        runner.jobChanged.connect(self.on_job_changed)

    def on_job_changed(self, job: Job) -> None:
        if job.id not in self._rows:
            item = QListWidgetItem()
            row = JobRow(job, self.runner)
            item.setSizeHint(row.sizeHint())
            self.list.insertItem(0, item)
            self.list.setItemWidget(item, row)
            self._rows[job.id] = (item, row)
        self._rows[job.id][1].update_from(job)

    def clear_finished(self) -> None:
        self.runner.manager.clear_finished()
        for job_id, (item, row) in list(self._rows.items()):
            if not row.cancel.isEnabled():
                self.list.takeItem(self.list.row(item))
                del self._rows[job_id]
