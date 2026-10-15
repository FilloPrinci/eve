"""Pure geometry for time-based views: time ↔ pixels, zoom, snapping and hit testing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from easy_video_editor.core.timecode import snap_to_frame

MIN_US_PER_PX = 1_000.0
"""Maximum zoom: one millisecond per pixel."""
EDGE_GRAB_PX = 5
SNAP_PX = 6


@dataclass
class TimeScale:
    duration_us: int
    width: int
    us_per_px: float = 1.0
    start_us: float = 0.0

    def fit(self) -> None:
        self.us_per_px = max(self.duration_us / max(self.width, 1), MIN_US_PER_PX)
        self.start_us = 0.0

    @property
    def max_us_per_px(self) -> float:
        return max(self.duration_us / max(self.width, 1), MIN_US_PER_PX)

    def x(self, us: float) -> float:
        return (us - self.start_us) / self.us_per_px

    def us(self, x: float) -> int:
        return round(self.start_us + x * self.us_per_px)

    def resize(self, width: int) -> None:
        fitted = self.us_per_px >= self.max_us_per_px * 0.999 or self.width <= 1
        self.width = max(width, 1)
        if fitted:
            self.fit()
        self._clamp()

    def zoom(self, factor: float, anchor_x: float) -> None:
        """Zoom by ``factor`` (> 1 zooms in) keeping the time under ``anchor_x`` in place."""
        anchor_us = self.start_us + anchor_x * self.us_per_px
        self.us_per_px = min(max(self.us_per_px / factor, MIN_US_PER_PX), self.max_us_per_px)
        self.start_us = anchor_us - anchor_x * self.us_per_px
        self._clamp()

    def scroll(self, pixels: float) -> None:
        self.start_us += pixels * self.us_per_px
        self._clamp()

    def ensure_visible(self, us: int) -> None:
        visible = self.width * self.us_per_px
        if us < self.start_us or us > self.start_us + visible:
            self.start_us = us - visible / 2
            self._clamp()

    def _clamp(self) -> None:
        visible = self.width * self.us_per_px
        self.start_us = min(max(self.start_us, 0.0), max(0.0, self.duration_us - visible))


def snap(
    us: int,
    scale: TimeScale,
    fps: Fraction | None,
    targets: Iterable[int] = (),
) -> int:
    """Snap to the nearest target edge within `SNAP_PX`, otherwise to the frame grid."""
    best: int | None = None
    for target in targets:
        if abs(scale.x(target) - scale.x(us)) <= SNAP_PX and (
            best is None or abs(target - us) < abs(best - us)
        ):
            best = target
    if best is not None:
        return best
    snapped = snap_to_frame(us, fps) if fps else us
    return min(max(snapped, 0), scale.duration_us)


@dataclass(frozen=True)
class Hit:
    index: int
    part: str
    """``start`` or ``end`` (edge grabs) or ``body``."""


def hit_test(ranges: Sequence[tuple[int, int]], scale: TimeScale, x: float) -> Hit | None:
    """Which range is under ``x``; edges win within `EDGE_GRAB_PX` (also just outside the range)."""
    for index, (start, end) in enumerate(ranges):
        x0, x1 = scale.x(start), scale.x(end)
        if (abs(x - x0) <= EDGE_GRAB_PX and (x1 - x0) > 2 * EDGE_GRAB_PX) or abs(x - x0) <= 2:
            return Hit(index, "start")
        if abs(x - x1) <= EDGE_GRAB_PX:
            return Hit(index, "end")
    for index, (start, end) in enumerate(ranges):
        if scale.x(start) <= x <= scale.x(end):
            return Hit(index, "body")
    return None


def drag_range(
    start: int, end: int, part: str, delta_us: int, duration_us: int, min_length_us: int
) -> tuple[int, int]:
    """New range after dragging ``part`` by ``delta_us``, kept inside the clip and non-empty."""
    if part == "start":
        return min(max(start + delta_us, 0), end - min_length_us), end
    if part == "end":
        return start, max(min(end + delta_us, duration_us), start + min_length_us)
    length = end - start
    new_start = min(max(start + delta_us, 0), duration_us - length)
    return new_start, new_start + length
