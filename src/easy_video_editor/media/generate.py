"""Generating proxies, thumbnail strips and waveforms with ffmpeg.

Every function writes to a temporary file in the destination folder and renames it on success,
so an interrupted or cancelled run never leaves a truncated file under the final name.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
import numpy.typing as npt

from easy_video_editor.core.model import Clip
from easy_video_editor.core.progress import CancellationToken, Progress, no_progress
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.process import FfmpegProgress, run

FALLBACK_PROXY_FPS = Fraction(30)
MAX_PROXY_FPS = Fraction(120)
THUMB_HEIGHT = 90
THUMB_SECONDS = 2
"""One thumbnail every this many seconds, within the limits below."""
MIN_THUMBS, MAX_THUMBS = 8, 120
WAVEFORM_SAMPLE_RATE = 8000
WAVEFORM_PEAKS_PER_SECOND = 100


def _temp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.stem}.partial{path.suffix}")


def _finish(temp: Path, final: Path) -> None:
    os.replace(temp, final)


def _cleanup(temp: Path) -> None:
    temp.unlink(missing_ok=True)


def proxy_frame_rate(clip: Clip) -> Fraction:
    rate = clip.frame_rate.as_fraction() if clip.frame_rate else None
    if rate is None or rate <= 0 or rate > MAX_PROXY_FPS:
        return FALLBACK_PROXY_FPS
    return rate


def proxy_command(clip: Clip, output: Path, toolchain: Toolchain, height: int) -> list[str | Path]:
    """Low-resolution, constant-frame-rate H.264 proxy with a keyframe every second."""
    rate = proxy_frame_rate(clip)
    target_height = min(height, clip.height or height)
    target_height -= target_height % 2
    args: list[str | Path] = [
        toolchain.ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", clip.path,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:{target_height},fps={rate.numerator}/{rate.denominator}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-g", str(max(1, round(rate))),
    ]  # fmt: skip
    if clip.has_audio:
        args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", output]
    return args


def make_proxy(
    clip: Clip,
    output: Path,
    toolchain: Toolchain,
    height: int,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
) -> Path:
    temp = _temp_sibling(output)
    tracker = FfmpegProgress(clip.duration_us, lambda f: progress(f, "Generating proxy"))
    try:
        run(proxy_command(clip, temp, toolchain, height), token=token, on_line=tracker.feed)
        _finish(temp, output)
    finally:
        _cleanup(temp)
    return output


@dataclass(frozen=True)
class StripInfo:
    count: int
    thumb_width: int
    thumb_height: int
    interval_us: int
    """Thumbnail ``i`` shows the frame at ``i * interval_us``."""

    @classmethod
    def load(cls, path: Path) -> StripInfo:
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def strip_layout(clip: Clip) -> StripInfo:
    count = min(MAX_THUMBS, max(MIN_THUMBS, clip.duration_us // (THUMB_SECONDS * 1_000_000)))
    if clip.width and clip.height:
        width = 2 * max(1, round(THUMB_HEIGHT * clip.width / clip.height / 2))
    else:
        width = 160
    return StripInfo(int(count), width, THUMB_HEIGHT, clip.duration_us // count)


def make_thumbnail_strip(
    clip: Clip,
    source: Path,
    output: Path,
    info_output: Path,
    toolchain: Toolchain,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
) -> StripInfo:
    """One JPEG with evenly spaced thumbnails side by side, plus a JSON file describing it."""
    info = strip_layout(clip)
    rate = Fraction(1_000_000, info.interval_us).limit_denominator(1_000_000)
    temp = _temp_sibling(output)
    tracker = FfmpegProgress(clip.duration_us, lambda f: progress(f, "Generating thumbnails"))
    try:
        run(
            [
                toolchain.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                source,
                "-vf",
                f"fps={rate.numerator}/{rate.denominator}:round=up,"
                f"scale={info.thumb_width}:{info.thumb_height},tile={info.count}x1",
                "-frames:v",
                "1",
                "-q:v",
                "4",
                "-update",
                "1",
                "-progress",
                "pipe:1",
                "-nostats",
                "-f",
                "image2",
                temp,
            ],
            token=token,
            on_line=tracker.feed,
        )
        _finish(temp, output)
    finally:
        _cleanup(temp)
    info_output.write_text(json.dumps(asdict(info)), encoding="utf-8")
    return info


class PeakAccumulator:
    """Turns a stream of float32 mono samples into (min, max) pairs per bucket."""

    def __init__(self, samples_per_bucket: int) -> None:
        self._per_bucket = samples_per_bucket
        self._pending = b""
        self._carry: npt.NDArray[np.float32] = np.empty(0, dtype=np.float32)
        self._peaks: list[npt.NDArray[np.float32]] = []
        self.samples = 0

    def feed(self, chunk: bytes) -> None:
        data = self._pending + chunk
        usable = len(data) - len(data) % 4
        self._pending = data[usable:]
        samples = np.frombuffer(data[:usable], dtype="<f4")
        self.samples += samples.size
        samples = np.concatenate([self._carry, samples])
        full = samples.size - samples.size % self._per_bucket
        self._carry = samples[full:].copy()
        if full:
            self._peaks.append(self._reduce(samples[:full].reshape(-1, self._per_bucket)))

    def result(self) -> npt.NDArray[np.float32]:
        """Array of shape (buckets, 2) holding the minimum and maximum of each bucket."""
        parts = list(self._peaks)
        if self._carry.size:
            parts.append(self._reduce(self._carry.reshape(1, -1)))
        if not parts:
            return np.zeros((0, 2), dtype=np.float32)
        return np.concatenate(parts).astype(np.float32)

    @staticmethod
    def _reduce(frames: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        return np.stack([frames.min(axis=1), frames.max(axis=1)], axis=1)


def make_waveform(
    clip: Clip,
    source: Path,
    output: Path,
    toolchain: Toolchain,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
) -> Path:
    """Save peaks (``WAVEFORM_PEAKS_PER_SECOND`` (min, max) pairs per second) as a .npy file."""
    accumulator = PeakAccumulator(WAVEFORM_SAMPLE_RATE // WAVEFORM_PEAKS_PER_SECOND)
    expected = max(1, clip.duration_us * WAVEFORM_SAMPLE_RATE // 1_000_000)

    def report(chunk: bytes) -> None:
        accumulator.feed(chunk)
        progress(min(accumulator.samples / expected, 1.0), "Generating waveform")

    run(
        [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(WAVEFORM_SAMPLE_RATE),
            "-f",
            "f32le",
            "pipe:1",
        ],
        token=token,
        on_chunk=report,
    )
    temp = _temp_sibling(output)
    try:
        with temp.open("wb") as handle:
            np.save(handle, accumulator.result())
        _finish(temp, output)
    finally:
        _cleanup(temp)
    return output
