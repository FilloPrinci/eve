"""Integration tests run real ffmpeg on tiny clips generated at test time (SPEC §15)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from easy_video_editor.media.binaries import Toolchain

if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
    pytest.skip("ffmpeg/ffprobe not installed", allow_module_level=True)


def make_clip(path: Path, seconds: float, size: str = "320x240", audio: bool = True) -> Path:
    """Synthetic clip: testsrc2 (with a visible timecode and frame counter) and a 440 Hz tone."""
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate=25:duration={seconds}"]  # fmt: skip
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
        args += ["-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture(scope="session")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("media")
    make_clip(directory / "clip.mp4", 4)
    make_clip(directory / "silent.mp4", 2, audio=False)
    make_clip(directory / "long.mp4", 20, size="640x480")
    return directory
