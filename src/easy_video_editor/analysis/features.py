"""Numeric features of a clip (SPEC §5.1): motion, scene cuts, motion centroid, audio energy and
onsets, on a fixed time grid. Computed once per clip content and cached in the project.

Frames are decoded small (64x36 grey at 10 Hz) from the proxy when available, so a full pass over
an hour of footage takes a minute or two.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from easy_video_editor.core.model import Clip
from easy_video_editor.core.progress import (
    CancellationToken,
    Progress,
    no_progress,
    sub_progress,
)
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache, content_key
from easy_video_editor.media.process import run

log = logging.getLogger(__name__)

FEATURES_VERSION = 1
HOP_US = 100_000
"""Feature resolution: 10 values per second."""
FRAME_W, FRAME_H = 64, 36
AUDIO_RATE = 22_050
HISTOGRAM_BINS = 16
CUT_THRESHOLD = 0.35
"""Histogram distance (0..1) above which a frame change counts as a scene cut."""
MOTION_PIXELS = 0.5
"""Mean pixel change (0..255) below which a frame has no meaningful motion centroid."""
SILENCE_DB = -60.0

Series = npt.NDArray[np.float32]


@dataclass
class ClipFeatures:
    duration_us: int
    motion: Series
    """Mean absolute frame difference, 0..1."""
    centroid_x: Series
    """Horizontal centre of the motion (fraction of the width); NaN without motion."""
    centroid_y: Series
    audio_energy: Series
    """RMS level mapped from -60..0 dBFS to 0..1."""
    onset: Series
    """Onset strength (impacts, hits), normalised to 0..1."""
    cuts_us: npt.NDArray[np.int64]
    hop_us: int = HOP_US

    @property
    def length(self) -> int:
        return len(self.motion)

    def save(self, path: Path) -> None:
        temp = path.with_name(f".{path.name}.partial")
        with temp.open("wb") as handle:
            np.savez_compressed(
                handle,
                version=FEATURES_VERSION,
                duration_us=self.duration_us,
                hop_us=self.hop_us,
                motion=self.motion,
                centroid_x=self.centroid_x,
                centroid_y=self.centroid_y,
                audio_energy=self.audio_energy,
                onset=self.onset,
                cuts_us=self.cuts_us,
            )
        os.replace(temp, path)

    @classmethod
    def load(cls, path: Path) -> ClipFeatures | None:
        try:
            with np.load(path) as data:
                if int(data["version"]) != FEATURES_VERSION:
                    return None
                return cls(
                    duration_us=int(data["duration_us"]),
                    hop_us=int(data["hop_us"]),
                    motion=data["motion"].astype(np.float32),
                    centroid_x=data["centroid_x"].astype(np.float32),
                    centroid_y=data["centroid_y"].astype(np.float32),
                    audio_energy=data["audio_energy"].astype(np.float32),
                    onset=data["onset"].astype(np.float32),
                    cuts_us=data["cuts_us"].astype(np.int64),
                )
        except (OSError, KeyError, ValueError) as error:
            log.warning("Ignoring unreadable features %s: %s", path, error)
            return None


def series_length(duration_us: int, hop_us: int = HOP_US) -> int:
    return max(1, math.ceil(duration_us / hop_us))


def _fit(values: npt.NDArray[np.floating], length: int, fill: float = 0.0) -> Series:
    """Cut or pad a series to ``length`` values."""
    result = np.full(length, fill, dtype=np.float32)
    count = min(length, len(values))
    result[:count] = values[:count]
    return result


# --- Video ---------------------------------------------------------------------------------------


def frames_to_features(
    frames: npt.NDArray[np.uint8], length: int, hop_us: int = HOP_US
) -> tuple[Series, Series, Series, npt.NDArray[np.int64]]:
    """Motion, motion centroid and scene cuts from small grey frames sampled every ``hop_us``."""
    if len(frames) < 2:
        empty = np.zeros(length, dtype=np.float32)
        nan = np.full(length, np.nan, dtype=np.float32)
        return empty, nan, nan.copy(), np.zeros(0, dtype=np.int64)
    as_float = frames.astype(np.float32)
    difference = np.abs(np.diff(as_float, axis=0))
    motion = np.concatenate([[0.0], difference.mean(axis=(1, 2)) / 255.0])

    height, width = frames.shape[1:]
    weights = difference.sum(axis=(1, 2))
    xs = (np.arange(width, dtype=np.float32) + 0.5) / width
    ys = (np.arange(height, dtype=np.float32) + 0.5) / height
    with np.errstate(invalid="ignore", divide="ignore"):
        cx = (difference.sum(axis=1) * xs).sum(axis=1) / weights
        cy = (difference.sum(axis=2) * ys).sum(axis=1) / weights
    still = difference.mean(axis=(1, 2)) < MOTION_PIXELS
    cx[still] = np.nan
    cy[still] = np.nan
    cx = np.concatenate([[np.nan], cx])
    cy = np.concatenate([[np.nan], cy])

    histograms = np.stack(
        [np.histogram(frame, bins=HISTOGRAM_BINS, range=(0, 256))[0] for frame in frames]
    ).astype(np.float32)
    histograms /= histograms.sum(axis=1, keepdims=True)
    distance = 0.5 * np.abs(np.diff(histograms, axis=0)).sum(axis=1)
    cuts = []
    for index, value in enumerate(distance):
        neighbours = distance[max(0, index - 2) : index + 3]
        if value > CUT_THRESHOLD and value >= neighbours.max():
            cuts.append((index + 1) * hop_us)
    return (
        _fit(motion, length),
        _fit(cx, length, np.nan),
        _fit(cy, length, np.nan),
        np.array(cuts, dtype=np.int64),
    )


def extract_video(
    source: Path,
    duration_us: int,
    toolchain: Toolchain,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
) -> tuple[Series, Series, Series, npt.NDArray[np.int64]]:
    frame_size = FRAME_W * FRAME_H
    expected = series_length(duration_us)
    chunks: list[bytes] = []
    received = 0

    def collect(chunk: bytes) -> None:
        nonlocal received
        chunks.append(chunk)
        received += len(chunk)
        progress(min(received / (expected * frame_size), 1.0), "Analysing motion")

    rate = round(1_000_000 / HOP_US)
    run(
        [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-vf",
            f"fps={rate},scale={FRAME_W}:{FRAME_H}:flags=area,format=gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        token=token,
        on_chunk=collect,
    )
    data = b"".join(chunks)
    frames = np.frombuffer(data[: len(data) - len(data) % frame_size], dtype=np.uint8)
    return frames_to_features(frames.reshape(-1, FRAME_H, FRAME_W), expected)


# --- Audio ---------------------------------------------------------------------------------------


def samples_to_features(
    samples: Series, length: int, hop_us: int = HOP_US
) -> tuple[Series, Series]:
    """Audio energy and onset strength per hop from mono samples at `AUDIO_RATE`."""
    hop = AUDIO_RATE * hop_us // 1_000_000
    usable = len(samples) - len(samples) % hop
    if usable == 0:
        return np.zeros(length, dtype=np.float32), np.zeros(length, dtype=np.float32)
    frames = samples[:usable].reshape(-1, hop)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    level = 20 * np.log10(rms + 1e-9)
    energy = np.clip((level - SILENCE_DB) / -SILENCE_DB, 0.0, 1.0)

    import librosa

    sub_hop = hop // 5
    strength = librosa.onset.onset_strength(
        y=samples.astype(np.float32), sr=AUDIO_RATE, hop_length=sub_hop
    )
    per_hop = strength[: len(strength) - len(strength) % 5].reshape(-1, 5).max(axis=1)
    scale = float(np.percentile(per_hop, 99)) if per_hop.size else 0.0
    onset = np.clip(per_hop / scale, 0.0, 1.0) if scale > 0 else np.zeros_like(per_hop)
    return _fit(energy, length), _fit(onset, length)


def read_audio(
    source: Path,
    toolchain: Toolchain,
    token: CancellationToken | None = None,
    rate: int = AUDIO_RATE,
) -> Series:
    """Mono float samples of the first audio stream."""
    chunks: list[bytes] = []
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
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        token=token,
        on_chunk=chunks.append,
    )
    data = b"".join(chunks)
    return np.frombuffer(data[: len(data) - len(data) % 4], dtype="<f4").astype(np.float32)


# --- Whole clip ----------------------------------------------------------------------------------


def features_path(cache: MediaCache, clip: Clip) -> Path:
    return cache.project_dir / "cache" / f"{content_key(clip)}_features_v{FEATURES_VERSION}.npz"


def analyze_clip(
    clip: Clip,
    cache: MediaCache,
    toolchain: Toolchain,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
    proxy_height: int = 540,
) -> ClipFeatures:
    """Features of ``clip``, from the cache when available."""
    path = features_path(cache, clip)
    cached = ClipFeatures.load(path) if path.is_file() else None
    if cached is not None and cached.duration_us == clip.duration_us:
        progress(1.0, "Features loaded from cache")
        return cached
    proxy = cache.proxy_path(clip, proxy_height)
    source = proxy if proxy.is_file() else clip.file_path
    length = series_length(clip.duration_us)
    motion, cx, cy, cuts = extract_video(
        source, clip.duration_us, toolchain, sub_progress(progress, 0.0, 0.7), token
    )
    if clip.has_audio:
        progress(0.7, "Analysing audio")
        samples = read_audio(source, toolchain, token)
        energy, onset = samples_to_features(samples, length)
    else:
        energy = onset = np.zeros(length, dtype=np.float32)
    features = ClipFeatures(clip.duration_us, motion, cx, cy, energy, onset, cuts)
    path.parent.mkdir(parents=True, exist_ok=True)
    features.save(path)
    progress(1.0, "Analysis done")
    return features
