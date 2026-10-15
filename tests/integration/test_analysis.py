"""Analysis and Auto mode on real media (SPEC §5)."""

import time
from pathlib import Path

import numpy as np
import pytest

from easy_video_editor.analysis.beats import detect_beats
from easy_video_editor.analysis.features import analyze_clip
from easy_video_editor.cli.main import EXIT_OK, main
from easy_video_editor.core.model import Origin, Project, ZoneKind
from easy_video_editor.core.persistence import load_project, save_project
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache
from easy_video_editor.media.importer import add_clip_command

from .conftest import BURSTS

S = 1_000_000


def test_features_find_bursts_and_cuts(
    media_dir: Path, toolchain: Toolchain, tmp_path: Path
) -> None:
    session = ProjectSession(Project(name="F"))
    clip_id = session.execute(
        add_clip_command(media_dir / "bursts.mp4", toolchain)[0], Origin.USER
    ).created_ids[0]
    clip = session.project.clip(clip_id)
    cache = MediaCache(tmp_path)
    features = analyze_clip(clip, cache, toolchain)
    cuts = sorted(features.cuts_us / S)
    assert cuts == pytest.approx([8, 13, 20, 24], abs=0.15)
    inside = np.concatenate([features.motion[int(a * 10) + 2 : int(b * 10) - 2] for a, b in BURSTS])
    assert inside.min() > 0.0 and features.motion[20:70].max() == 0.0
    assert features.audio_energy[90:120].mean() > features.audio_energy[20:70].mean() + 0.3

    started = time.monotonic()
    again = analyze_clip(clip, cache, toolchain)
    assert time.monotonic() - started < 1.0  # from the cache
    assert np.array_equal(again.motion, features.motion)


def test_beats_of_a_click_track(media_dir: Path, toolchain: Toolchain, tmp_path: Path) -> None:
    grid = detect_beats(media_dir / "click.wav", toolchain, tmp_path)
    assert grid is not None and abs(grid.period_us - 500_000) < 15_000
    assert list(tmp_path.glob("*_beats_v1.json"))


def test_cli_auto_pipeline_and_follow_crop_render(media_dir: Path, tmp_path: Path) -> None:
    from easy_video_editor.core.commands import CreateZone, SetOutputPresets
    from easy_video_editor.core.model import OutputPreset, ReframeMode

    assert main(["new", str(tmp_path), "--name", "Auto"]) == EXIT_OK
    project_dir = tmp_path / "Auto.eve"
    assert main(["import", str(project_dir), str(media_dir / "bursts.mp4")]) == EXIT_OK
    session = ProjectSession(load_project(project_dir))
    clip_id = session.project.clips[0].id
    session.execute(
        CreateZone(kind=ZoneKind.EXCLUDE, clip_id=clip_id, start_us=21 * S, end_us=22 * S),
        Origin.USER,
    )
    presets = [
        OutputPreset(id="wide", name="Wide", width=320, height=180, fps=25),
        OutputPreset(
            id="tall",
            name="Tall",
            width=180,
            height=320,
            fps=25,
            reframe_mode=ReframeMode.FOLLOW_CROP,
        ),
    ]
    session.execute(
        SetOutputPresets(presets=presets, enabled_preset_ids=["wide", "tall"]), Origin.USER
    )
    save_project(session.project, project_dir)

    for command in ("analyze", "refine", "assemble"):
        assert main([command, str(project_dir)]) == EXIT_OK
    project = load_project(project_dir)
    candidates = project.zones_of_kind(ZoneKind.CANDIDATE)
    assert candidates and all(z.origin is Origin.AUTO for z in candidates)
    for zone in project.zones:
        if zone.kind is not ZoneKind.EXCLUDE:
            assert zone.end_us <= 21 * S or zone.start_us >= 22 * S
    assert sorted(t.aspect for t in project.timelines) == ["16:9", "9:16"]
    assert all(t.segments for t in project.timelines)

    assert main(["validate", str(project_dir)]) == EXIT_OK
    assert main(["render", str(project_dir), "--preset", "tall", "--draft"]) == EXIT_OK
    assert (project_dir / "exports" / "Auto-tall-draft.mp4").is_file()
    assert main(["refresh", str(project_dir)]) == EXIT_OK
    assert main(["analyze", str(project_dir), "--mode", "ai"]) != EXIT_OK
