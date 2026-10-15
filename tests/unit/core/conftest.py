from collections.abc import Callable
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from easy_video_editor.core.commands import AddClip, CreateTimeline, CreateZone
from easy_video_editor.core.model import Origin, Project, ZoneKind
from easy_video_editor.core.session import ProjectSession

S = 1_000_000
"""One second in microseconds."""

MEDIA = Path(Path.cwd().anchor) / "media"
"""An absolute folder on every platform (``/media`` on Linux, ``C:\\media`` on Windows)."""

USER, AI, AUTO = Origin.USER, Origin.AI, Origin.AUTO
RED, BLUE, YELLOW = ZoneKind.EXCLUDE, ZoneKind.CANDIDATE, ZoneKind.HIGHLIGHT


def sequential_ids() -> Callable[[str], str]:
    counter = count(1)
    return lambda prefix: f"{prefix}_{next(counter)}"


@pytest.fixture
def session() -> ProjectSession:
    """A project with one 60 s clip ``clip`` and one 16:9 timeline ``tl``."""
    session = ProjectSession(Project(name="Test"), id_factory=sequential_ids())
    session.execute(AddClip(path=str(MEDIA / "clip.mp4"), duration_us=60 * S, clip_id="clip"), USER)
    session.execute(CreateTimeline(aspect="16:9", timeline_id="tl"), USER)
    return session


def zone(
    session: ProjectSession,
    kind: ZoneKind,
    start_s: float,
    end_s: float,
    origin: Origin = USER,
    **kwargs: Any,
) -> str:
    command = CreateZone(
        kind=kind, clip_id="clip", start_us=int(start_s * S), end_us=int(end_s * S), **kwargs
    )
    return session.execute(command, origin).created_ids[0]
