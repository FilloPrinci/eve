"""Beat detection on music (SPEC §5.2): the beat grid that shot lengths and cuts snap to."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from easy_video_editor.core.progress import CancellationToken
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.fingerprint import fingerprint

log = logging.getLogger(__name__)

BEATS_VERSION = 1
BEAT_RATE = 22_050


@dataclass(frozen=True)
class BeatGrid:
    beats_us: tuple[int, ...]
    period_us: int
    """Median interval between beats (more precise than the tempo estimate)."""

    @property
    def tempo_bpm(self) -> float:
        return 60_000_000 / self.period_us if self.period_us else 0.0

    def first_beat_us(self) -> int:
        return self.beats_us[0] if self.beats_us else 0


def grid_from_times(times_s: list[float]) -> BeatGrid | None:
    beats = [round(t * 1_000_000) for t in times_s]
    if len(beats) < 4:
        return None
    period = int(np.median(np.diff(beats)))
    return BeatGrid(tuple(beats), period) if period > 0 else None


def detect_beats(
    music: Path, toolchain: Toolchain, cache_dir: Path, token: CancellationToken | None = None
) -> BeatGrid | None:
    """Beat grid of a music file, cached by file content. None when no steady beat is found."""
    from easy_video_editor.analysis.features import read_audio

    key = fingerprint(music).content_key
    path = cache_dir / f"{key}_beats_v{BEATS_VERSION}.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BeatGrid(tuple(data["beats_us"]), data["period_us"]) if data else None
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("Ignoring unreadable beat cache %s", path)
    import librosa

    samples = read_audio(music, toolchain, token, rate=BEAT_RATE)
    if token is not None:
        token.raise_if_cancelled()
    _, times = librosa.beat.beat_track(y=samples, sr=BEAT_RATE, units="time")
    grid = grid_from_times([float(t) for t in times])
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(grid) if grid else {}), encoding="utf-8")
    log.info("Beats of %s: %s", music.name, f"{grid.tempo_bpm:.1f} BPM" if grid else "none")
    return grid
