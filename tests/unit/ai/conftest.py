from pathlib import Path

import pytest

from easy_video_editor.ai.tools import ToolContext
from easy_video_editor.core.commands import AddClip, CreateTimeline, CreateZone
from easy_video_editor.core.model import FrameRate, Origin, Project, ZoneKind
from easy_video_editor.core.persistence import create_project, load_project, save_project
from easy_video_editor.core.session import ProjectSession

S = 1_000_000
MEDIA = Path(Path.cwd().anchor) / "media"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A saved project: one 60 s clip ``clip`` (no media on disk), a red zone at 20-22 s, a user
    candidate zone ``user_blue`` at 40-50 s and a 16:9 timeline ``tl``."""
    directory = tmp_path / "Ai.eve"
    create_project(directory, Project(name="Ai"))
    session = ProjectSession(load_project(directory))
    session.execute(
        AddClip(
            path=str(MEDIA / "lap.mp4"), duration_us=60 * S, clip_id="clip",
            frame_rate=FrameRate(num=30), width=1920, height=1080,
        ),
        Origin.USER,
    )  # fmt: skip
    for kind, start, end, zone_id in (
        (ZoneKind.EXCLUDE, 20, 22, "red"),
        (ZoneKind.CANDIDATE, 40, 50, "user_blue"),
    ):
        session.execute(
            CreateZone(kind=kind, clip_id="clip", start_us=start * S, end_us=end * S,
                       zone_id=zone_id),
            Origin.USER,
        )  # fmt: skip
    session.execute(CreateTimeline(aspect="16:9", timeline_id="tl"), Origin.USER)
    save_project(session.project, directory)
    return directory


@pytest.fixture
def ctx(project_dir: Path) -> ToolContext:
    return ToolContext(ProjectSession(load_project(project_dir)), project_dir)
