"""Motion paths for follow-crop reframing (SPEC §6.3), from the cached clip features."""

from __future__ import annotations

import numpy as np

from easy_video_editor.analysis.features import ClipFeatures, features_path
from easy_video_editor.analysis.scoring import smooth
from easy_video_editor.core.model import Clip, Project
from easy_video_editor.media.cache import MediaCache

STEP_US = 500_000
SMOOTHING_US = 1_500_000

FollowPath = list[tuple[int, float]]


def follow_path(clip: Clip, features: ClipFeatures) -> FollowPath:
    """Smoothed motion centre along the axis a crop moves on (x for wide clips, y for tall ones)."""
    tall = bool(clip.width and clip.height and clip.height > clip.width)
    series = (features.centroid_y if tall else features.centroid_x).astype(np.float64)
    known = np.isfinite(series)
    if not known.any():
        return [(0, 0.5)]
    positions = np.arange(len(series))
    filled = np.interp(positions, positions[known], series[known])
    smoothed = smooth(filled.astype(np.float32), max(1, SMOOTHING_US // features.hop_us))
    step = max(1, STEP_US // features.hop_us)
    return [
        (index * features.hop_us, round(float(smoothed[index]), 4))
        for index in range(0, len(smoothed), step)
    ]


def follow_paths(project: Project, cache: MediaCache) -> dict[str, FollowPath]:
    """Paths for every clip with cached features (clips without them use a centred crop)."""
    paths = {}
    for clip in project.clips:
        path = features_path(cache, clip)
        features = ClipFeatures.load(path) if path.is_file() else None
        if features is not None:
            paths[clip.id] = follow_path(clip, features)
    return paths
