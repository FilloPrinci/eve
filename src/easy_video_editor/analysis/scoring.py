"""Turning features into an "interest" score per time step (SPEC §5.4)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from easy_video_editor.analysis.features import ClipFeatures, Series

SMOOTHING_US = 1_000_000
CUT_WINDOW_US = 2_000_000


@dataclass(frozen=True)
class Weights:
    motion: float
    audio: float
    onset: float
    cuts: float


MOOD_WEIGHTS = {
    "adrenaline": Weights(motion=0.45, audio=0.2, onset=0.2, cuts=0.15),
    "epic": Weights(motion=0.35, audio=0.3, onset=0.2, cuts=0.15),
    "cinematic": Weights(motion=0.25, audio=0.35, onset=0.15, cuts=0.25),
}
DEFAULT_WEIGHTS = Weights(motion=0.4, audio=0.25, onset=0.2, cuts=0.15)


def normalise(values: Series) -> Series:
    """Robust 0..1 scaling (5th to 95th percentile); constant series become 0."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros_like(values)
    low, high = np.percentile(finite, [5, 95])
    if high - low < 1e-6:
        return np.zeros_like(values)
    scaled: Series = np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)
    return scaled


def smooth(values: Series, window: int) -> Series:
    """Moving average; edges are extended (not zero-padded) so they keep their level."""
    if window <= 1 or len(values) == 0:
        return values
    kernel = np.ones(window, dtype=np.float32) / window
    before = window // 2
    padded = np.pad(values, (before, window - 1 - before), mode="edge")
    result: Series = np.convolve(padded, kernel, mode="valid").astype(np.float32)
    return result


def cut_density(features: ClipFeatures) -> Series:
    """Scene cuts per window, normalised."""
    counts = np.zeros(features.length, dtype=np.float32)
    for cut in features.cuts_us:
        index = int(cut // features.hop_us)
        if 0 <= index < features.length:
            counts[index] += 1
    return normalise(smooth(counts, max(1, CUT_WINDOW_US // features.hop_us)))


def score(features: ClipFeatures, mood: str) -> Series:
    """Weighted interest score in 0..1, smoothed over about one second."""
    weights = MOOD_WEIGHTS.get(mood, DEFAULT_WEIGHTS)
    total = (
        weights.motion * normalise(features.motion)
        + weights.audio * normalise(features.audio_energy)
        + weights.onset * normalise(features.onset)
        + weights.cuts * cut_density(features)
    )
    return smooth(total.astype(np.float32), max(1, SMOOTHING_US // features.hop_us))


def dominant_feature(features: ClipFeatures, start: int, end: int) -> str:
    """Short English label for what makes a range interesting."""
    parts = {
        "Fast action": float(normalise(features.motion)[start:end].mean()),
        "Loud moment": float(normalise(features.audio_energy)[start:end].mean()),
        "Impacts": float(normalise(features.onset)[start:end].mean()),
        "Quick cuts": float(cut_density(features)[start:end].mean()),
    }
    return max(parts, key=lambda name: parts[name])


def mask(values: Series, hop_us: int, ranges: Iterable[tuple[int, int]]) -> Series:
    """Copy of ``values`` with every step touching a range set to NaN (never selected)."""
    masked = values.astype(np.float32).copy()
    for start, end in ranges:
        first = max(0, int(start // hop_us))
        last = min(len(masked), -(-end // hop_us))
        masked[first:last] = np.nan
    return masked
