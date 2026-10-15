from collections.abc import Callable
from itertools import count

import numpy as np
import pytest

from easy_video_editor.analysis.features import HOP_US, ClipFeatures, series_length
from easy_video_editor.core.commands import AddClip, UpdateSettings
from easy_video_editor.core.model import FrameRate, Origin, Project
from easy_video_editor.core.session import ProjectSession
from tests.unit.core.conftest import MEDIA

S = 1_000_000
DURATION = 60 * S


def ids() -> Callable[[str], str]:
    numbers = count(1)
    return lambda prefix: f"{prefix}_{next(numbers)}"


def bursts(ranges: list[tuple[float, float]], duration_us: int = DURATION) -> ClipFeatures:
    """Synthetic features: strong motion and loud audio inside ``ranges`` (seconds)."""
    length = series_length(duration_us)
    motion = np.full(length, 0.01, dtype=np.float32)
    energy = np.full(length, 0.1, dtype=np.float32)
    for start, end in ranges:
        first, last = int(start * S // HOP_US), int(end * S // HOP_US)
        motion[first:last] = 0.6
        energy[first:last] = 0.9
    centroid = np.linspace(0.2, 0.8, length).astype(np.float32)
    return ClipFeatures(
        duration_us=duration_us,
        motion=motion,
        centroid_x=centroid,
        centroid_y=centroid.copy(),
        audio_energy=energy,
        onset=np.zeros(length, dtype=np.float32),
        cuts_us=np.array([int(r[0] * S) for r in ranges], dtype=np.int64),
    )


@pytest.fixture
def session() -> ProjectSession:
    session = ProjectSession(Project(name="Auto"), id_factory=ids())
    session.execute(
        AddClip(
            path=str(MEDIA / "lap.mp4"),
            duration_us=DURATION,
            clip_id="clip",
            frame_rate=FrameRate(num=30),
            width=1920,
            height=1080,
        ),
        Origin.USER,
    )
    session.execute(UpdateSettings(changes={"target_duration_us": 15 * S}), Origin.USER)
    return session
