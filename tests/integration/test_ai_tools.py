"""AI tools on real media: frames, the frame budget and red zones (SPEC §7.5)."""

from pathlib import Path

import pytest

from easy_video_editor.ai.tools import ToolContext, ToolPhase, call_tool
from easy_video_editor.core.commands import CreateZone
from easy_video_editor.core.model import Origin, Project, ZoneKind
from easy_video_editor.core.persistence import create_project
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.importer import add_clip_command

S = 1_000_000


@pytest.fixture
def ctx(media_dir: Path, toolchain: Toolchain, tmp_path: Path) -> ToolContext:
    directory = tmp_path / "Frames.eve"
    create_project(directory, Project(name="Frames"))
    session = ProjectSession(Project(name="Frames"))
    clip_id = session.execute(
        add_clip_command(media_dir / "long.mp4", toolchain)[0], Origin.USER
    ).created_ids[0]
    session.execute(
        CreateZone(kind=ZoneKind.EXCLUDE, clip_id=clip_id, start_us=5 * S, end_us=8 * S),
        Origin.USER,
    )
    return ToolContext(session, directory, toolchain, frame_budget=2)


def test_frames_skip_red_zones_and_respect_the_budget(ctx: ToolContext) -> None:
    clip_id = ctx.project.clips[0].id
    result = call_tool(
        ctx, ToolPhase.ANALYZE, "get_frames", {"clip_id": clip_id, "times_s": [1.0, 6.0, 30.0]}
    )
    assert len(result.images) == 1 and result.images[0].data[:2] == b"\xff\xd8"  # JPEG
    assert "inside an exclude zone" in result.text and "outside the clip" in result.text
    assert ctx.frame_budget == 1

    result = call_tool(
        ctx, ToolPhase.ANALYZE, "get_frames", {"clip_id": clip_id, "times_s": [2.0, 3.0]}
    )
    assert len(result.images) == 1 and "used up" in result.text and ctx.frame_budget == 0


def test_features_are_summarised_per_second(ctx: ToolContext) -> None:
    clip_id = ctx.project.clips[0].id
    text = call_tool(
        ctx, ToolPhase.ANALYZE, "get_features", {"clip_id": clip_id, "end_s": 10, "step_s": 2}
    ).text
    times = [line.split()[0] for line in text.splitlines()[1:] if not line.startswith("scene")]
    assert times == ["0.0", "2.0", "8.0"]  # rows overlapping the red zone (5-8 s) are left out
