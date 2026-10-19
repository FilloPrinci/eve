import sys
import threading

import pytest

from easy_video_editor.core.progress import CancellationToken, CancelledError
from easy_video_editor.media.errors import ProcessError
from easy_video_editor.media.process import run

PYTHON = sys.executable


def test_captures_output_and_lines() -> None:
    lines: list[str] = []
    result = run(
        [PYTHON, "-c", "import sys; print('a'); print('b'); print('oops', file=sys.stderr)"],
        on_line=lines.append,
    )
    assert lines == ["a", "b"]
    assert result.returncode == 0
    assert result.stderr.strip() == "oops"


def test_binary_chunks() -> None:
    received = bytearray()
    run(
        [PYTHON, "-c", "import sys; sys.stdout.buffer.write(bytes(range(256)) * 1000)"],
        on_chunk=received.extend,
    )
    assert bytes(received) == bytes(range(256)) * 1000


def test_failure_raises_with_last_stderr_line() -> None:
    with pytest.raises(ProcessError, match=r"exit code 3.*boom") as error:
        run([PYTHON, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"])
    assert error.value.returncode == 3
    result = run([PYTHON, "-c", "import sys; sys.exit(2)"], check=False)
    assert result.returncode == 2


def test_cancellation_terminates_the_process() -> None:
    token = CancellationToken()
    timer = threading.Timer(0.3, token.cancel)
    timer.start()
    with pytest.raises(CancelledError):
        run([PYTHON, "-c", "import time; time.sleep(30)"], token=token)


def test_frozen_linux_builds_give_children_the_original_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from easy_video_editor.media.process import child_environment

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_EVE/usr/lib/eve/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    environment = child_environment({"EXTRA": "1"})
    assert environment["LD_LIBRARY_PATH"] == "/opt/lib" and environment["EXTRA"] == "1"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in child_environment()
