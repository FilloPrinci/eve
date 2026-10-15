"""Source view (SPEC §4.3, §8.1): thumbnails, waveform and the red/blue/yellow zone lanes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from fractions import Fraction

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from easy_video_editor.core.commands import CreateZone, UpdateZone
from easy_video_editor.core.model import Clip, Zone, ZoneKind
from easy_video_editor.core.timecode import format_us
from easy_video_editor.media.generate import StripInfo
from easy_video_editor.ui.controller import ProjectController, Selection, SelectionKind
from easy_video_editor.ui.timescale import TimeScale, drag_range, hit_test, snap

log = logging.getLogger(__name__)

RULER_H, THUMBS_H, WAVE_H, LANE_H, GAP = 20, 48, 40, 24, 2
LANES = (ZoneKind.EXCLUDE, ZoneKind.CANDIDATE, ZoneKind.HIGHLIGHT)
ZONE_COLORS = {
    ZoneKind.EXCLUDE: QColor("#E5484D"),
    ZoneKind.CANDIDATE: QColor("#6CB6FF"),
    ZoneKind.HIGHLIGHT: QColor("#F5C400"),
}
PLAYHEAD_COLOR = QColor("#FF3B30")
DEFAULT_FPS = Fraction(30)
MIN_ZONE_FRAMES = 2


@dataclass
class _Drag:
    kind: str
    """``create``, ``start``, ``end``, ``body`` or ``seek``."""
    lane: ZoneKind | None
    origin_us: int
    zone_id: str | None = None
    original: tuple[int, int] | None = None
    current: tuple[int, int] | None = None


def paint_lock(painter: QPainter, left: float, center_y: float) -> None:
    """A small padlock (emoji fonts are not available everywhere)."""
    painter.save()
    painter.setPen(QPen(QColor("#000000"), 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(left + 2, center_y - 7, 6, 8), 0, 180 * 16)
    painter.setBrush(QColor("#000000"))
    painter.drawRect(QRectF(left, center_y - 3, 10, 7))
    painter.restore()


def lane_top(index: int) -> int:
    return RULER_H + THUMBS_H + WAVE_H + 3 * GAP + index * (LANE_H + GAP)


class SourceView(QWidget):
    seekRequested = Signal(int)
    """Clip time in microseconds."""
    marksChanged = Signal()

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.scale = TimeScale(duration_us=1, width=1)
        self.clip: Clip | None = None
        self.playhead_us = 0
        self.mark_in: int | None = None
        self.mark_out: int | None = None
        self.snap_to_zones = True
        self._strip: QPixmap | None = None
        self._strip_info: StripInfo | None = None
        self._peaks: npt.NDArray[np.float32] | None = None
        self._drag: _Drag | None = None
        self.setMinimumHeight(lane_top(len(LANES)) + GAP)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        controller.currentClipChanged.connect(self.reload_clip)
        controller.projectChanged.connect(self.on_project_changed)
        controller.selectionChanged.connect(self.update)

    # --- Data ----------------------------------------------------------------------------------

    @property
    def fps(self) -> Fraction:
        if self.clip and self.clip.frame_rate:
            return self.clip.frame_rate.as_fraction()
        return DEFAULT_FPS

    def reload_clip(self) -> None:
        project = self.controller.project
        clip_id = self.controller.current_clip_id
        self.clip = project.clip(clip_id) if project and clip_id else None
        self.mark_in = self.mark_out = None
        self.playhead_us = 0
        self.scale = TimeScale(
            duration_us=self.clip.duration_us if self.clip else 1, width=max(self.width(), 1)
        )
        self.scale.fit()
        self.reload_media()
        self.marksChanged.emit()

    def reload_media(self) -> None:
        self._strip = self._strip_info = self._peaks = None
        cache = self.controller.cache
        if self.clip is None or cache is None:
            self.update()
            return
        strip_path, info_path = cache.strip_path(self.clip), cache.strip_info_path(self.clip)
        if strip_path.is_file() and info_path.is_file():
            self._strip = QPixmap(str(strip_path))
            try:
                self._strip_info = StripInfo.load(info_path)
            except (OSError, ValueError, TypeError) as error:
                log.warning("Unreadable thumbnail info %s: %s", info_path, error)
        wave_path = cache.waveform_path(self.clip)
        if wave_path.is_file():
            try:
                self._peaks = np.load(wave_path)
            except (OSError, ValueError) as error:
                log.warning("Unreadable waveform %s: %s", wave_path, error)
        self.update()

    def on_project_changed(self) -> None:
        project = self.controller.project
        if self.clip and project:
            try:
                self.clip = project.clip(self.clip.id)
            except Exception:
                self.reload_clip()
                return
        self.update()

    def zones(self, kind: ZoneKind | None = None) -> list[Zone]:
        project = self.controller.project
        if project is None or self.clip is None:
            return []
        return project.zones_of(self.clip.id, kind)

    # --- Playhead and marks --------------------------------------------------------------------

    def set_playhead(self, us: int) -> None:
        self.playhead_us = us
        self.update()

    def set_mark_in(self) -> None:
        self.mark_in = self.playhead_us
        if self.mark_out is not None and self.mark_out <= self.mark_in:
            self.mark_out = None
        self.marksChanged.emit()
        self.update()

    def set_mark_out(self) -> None:
        self.mark_out = self.playhead_us
        if self.mark_in is not None and self.mark_in >= self.mark_out:
            self.mark_in = None
        self.marksChanged.emit()
        self.update()

    def zone_from_marks(self, kind: ZoneKind) -> None:
        if self.clip is None or self.mark_in is None or self.mark_out is None:
            self.controller.error.emit(self.tr("Set a mark-in (I) and a mark-out (O) first."))
            return
        parent_id = (
            self._parent_for(self.mark_in, self.mark_out) if kind is ZoneKind.HIGHLIGHT else None
        )
        result = self.controller.execute(
            CreateZone(
                kind=kind,
                clip_id=self.clip.id,
                start_us=self.mark_in,
                end_us=self.mark_out,
                parent_id=parent_id,
            )
        )
        if result:
            self.controller.select(Selection(SelectionKind.ZONE, result.created_ids[0]))

    def _parent_for(self, start: int, end: int) -> str | None:
        """The candidate zone overlapping a new highlight the most, if any."""
        best, best_overlap = None, 0
        for zone in self.zones(ZoneKind.CANDIDATE):
            overlap = min(end, zone.end_us) - max(start, zone.start_us)
            if overlap > best_overlap:
                best, best_overlap = zone.id, overlap
        return best

    # --- Painting ------------------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.scale.resize(event.size().width())
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        if self.clip is None:
            painter.setPen(palette.text().color())
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                self.tr("Import a clip and select it to see its zones."),
            )
            return
        self._paint_ruler(painter)
        self._paint_thumbnails(painter)
        self._paint_waveform(painter)
        self._paint_lanes(painter)
        self._paint_marks_and_playhead(painter)

    def _paint_ruler(self, painter: QPainter) -> None:
        color = self.palette().text().color()
        painter.setPen(color)
        visible_us = self.scale.width * self.scale.us_per_px
        steps = [
            100_000,
            250_000,
            500_000,
            1_000_000,
            2_000_000,
            5_000_000,
            10_000_000,
            30_000_000,
            60_000_000,
            300_000_000,
            600_000_000,
        ]
        step = next((s for s in steps if visible_us / s <= max(self.width() / 90, 1)), steps[-1])
        tick = int(self.scale.start_us // step) * step
        while tick <= self.scale.start_us + visible_us:
            x = self.scale.x(tick)
            painter.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
            painter.drawText(QPointF(x + 3, RULER_H - 7), format_us(tick))
            tick += step

    def _paint_thumbnails(self, painter: QPainter) -> None:
        top = RULER_H + GAP
        if self._strip is None or self._strip_info is None or self._strip.isNull():
            painter.fillRect(QRectF(0, top, self.width(), THUMBS_H), self.palette().alternateBase())
            return
        info = self._strip_info
        thumb_px = self.scale.x(info.interval_us) - self.scale.x(0)
        stride = max(
            1, int(np.ceil(THUMBS_H * info.thumb_width / info.thumb_height / max(thumb_px, 1)))
        )
        for index in range(0, info.count, stride):
            x = self.scale.x(index * info.interval_us)
            width = THUMBS_H * info.thumb_width / info.thumb_height
            if x > self.width() or x + width < 0:
                continue
            source = QRectF(index * info.thumb_width, 0, info.thumb_width, info.thumb_height)
            painter.drawPixmap(QRectF(x, top, width, THUMBS_H), self._strip, source)

    def _paint_waveform(self, painter: QPainter) -> None:
        top = RULER_H + THUMBS_H + 2 * GAP
        painter.fillRect(QRectF(0, top, self.width(), WAVE_H), self.palette().alternateBase())
        peaks = self._peaks
        if peaks is None or self.clip is None or len(peaks) == 0:
            return
        per_second = len(peaks) / (self.clip.duration_us / 1_000_000)
        middle = top + WAVE_H / 2
        gain = 1.0 / max(float(np.abs(peaks).max()), 1e-3)  # normalise to the clip's peak
        painter.setPen(QPen(self.palette().highlight().color(), 1))
        for x in range(self.width()):
            first = int(self.scale.us(x) / 1_000_000 * per_second)
            last = max(first + 1, int(self.scale.us(x + 1) / 1_000_000 * per_second))
            if first >= len(peaks):
                break
            chunk = peaks[max(first, 0) : last]
            low, high = float(chunk[:, 0].min()), float(chunk[:, 1].max())
            painter.drawLine(
                QPointF(x, middle - high * gain * WAVE_H / 2),
                QPointF(x, middle - low * gain * WAVE_H / 2),
            )

    def _paint_lanes(self, painter: QPainter) -> None:
        selected = self.controller.selection
        names = {
            ZoneKind.EXCLUDE: self.tr("Exclude"),
            ZoneKind.CANDIDATE: self.tr("Candidates"),
            ZoneKind.HIGHLIGHT: self.tr("Highlights"),
        }
        for index, kind in enumerate(LANES):
            top = lane_top(index)
            painter.fillRect(QRectF(0, top, self.width(), LANE_H), self.palette().alternateBase())
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(
                QRectF(4, top, 200, LANE_H), Qt.AlignmentFlag.AlignVCenter, names[kind]
            )
            for zone in self.zones(kind):
                self._paint_zone(painter, zone, top, selected)
        drag = self._drag
        if drag and drag.current and drag.lane is not None:
            top = lane_top(LANES.index(drag.lane))
            color = QColor(ZONE_COLORS[drag.lane])
            color.setAlpha(120)
            x0, x1 = self.scale.x(drag.current[0]), self.scale.x(drag.current[1])
            painter.fillRect(QRectF(x0, top, x1 - x0, LANE_H), color)

    def _paint_zone(
        self, painter: QPainter, zone: Zone, top: int, selected: Selection | None
    ) -> None:
        drag = self._drag
        start, end = zone.start_us, zone.end_us
        if drag and drag.zone_id == zone.id and drag.current:
            start, end = drag.current
        x0, x1 = self.scale.x(start), self.scale.x(end)
        if x1 < 0 or x0 > self.width():
            return
        rect = QRectF(x0, top + 1, max(x1 - x0, 2), LANE_H - 2)
        color = QColor(ZONE_COLORS[zone.kind])
        if zone.stale:
            color.setAlpha(110)
        painter.fillRect(rect, color)
        is_selected = selected is not None and selected.item_id == zone.id
        pen = QPen(
            self.palette().text().color() if is_selected else color.darker(150),
            2 if is_selected else 1,
        )
        if zone.stale:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        label = zone.label or ""
        text_rect = rect.adjusted(4, 0, -2, 0)
        if zone.locked and zone.kind is not ZoneKind.EXCLUDE:
            paint_lock(painter, rect.left() + 4, rect.center().y())
            text_rect = rect.adjusted(16, 0, -2, 0)
        if zone.priority:
            label += " " + "★" * zone.priority
        painter.setPen(QColor("#000000"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, label.strip())

    def _paint_marks_and_playhead(self, painter: QPainter) -> None:
        height = lane_top(len(LANES))
        if self.mark_in is not None or self.mark_out is not None:
            start = self.mark_in if self.mark_in is not None else 0
            end = (
                self.mark_out
                if self.mark_out is not None
                else (self.clip.duration_us if self.clip else 0)
            )
            shade = QColor(self.palette().highlight().color())
            shade.setAlpha(50)
            painter.fillRect(
                QRectF(self.scale.x(start), 0, self.scale.x(end) - self.scale.x(start), height),
                shade,
            )
        x = self.scale.x(self.playhead_us)
        painter.setPen(QPen(PLAYHEAD_COLOR, 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, height))
        painter.setBrush(QBrush(PLAYHEAD_COLOR))

    # --- Mouse ---------------------------------------------------------------------------------

    def _lane_at(self, y: float) -> ZoneKind | None:
        for index, kind in enumerate(LANES):
            top = lane_top(index)
            if top <= y < top + LANE_H:
                return kind
        return None

    def _snap(self, us: int, exclude_id: str | None = None) -> int:
        targets: list[int] = []
        if self.snap_to_zones:
            for zone in self.zones():
                if zone.id != exclude_id:
                    targets += [zone.start_us, zone.end_us]
            targets.append(self.playhead_us)
        return snap(us, self.scale, self.fps, targets)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.clip is None or event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        us = self._snap(self.scale.us(x))
        lane = self._lane_at(y)
        if lane is None:
            self._drag = _Drag("seek", None, us)
            self.seekRequested.emit(us)
            return
        zones = self.zones(lane)
        hit = hit_test([(z.start_us, z.end_us) for z in zones], self.scale, x)
        if hit is None:
            self.controller.select(None)
            self._drag = _Drag("create", lane, us, current=None)
            return
        zone = zones[hit.index]
        self.controller.select(Selection(SelectionKind.ZONE, zone.id))
        self._drag = _Drag(hit.part, lane, self.scale.us(x), zone.id, (zone.start_us, zone.end_us))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        drag = self._drag
        if drag is None:
            self._update_cursor(event)
            return
        if drag.kind == "seek":
            self.seekRequested.emit(self._snap(self.scale.us(x)))
            return
        min_length = int(MIN_ZONE_FRAMES * 1_000_000 / self.fps)
        if drag.kind == "create":
            us = self._snap(self.scale.us(x))
            drag.current = (min(drag.origin_us, us), max(drag.origin_us, us))
        elif drag.original is not None and self.clip is not None:
            delta = self.scale.us(x) - drag.origin_us
            start, end = drag_range(
                *drag.original, drag.kind, delta, self.clip.duration_us, min_length
            )
            if drag.kind == "body":
                snapped = self._snap(start, drag.zone_id)
                start, end = snapped, snapped + (end - start)
            elif drag.kind == "start":
                start = min(self._snap(start, drag.zone_id), end - min_length)
            else:
                end = max(self._snap(end, drag.zone_id), start + min_length)
            drag.current = (start, end)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        drag, self._drag = self._drag, None
        if drag is None or self.clip is None or drag.current is None:
            self.update()
            return
        start, end = drag.current
        min_length = int(MIN_ZONE_FRAMES * 1_000_000 / self.fps)
        if drag.kind == "create" and drag.lane is not None and end - start >= min_length:
            parent = self._parent_for(start, end) if drag.lane is ZoneKind.HIGHLIGHT else None
            result = self.controller.execute(
                CreateZone(
                    kind=drag.lane,
                    clip_id=self.clip.id,
                    start_us=start,
                    end_us=end,
                    parent_id=parent,
                )
            )
            if result:
                self.controller.select(Selection(SelectionKind.ZONE, result.created_ids[0]))
        elif drag.zone_id and drag.original != drag.current:
            self.controller.execute(UpdateZone(zone_id=drag.zone_id, start_us=start, end_us=end))
        self.update()

    def _update_cursor(self, event: QMouseEvent) -> None:
        lane = self._lane_at(event.position().y())
        shape = Qt.CursorShape.ArrowCursor
        if lane is not None:
            zones = self.zones(lane)
            hit = hit_test(
                [(z.start_us, z.end_us) for z in zones], self.scale, event.position().x()
            )
            if hit and hit.part in ("start", "end"):
                shape = Qt.CursorShape.SizeHorCursor
            elif hit:
                shape = Qt.CursorShape.OpenHandCursor
        self.setCursor(shape)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.scale.zoom(1.25 if delta > 0 else 0.8, event.position().x())
        else:
            self.scale.scroll(-delta / 2)
        self.update()
        event.accept()
