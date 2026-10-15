"""Running the Auto phases: analyse what is needed, then compute the plan (SPEC §5)."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from easy_video_editor.analysis.auto import (
    music_path,
    plan_phase1,
    plan_phase2,
    plan_phase3,
    plan_refresh_stale,
)
from easy_video_editor.analysis.beats import BeatGrid, detect_beats
from easy_video_editor.analysis.features import ClipFeatures, analyze_clip
from easy_video_editor.analysis.plan import Plan
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.errors import EveError
from easy_video_editor.core.model import Project
from easy_video_editor.core.progress import (
    CancellationToken,
    Progress,
    no_progress,
    sub_progress,
)
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache


class Phase(StrEnum):
    ANALYZE = "analyze"
    """Phase 1: candidate (blue) zones."""
    REFINE = "refine"
    """Phase 2: highlight (yellow) zones."""
    ASSEMBLE = "assemble"
    """Phase 3: the edit timeline(s)."""
    REFRESH = "refresh"
    """Regenerate stale items only."""


def ensure_features(
    project: Project,
    cache: MediaCache,
    toolchain: Toolchain,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
    proxy_height: int = 540,
) -> dict[str, ClipFeatures]:
    features: dict[str, ClipFeatures] = {}
    count = len(project.clips)
    for index, clip in enumerate(project.clips):
        if not clip.file_path.is_file() and not cache.proxy_path(clip, proxy_height).is_file():
            continue
        part = sub_progress(progress, index / max(count, 1), (index + 1) / max(count, 1))
        part(0.0, f"Analysing clip {index + 1} of {count}: {clip.name}")
        features[clip.id] = analyze_clip(clip, cache, toolchain, part, token, proxy_height)
    return features


def ensure_beats(
    project: Project,
    cache: MediaCache,
    toolchain: Toolchain,
    token: CancellationToken | None = None,
) -> BeatGrid | None:
    path = music_path(project)
    if path is None or not Path(path).is_file():
        return None
    return detect_beats(Path(path), toolchain, cache.project_dir / "cache", token)


def plan_phase(
    phase: Phase,
    project: Project,
    cache: MediaCache,
    toolchain: Toolchain,
    brand: BrandKit | None = None,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
    proxy_height: int = 540,
) -> Plan:
    """Analyse what the phase needs (cached), then compute its plan from ``project``."""
    if phase is Phase.ASSEMBLE:
        progress(None, "Detecting music beats")
        return plan_phase3(project, ensure_beats(project, cache, toolchain, token), brand)
    if not project.clips:
        raise EveError("Import at least one clip first.")
    features = ensure_features(
        project, cache, toolchain, sub_progress(progress, 0.0, 0.9), token, proxy_height
    )
    if phase is Phase.ANALYZE:
        return plan_phase1(project, features)
    progress(0.9, "Detecting music beats")
    beats = ensure_beats(project, cache, toolchain, token)
    if phase is Phase.REFINE:
        return plan_phase2(project, features, beats)
    return plan_refresh_stale(project, features, beats)
