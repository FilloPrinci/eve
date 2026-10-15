"""Edit timeline view (SPEC §6.1, §8.1): segments, transitions, text overlays and music."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from easy_video_editor.core.commands import MoveSegment, UpdateSegment, UpdateText
from easy_video_editor.core.model import EditTimeline, Project, Segment, TransitionKind
from easy_video_editor.core.timecode import format_us
from easy_video_editor.ui.controller import ProjectController, Selection, SelectionKind
from easy_video_editor.ui.timescale import TimeScale, drag_range, hit_test

RULER_H, VIDEO_H, TEXT_H, MUSIC_H, GAP = 18, 54, 22, 22, 3
SEGMENT_COLOR = QColor("#4C7DBF")
STALE_COLOR = QColor("#8A6D3B")
END_CARD_COLOR = QColor("#555555")
TEXT_COLOR = QColor("#9B59B6")
MUSIC_COLOR = QColor("#2E8B57")
PLAYHEAD_COLOR = QColor("#FF3B30")
TRANSITION_MARKS = {
    TransitionKind.CROSSFADE: "X",
    TransitionKind.DIP_TO_BLACK: "B",
    TransitionKind.DIP_TO_WHITE: "W",
    TransitionKind.FLASH: "F",
}
MIN_SEGMENT_US = 100_000


def segment_spans(timeline: EditTimeline) -> list[tuple[int, int]]:
    """Output-time span of every segment."""
    spans, position = [], 0
    for segment in timeline.segments:
        spans.append((position, position + segment.output_duration_us))
        position += segment.output_duration_us
    return spans


def insertion_index(spans: list[tuple[int, int]], moving: int, center_us: int) -> int:
    """Index a dragged segment lands at, given the output time of its centre."""
    others = [span for index, span in enumerate(spans) if index != moving]
    return sum(1 for start, end in others if (start + end) / 2 < center_us)


@dataclass
class _Drag:
    target: str
    """``segment``, ``text`` or ``seek``."""
    part: str
    item_id: str
    origin_us: int
    original: tuple[int, int]
    current: tuple[int, int] | None = None


class TimelineView(QWidget):
    seekRequested = Signal(int)
    """Edit-timeline (output) time in microseconds."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.scale = TimeScale(duration_us=1_000_000, width=1)
        self.playhead_us = 0
        self._drag: _Drag | None = None
        self.setMinimumHeight(RULER_H + VIDEO_H + TEXT_H + MUSIC_H + 5 * GAP)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        controller.projectChanged.connect(self.refresh)
        controller.currentTimelineChanged.connect(self.refresh)
        controller.selectionChanged.connect(self.update)

    @property
    def timeline(self) -> EditTimeline | None:
        return self.controller.current_timeline

    def refresh(self) -> None:
        timeline = self.timeline
        duration = max(timeline.duration_us if timeline else 0, 5_000_000)
        fitted = self.scale.us_per_px >= self.scale.max_us_per_px * 0.999
        self.scale.duration_us = round(duration * 1.1)
        if fitted:
            self.scale.fit()
        self.update()

    def set_playhead(self, us: int) -> None:
        self.playhead_us = us
        self.update()

    # --- Rows ----------------------------------------------------------------------------------

    @staticmethod
    def video_top() -> int:
        return RULER_H + GAP

    @staticmethod
    def text_top() -> int:
        return RULER_H + VIDEO_H + 2 * GAP

    @staticmethod
    def music_top() -> int:
        return RULER_H + VIDEO_H + TEXT_H + 3 * GAP

    # --- Painting ------------------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.scale.resize(event.size().width())
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        timeline, project = self.timeline, self.controller.project
        if timeline is None or project is None:
            painter.setPen(palette.text().color())
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                self.tr("No edit timeline. Create one for an output preset."),
            )
            return
        for top, height in (
            (self.video_top(), VIDEO_H),
            (self.text_top(), TEXT_H),
            (self.music_top(), MUSIC_H),
        ):
            painter.fillRect(QRectF(0, top, self.width(), height), palette.alternateBase())
        self._paint_ruler(painter)
        self._paint_segments(painter, project, timeline)
        self._paint_texts(painter, timeline)
        self._paint_music(painter, timeline)
        x = self.scale.x(self.playhead_us)
        painter.setPen(QPen(PLAYHEAD_COLOR, 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    def _paint_ruler(self, painter: QPainter) -> None:
        painter.setPen(self.palette().text().color())
        steps = [500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 30_000_000, 60_000_000]
        step = next((s for s in steps if s / self.scale.us_per_px >= 70), steps[-1])
        tick = int(self.scale.start_us // step) * step
        end = self.scale.start_us + self.width() * self.scale.us_per_px
        while tick <= end:
            x = self.scale.x(tick)
            painter.drawLine(QPointF(x, RULER_H - 5), QPointF(x, RULER_H))
            painter.drawText(QPointF(x + 3, RULER_H - 6), format_us(tick))
            tick += step

    def _block(self, start: int, end: int, top: int, height: int) -> QRectF:
        x0, x1 = self.scale.x(start), self.scale.x(end)
        return QRectF(x0, top + 1, max(x1 - x0, 2), height - 2)

    def _paint_segments(self, painter: QPainter, project: Project, timeline: EditTimeline) -> None:
        selection = self.controller.selection
        spans = segment_spans(timeline)
        drag = self._drag
        for index, (segment, (start, end)) in enumerate(zip(timeline.segments, spans, strict=True)):
            if drag and drag.target == "segment" and drag.item_id == segment.id and drag.current:
                start, end = drag.current
            rect = self._block(start, end, self.video_top(), VIDEO_H)
            color = STALE_COLOR if segment.stale else SEGMENT_COLOR
            painter.fillRect(rect, color)
            selected = selection is not None and selection.item_id == segment.id
            painter.setPen(
                QPen(
                    self.palette().text().color() if selected else color.darker(140),
                    2 if selected else 1,
                )
            )
            painter.drawRect(rect)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                rect.adjusted(4, 2, -2, -2),
                Qt.AlignmentFlag.AlignLeft,
                self._segment_label(project, segment),
            )
            mark = TRANSITION_MARKS.get(segment.transition_out.kind)
            if mark and index < len(timeline.segments) - 1 + int(timeline.end_card.enabled):
                x = self.scale.x(spans[index][1])
                badge = QRectF(x - 8, self.video_top() + VIDEO_H / 2 - 8, 16, 16)
                painter.fillRect(badge, QColor("#F5C400"))
                painter.setPen(QColor("#000000"))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, mark)
        if timeline.end_card.enabled:
            start = spans[-1][1] if spans else 0
            rect = self._block(
                start, start + timeline.end_card.duration_us, self.video_top(), VIDEO_H
            )
            painter.fillRect(rect, END_CARD_COLOR)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.tr("End card"))

    def _segment_label(self, project: Project, segment: Segment) -> str:
        label = ""
        if segment.zone_id:
            try:
                label = project.zone(segment.zone_id).label
            except Exception:
                label = ""
        if not label:
            label = project.clip(segment.clip_id).name
        speed = "" if segment.speed == 1 else f"  {segment.speed:g}×"
        return f"{label}\n{format_us(segment.in_us)}–{format_us(segment.out_us)}{speed}"

    def _paint_texts(self, painter: QPainter, timeline: EditTimeline) -> None:
        selection = self.controller.selection
        drag = self._drag
        for text in timeline.texts:
            start, end = text.start_us, text.end_us
            if drag and drag.target == "text" and drag.item_id == text.id and drag.current:
                start, end = drag.current
            rect = self._block(start, end, self.text_top(), TEXT_H)
            painter.fillRect(rect, TEXT_COLOR)
            selected = selection is not None and selection.item_id == text.id
            painter.setPen(
                QPen(self.palette().text().color(), 2) if selected else TEXT_COLOR.darker()
            )
            painter.drawRect(rect)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(rect.adjusted(3, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, text.text)

    def _paint_music(self, painter: QPainter, timeline: EditTimeline) -> None:
        total = timeline.duration_us
        for cue in timeline.music:
            length = (
                (cue.source_out_us - cue.source_in_us)
                if cue.source_out_us
                else total - cue.start_us
            )
            rect = self._block(
                cue.start_us, min(cue.start_us + length, total), self.music_top(), MUSIC_H
            )
            painter.fillRect(rect, MUSIC_COLOR)
            painter.setPen(QColor("#FFFFFF"))
            name = cue.path.replace("\\", "/").rsplit("/", 1)[-1]
            painter.drawText(rect.adjusted(3, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, f"♪ {name}")

    # --- Mouse ---------------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        timeline = self.timeline
        if timeline is None or event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        us = self.scale.us(x)
        if self.video_top() <= y < self.video_top() + VIDEO_H:
            spans = segment_spans(timeline)
            hit = hit_test(spans, self.scale, x)
            if hit is not None:
                segment = timeline.segments[hit.index]
                self.controller.select(Selection(SelectionKind.SEGMENT, segment.id, timeline.id))
                self._drag = _Drag("segment", hit.part, segment.id, us, spans[hit.index])
                return
        elif self.text_top() <= y < self.text_top() + TEXT_H:
            ranges = [(t.start_us, t.end_us) for t in timeline.texts]
            hit = hit_test(ranges, self.scale, x)
            if hit is not None:
                text = timeline.texts[hit.index]
                self.controller.select(Selection(SelectionKind.TEXT, text.id, timeline.id))
                self._drag = _Drag("text", hit.part, text.id, us, ranges[hit.index])
                return
        self.controller.select(None)
        self._drag = _Drag("seek", "body", "", us, (us, us))
        self.seekRequested.emit(max(us, 0))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        drag = self._drag
        if drag is None:
            return
        us = self.scale.us(event.position().x())
        if drag.target == "seek":
            self.seekRequested.emit(max(us, 0))
            return
        delta = us - drag.origin_us
        limit = self.scale.duration_us * 10
        drag.current = drag_range(*drag.original, drag.part, delta, limit, MIN_SEGMENT_US)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        drag, self._drag = self._drag, None
        timeline = self.timeline
        if (
            drag is None
            or timeline is None
            or drag.current is None
            or drag.current == drag.original
        ):
            self.update()
            return
        if drag.target == "text":
            start, end = drag.current
            self.controller.execute(
                UpdateText(
                    timeline_id=timeline.id,
                    text_id=drag.item_id,
                    start_us=max(start, 0),
                    end_us=end,
                )
            )
        elif drag.target == "segment":
            self._finish_segment_drag(timeline, drag)
        self.update()

    def _finish_segment_drag(self, timeline: EditTimeline, drag: _Drag) -> None:
        assert drag.current is not None
        index = timeline.segment_index(drag.item_id)
        segment = timeline.segments[index]
        if drag.part == "body":
            center = (drag.current[0] + drag.current[1]) // 2
            new_index = insertion_index(segment_spans(timeline), index, center)
            if new_index != index:
                self.controller.execute(
                    MoveSegment(timeline_id=timeline.id, segment_id=segment.id, index=new_index)
                )
            return
        # Trimming: output-time change × speed = source-time change.
        if drag.part == "start":
            change = round((drag.current[0] - drag.original[0]) * segment.speed)
            command = UpdateSegment(
                timeline_id=timeline.id, segment_id=segment.id, in_us=max(segment.in_us + change, 0)
            )
        else:
            change = round((drag.current[1] - drag.original[1]) * segment.speed)
            command = UpdateSegment(
                timeline_id=timeline.id, segment_id=segment.id, out_us=segment.out_us + change
            )
        self.controller.execute(command)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.scale.zoom(1.25 if delta > 0 else 0.8, event.position().x())
        else:
            self.scale.scroll(-delta / 2)
        self.update()
        event.accept()
