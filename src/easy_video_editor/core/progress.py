"""Progress reporting and cancellation for long operations (SPEC §7.1).

Layers below the UI never import Qt: long operations take a `Progress` callback and a
`CancellationToken`, and the UI adapts them to its own widgets.
"""

from __future__ import annotations

import threading
from typing import Protocol

from easy_video_editor.core.errors import EveError


class CancelledError(EveError):
    """The operation was cancelled by the user."""

    def __init__(self, message: str = "Cancelled.") -> None:
        super().__init__(message)


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError()

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds; return True as soon as the token is cancelled."""
        return self._event.wait(timeout)


class Progress(Protocol):
    def __call__(self, fraction: float | None, message: str = "") -> None:
        """Report progress in ``[0, 1]`` (None when unknown) with an optional English message."""


def no_progress(fraction: float | None, message: str = "") -> None:
    """A `Progress` that ignores reports."""


def sub_progress(progress: Progress, start: float, end: float) -> Progress:
    """Map ``[0, 1]`` of a sub-task onto ``[start, end]`` of ``progress``."""

    def report(fraction: float | None, message: str = "") -> None:
        mapped = None if fraction is None else start + (end - start) * min(max(fraction, 0.0), 1.0)
        progress(mapped, message)

    return report
