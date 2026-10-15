"""Exceptions raised by the media layer."""

from __future__ import annotations

from easy_video_editor.core.errors import EveError


class MediaError(EveError):
    """A media operation failed."""


class MissingBinaryError(MediaError):
    """A required external program (ffmpeg, ffprobe) was not found."""


class ProcessError(MediaError):
    """An external process exited with an error."""

    def __init__(self, message: str, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
