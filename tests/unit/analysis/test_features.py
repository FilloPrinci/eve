import itertools

import numpy as np
import pytest

from easy_video_editor.analysis.beats import grid_from_times
from easy_video_editor.analysis.features import (
    AUDIO_RATE,
    FRAME_H,
    FRAME_W,
    ClipFeatures,
    frames_to_features,
    samples_to_features,
)
from easy_video_editor.analysis.follow import follow_path
from easy_video_editor.analysis.scoring import mask, normalise, score, smooth
from easy_video_editor.core.model import Clip

from .conftest import S, bursts


def moving_block_frames() -> np.ndarray:
    """20 still grey frames, 20 frames of a white block moving right, then a cut to black."""
    frames = np.full((60, FRAME_H, FRAME_W), 100, dtype=np.uint8)
    for index in range(20, 40):
        x = (index - 20) * 2
        frames[index, 10:20, x : x + 8] = 255
    frames[40:] = 0
    return frames


def test_motion_centroid_and_cuts() -> None:
    motion, cx, _, cuts = frames_to_features(moving_block_frames(), 60)
    assert motion[:20].max() == 0.0
    assert motion[21:39].min() > 0.0
    assert np.isnan(cx[5])
    assert cx[22] < cx[30] < cx[38]  # the centroid follows the block to the right
    assert list(cuts) == [40 * 100_000]


def test_short_input() -> None:
    motion, cx, _, cuts = frames_to_features(np.zeros((1, FRAME_H, FRAME_W), np.uint8), 5)
    assert motion.shape == (5,) and np.isnan(cx).all() and cuts.size == 0


def test_audio_energy_and_onsets() -> None:
    rate = AUDIO_RATE
    quiet = 0.001 * np.sin(np.linspace(0, 200 * np.pi, rate))
    loud = 0.5 * np.sin(np.linspace(0, 400 * np.pi, rate))
    clicks = np.zeros(rate, dtype=np.float32)
    clicks[:: rate // 4] = 1.0
    samples = np.concatenate([quiet, loud, clicks + quiet]).astype(np.float32)
    energy, onset = samples_to_features(samples, 30)
    assert energy[:10].mean() < 0.3 < 0.8 < energy[10:20].mean()
    assert onset[20:30].max() > 0.5
    assert energy.shape == onset.shape == (30,)


def test_features_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    features = bursts([(5, 10)])
    path = tmp_path / "f.npz"
    features.save(path)
    loaded = ClipFeatures.load(path)
    assert loaded is not None and np.array_equal(loaded.motion, features.motion)
    assert ClipFeatures.load(tmp_path / "missing.npz") is None


def test_scoring_helpers() -> None:
    assert normalise(np.ones(10, dtype=np.float32)).max() == 0
    assert smooth(np.array([0, 0, 3, 0, 0], dtype=np.float32), 3)[2] == pytest.approx(1)
    masked = mask(np.ones(100, dtype=np.float32), 100_000, [(1 * S, 2 * S)])
    assert np.isnan(masked[10:20]).all() and np.isfinite(masked[:10]).all()
    values = score(bursts([(10, 15)]), "adrenaline")
    assert values[120] > 0.6 > 0.1 > values[400]


def test_follow_path_is_smooth_and_uses_the_right_axis() -> None:
    features = bursts([])
    features.centroid_x[:100] = np.nan
    wide = Clip(id="c", path="/a", name="a", duration_us=60 * S, width=1920, height=1080)
    path = follow_path(wide, features)
    assert path[0][0] == 0 and 0.15 < path[0][1] < 0.85
    assert all(b[1] >= a[1] - 1e-3 for a, b in itertools.pairwise(path))
    tall = wide.model_copy(update={"width": 1080, "height": 1920})
    features.centroid_y[:] = np.nan
    assert follow_path(tall, features) == [(0, 0.5)]


def test_beat_grid_from_times() -> None:
    grid = grid_from_times([0.5, 1.0, 1.5, 2.0, 2.5])
    assert grid is not None and grid.period_us == 500_000 and grid.tempo_bpm == 120
    assert grid_from_times([1.0, 2.0]) is None
