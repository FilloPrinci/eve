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


CODED_FPS = 25
REFERENCE_LEVEL = 208
"""Luma of the reference band: frames where it differs (fades, blends) are not decoded."""


def make_coded_clip(path: Path, seconds: int = 12) -> Path:
    """Each frame encodes its own index in grey bands (top to bottom): a constant reference band,
    then the index in base 8 (three digits, level = digit * 32 + 16). Lossless, 320x180, 25 fps,
    with a 440 Hz tone."""
    digits = (
        f"if(lt(Y,H/4),{REFERENCE_LEVEL},"
        "if(lt(Y,H/2),floor(N/64)*32+16,"
        "if(lt(Y,3*H/4),mod(floor(N/8),8)*32+16,mod(N,8)*32+16)))"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=s=320x180:r={CODED_FPS}:d={seconds},format=yuv420p,"
         f"geq=lum='{digits}':cb=128:cr=128",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def decode_frame_indexes(path: Path) -> list[int | None]:
    """Source frame index of every frame of a rendered video (None where it cannot be read)."""
    import json

    import numpy as np

    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        ).stdout
    )["streams"][0]  # fmt: skip
    width, height = info["width"], info["height"]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    frames = np.frombuffer(raw, np.uint8).reshape(-1, height, width)

    def level(frame: "np.ndarray", band: int) -> float:
        y = (2 * band + 1) * height // 8
        full_range = float(frame[y - 1 : y + 2, 2:6].mean())
        return full_range * 219 / 255 + 16  # gray output is full range; the clip is limited

    indexes: list[int | None] = []
    for frame in frames:
        if abs(level(frame, 0) - REFERENCE_LEVEL) > 6:
            indexes.append(None)
            continue
        number = 0
        for band in (1, 2, 3):
            value = level(frame, band)
            digit = round((value - 16) / 32)
            if not 0 <= digit <= 7 or abs(value - (digit * 32 + 16)) > 6:
                number = -1
                break
            number = number * 8 + digit
        indexes.append(None if number < 0 else number)
    return indexes


@pytest.fixture(scope="session")
def toolchain() -> Toolchain:
    return Toolchain.discover()


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("media")
    make_clip(directory / "clip.mp4", 4)
    make_clip(directory / "silent.mp4", 2, audio=False)
    make_clip(directory / "long.mp4", 20, size="640x480")
    make_coded_clip(directory / "coded.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=220:sample_rate=48000:duration=20", str(directory / "music.mp3")],
        check=True,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x32",
         "-frames:v", "1", str(directory / "logo.png")],
        check=True,
    )  # fmt: skip
    return directory
