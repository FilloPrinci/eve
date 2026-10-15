"""Running external processes (SPEC §7.7, §12).

Commands are always argument lists (never ``shell=True``). The full command line, exit code
and duration are logged; stdout/stderr are logged at debug level. Processes are terminated when
the cancellation token fires.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from easy_video_editor.core.progress import CancellationToken, CancelledError
from easy_video_editor.media.errors import ProcessError

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.1
TERMINATE_GRACE_S = 5.0
CHUNK_SIZE = 1 << 16
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class ProcessResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


def format_command(args: Sequence[str]) -> str:
    return shlex.join(args)


def run(
    args: Sequence[str | Path],
    *,
    token: CancellationToken | None = None,
    on_line: Callable[[str], None] | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
    check: bool = True,
) -> ProcessResult:
    """Run a process to completion.

    stdout is captured as text and passed line by line to ``on_line``, unless ``on_chunk`` is
    given: then stdout is read as raw bytes and passed to ``on_chunk`` (and not captured).
    Raises `CancelledError` if ``token`` is cancelled, `ProcessError` on a non-zero exit code
    when ``check`` is true.
    """
    command = [str(arg) for arg in args]
    log.info("Running: %s", format_command(command))
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    readers = [
        threading.Thread(
            target=_read_stdout, args=(process.stdout, stdout_lines, on_line, on_chunk), daemon=True
        ),
        threading.Thread(target=_read_text, args=(process.stderr, stderr_lines, None), daemon=True),
    ]
    for reader in readers:
        reader.start()

    cancelled = False
    while process.poll() is None:
        if token is not None and token.wait(POLL_INTERVAL_S):
            cancelled = True
            _stop(process)
            break
        if token is None:
            time.sleep(POLL_INTERVAL_S)
    returncode = process.wait()
    for reader in readers:
        reader.join()

    duration = time.monotonic() - started
    result = ProcessResult(
        command, returncode, "".join(stdout_lines), "".join(stderr_lines), duration
    )
    log.info("Exit code %d after %.2f s: %s", returncode, duration, command[0])
    if result.stdout:
        log.debug("stdout of %s:\n%s", command[0], result.stdout.rstrip())
    if result.stderr:
        log.debug("stderr of %s:\n%s", command[0], result.stderr.rstrip())

    if cancelled:
        raise CancelledError()
    if check and returncode != 0:
        last_line = result.stderr.strip().splitlines()[-1:] or ["no error output"]
        raise ProcessError(
            f"{Path(command[0]).name} failed (exit code {returncode}): {last_line[0]}",
            returncode,
            result.stderr,
        )
    return result


def _read_stdout(
    stream: IO[bytes],
    lines: list[str],
    on_line: Callable[[str], None] | None,
    on_chunk: Callable[[bytes], None] | None,
) -> None:
    if on_chunk is None:
        _read_text(stream, lines, on_line)
        return
    while chunk := stream.read(CHUNK_SIZE):
        on_chunk(chunk)


def _read_text(stream: IO[bytes], lines: list[str], on_line: Callable[[str], None] | None) -> None:
    for raw in stream:
        line = raw.decode("utf-8", errors="replace")
        lines.append(line)
        if on_line is not None:
            on_line(line.rstrip("\r\n"))


def _stop(process: subprocess.Popen[bytes]) -> None:
    log.info("Cancelling process %d", process.pid)
    process.terminate()
    try:
        process.wait(TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()


class FfmpegProgress:
    """Parses ``ffmpeg -progress pipe:1`` output into a fraction of ``duration_us``."""

    def __init__(self, duration_us: int, report: Callable[[float], None]) -> None:
        self._duration_us = max(duration_us, 1)
        self._report = report

    def feed(self, line: str) -> None:
        key, _, value = line.partition("=")
        if key == "out_time_us" and value.strip().lstrip("-").isdigit():
            self._report(min(max(int(value) / self._duration_us, 0.0), 1.0))
        elif key == "progress" and value.strip() == "end":
            self._report(1.0)
