"""Inspector (SPEC §8.1): edit the selected zone, segment or text. Every edit is a command."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.core.commands import (
    AddSegment,
    Command,
    DeleteZone,
    RemoveSegment,
    RemoveText,
    SetTransition,
    SetZoneLock,
    UpdateSegment,
    UpdateText,
    UpdateZone,
)
from easy_video_editor.core.errors import NotFoundError
from easy_video_editor.core.model import (
    Reframe,
    ReframeMode,
    Transition,
    TransitionKind,
    ZoneKind,
)
from easy_video_editor.core.timecode import format_us
from easy_video_editor.media.render.styles import STYLE_NAMES
from easy_video_editor.ui.controller import ProjectController, Selection, SelectionKind

US = 1_000_000


def seconds_box(maximum: float = 86_400.0, decimals: int = 3, step: float = 0.1) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(0.0, maximum)
    box.setSingleStep(step)
    box.setSuffix(" s")
    box.setKeyboardTracking(False)
    return box


class _Form(QWidget):
    """A form whose widgets are refreshed from the model without echoing edits back."""

    def __init__(self, controller: ProjectController) -> None:
        super().__init__()
        self.controller = controller
        self.layout_ = QFormLayout(self)
        self._loading = False

    def on_edit(self, make_command: Callable[[], Command | None]) -> Callable[..., None]:
        def handler(*_: object) -> None:
            if self._loading:
                return
            command = make_command()
            if command is not None:
                self.controller.execute(command)

        return handler


class ZoneForm(_Form):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__(controller)
        self.zone_id = ""
        self.kind = QLabel()
        self.origin = QLabel()
        self.label = QLineEdit()
        self.start = seconds_box()
        self.end = seconds_box()
        self.priority = QComboBox()
        self.priority.addItems(["—", "★", "★★", "★★★"])
        self.score = QLabel()
        self.locked = QCheckBox(self.tr("Locked (automation keeps away)"))
        self.stale = QLabel()
        to_timeline = QPushButton(self.tr("Add to edit timeline"))
        to_timeline.clicked.connect(self.add_to_timeline)
        self.to_timeline = to_timeline
        delete = QPushButton(self.tr("Delete zone"))
        delete.clicked.connect(lambda: controller.execute(DeleteZone(zone_id=self.zone_id)))
        form = self.layout_
        for name, widget in [
            (self.tr("Kind"), self.kind),
            (self.tr("Origin"), self.origin),
            (self.tr("Label"), self.label),
            (self.tr("Start"), self.start),
            (self.tr("End"), self.end),
            (self.tr("Priority"), self.priority),
            (self.tr("Score"), self.score),
            ("", self.locked),
            ("", self.stale),
        ]:
            form.addRow(name, widget)
        buttons = QHBoxLayout()
        buttons.addWidget(to_timeline)
        buttons.addWidget(delete)
        form.addRow(buttons)
        self.label.editingFinished.connect(
            self.on_edit(lambda: UpdateZone(zone_id=self.zone_id, label=self.label.text()))
        )
        self.start.valueChanged.connect(
            self.on_edit(
                lambda: UpdateZone(zone_id=self.zone_id, start_us=round(self.start.value() * US))
            )
        )
        self.end.valueChanged.connect(
            self.on_edit(
                lambda: UpdateZone(zone_id=self.zone_id, end_us=round(self.end.value() * US))
            )
        )
        self.priority.currentIndexChanged.connect(
            self.on_edit(
                lambda: (
                    UpdateZone(zone_id=self.zone_id, priority=self.priority.currentIndex())
                    if self.priority.currentIndex()
                    else None
                )
            )
        )
        self.locked.toggled.connect(
            self.on_edit(lambda: SetZoneLock(zone_id=self.zone_id, locked=self.locked.isChecked()))
        )

    def load(self, zone_id: str) -> None:
        project = self.controller.project
        if project is None:
            return
        zone = project.zone(zone_id)
        clip = project.clip(zone.clip_id)
        self._loading = True
        self.zone_id = zone.id
        names = {
            ZoneKind.EXCLUDE: self.tr("Exclude (red)"),
            ZoneKind.CANDIDATE: self.tr("Candidate (blue)"),
            ZoneKind.HIGHLIGHT: self.tr("Highlight (yellow)"),
        }
        self.kind.setText(names[zone.kind])
        self.origin.setText(zone.origin.value)
        self.label.setText(zone.label)
        for box in (self.start, self.end):
            box.setMaximum(clip.duration_us / US)
        self.start.setValue(zone.start_us / US)
        self.end.setValue(zone.end_us / US)
        self.priority.setCurrentIndex(zone.priority or 0)
        self.score.setText("—" if zone.score is None else f"{zone.score:.2f}")
        self.locked.setChecked(zone.locked)
        self.locked.setVisible(zone.kind is not ZoneKind.EXCLUDE)
        self.stale.setText(self.tr("Stale: its source changed") if zone.stale else "")
        self.to_timeline.setVisible(zone.kind is ZoneKind.HIGHLIGHT)
        self._loading = False

    def add_to_timeline(self) -> None:
        project, timeline = self.controller.project, self.controller.current_timeline
        if project is None:
            return
        if timeline is None:
            self.controller.error.emit(self.tr("Create an edit timeline first (Timeline menu)."))
            return
        zone = project.zone(self.zone_id)
        self.controller.execute(
            AddSegment(
                timeline_id=timeline.id,
                clip_id=zone.clip_id,
                in_us=zone.start_us,
                out_us=zone.end_us,
                zone_id=zone.id,
            )
        )


class SegmentForm(_Form):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__(controller)
        self.timeline_id = self.segment_id = ""
        self.source = QLabel()
        self.in_point = seconds_box()
        self.out_point = seconds_box()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.1, 10.0)
        self.speed.setSingleStep(0.25)
        self.speed.setSuffix("×")
        self.speed.setKeyboardTracking(False)
        self.transition = QComboBox()
        self.transitions = list(TransitionKind)
        self.transition.addItems(
            [self.tr(kind.value.replace("_", " ")) for kind in self.transitions]
        )
        self.transition_length = seconds_box(maximum=5.0)
        self.reframe = QComboBox()
        self.reframe_modes: list[ReframeMode | None] = [None, *ReframeMode]
        self.reframe.addItems(
            [self.tr("Preset default")] + [self.tr(m.value.replace("_", " ")) for m in ReframeMode]
        )
        self.center_x = QDoubleSpinBox()
        self.center_x.setRange(0.0, 1.0)
        self.center_x.setSingleStep(0.05)
        self.center_x.setKeyboardTracking(False)
        self.game_audio = QDoubleSpinBox()
        self.game_audio.setRange(-60.0, 12.0)
        self.game_audio.setSuffix(" dB")
        self.game_audio.setKeyboardTracking(False)
        self.stale = QLabel()
        remove = QPushButton(self.tr("Remove from timeline"))
        remove.clicked.connect(
            lambda: controller.execute(
                RemoveSegment(timeline_id=self.timeline_id, segment_id=self.segment_id)
            )
        )
        form = self.layout_
        for name, widget in [
            (self.tr("Source"), self.source),
            (self.tr("In"), self.in_point),
            (self.tr("Out"), self.out_point),
            (self.tr("Speed"), self.speed),
            (self.tr("Transition out"), self.transition),
            (self.tr("Transition length"), self.transition_length),
            (self.tr("Reframing"), self.reframe),
            (self.tr("Crop centre"), self.center_x),
            (self.tr("Game audio"), self.game_audio),
            ("", self.stale),
            ("", remove),
        ]:
            form.addRow(name, widget)
        update = self._update
        self.in_point.valueChanged.connect(
            self.on_edit(lambda: update(in_us=round(self.in_point.value() * US)))
        )
        self.out_point.valueChanged.connect(
            self.on_edit(lambda: update(out_us=round(self.out_point.value() * US)))
        )
        self.speed.valueChanged.connect(self.on_edit(lambda: update(speed=self.speed.value())))
        self.game_audio.valueChanged.connect(
            self.on_edit(lambda: update(game_audio_db=self.game_audio.value()))
        )
        self.reframe.currentIndexChanged.connect(self.on_edit(self._reframe_command))
        self.center_x.valueChanged.connect(self.on_edit(self._reframe_command))
        self.transition.currentIndexChanged.connect(self.on_edit(self._transition_command))
        self.transition_length.valueChanged.connect(self.on_edit(self._transition_command))

    def _update(self, **changes: object) -> Command:
        return UpdateSegment.model_validate(
            {"timeline_id": self.timeline_id, "segment_id": self.segment_id, **changes}
        )

    def _reframe_command(self) -> Command:
        mode = self.reframe_modes[self.reframe.currentIndex()]
        return self._update(reframe=Reframe(mode=mode, center_x=self.center_x.value()))

    def _transition_command(self) -> Command:
        kind = self.transitions[self.transition.currentIndex()]
        length = (
            0
            if kind is TransitionKind.CUT
            else max(round(self.transition_length.value() * US), 40_000)
        )
        return SetTransition(
            timeline_id=self.timeline_id,
            segment_id=self.segment_id,
            transition=Transition(kind=kind, duration_us=length),
        )

    def load(self, timeline_id: str, segment_id: str) -> None:
        project = self.controller.project
        if project is None:
            return
        segment = project.timeline(timeline_id).segment(segment_id)
        clip = project.clip(segment.clip_id)
        self._loading = True
        self.timeline_id, self.segment_id = timeline_id, segment_id
        self.source.setText(f"{clip.name} ({format_us(segment.in_us)}–{format_us(segment.out_us)})")
        for box in (self.in_point, self.out_point):
            box.setMaximum(clip.duration_us / US)
        self.in_point.setValue(segment.in_us / US)
        self.out_point.setValue(segment.out_us / US)
        self.speed.setValue(segment.speed)
        self.transition.setCurrentIndex(self.transitions.index(segment.transition_out.kind))
        self.transition_length.setValue(segment.transition_out.duration_us / US or 0.4)
        self.reframe.setCurrentIndex(self.reframe_modes.index(segment.reframe.mode))
        self.center_x.setValue(segment.reframe.center_x)
        self.game_audio.setValue(segment.game_audio_db)
        self.stale.setText(self.tr("Stale: its highlight changed") if segment.stale else "")
        self._loading = False


class TextForm(_Form):
    def __init__(self, controller: ProjectController) -> None:
        super().__init__(controller)
        self.timeline_id = self.text_id = ""
        self.text = QLineEdit()
        self.style_box = QComboBox()
        self.style_box.addItems(list(STYLE_NAMES))
        self.start = seconds_box()
        self.end = seconds_box()
        self.x_box = QSpinBox()
        self.y_box = QSpinBox()
        for box in (self.x_box, self.y_box):
            box.setRange(0, 100)
            box.setSuffix(" %")
            box.setKeyboardTracking(False)
        remove = QPushButton(self.tr("Remove text"))
        remove.clicked.connect(
            lambda: controller.execute(
                RemoveText(timeline_id=self.timeline_id, text_id=self.text_id)
            )
        )
        form = self.layout_
        for name, widget in [
            (self.tr("Text"), self.text),
            (self.tr("Style"), self.style_box),
            (self.tr("Start"), self.start),
            (self.tr("End"), self.end),
            (self.tr("Horizontal position"), self.x_box),
            (self.tr("Vertical position"), self.y_box),
            ("", remove),
        ]:
            form.addRow(name, widget)
        update = self._update
        self.text.editingFinished.connect(
            self.on_edit(lambda: update(text=self.text.text() or None))
        )
        self.style_box.currentTextChanged.connect(
            self.on_edit(lambda: update(style=self.style_box.currentText()))
        )
        self.start.valueChanged.connect(
            self.on_edit(lambda: update(start_us=round(self.start.value() * US)))
        )
        self.end.valueChanged.connect(
            self.on_edit(lambda: update(end_us=round(self.end.value() * US)))
        )
        self.x_box.valueChanged.connect(self.on_edit(lambda: update(x=self.x_box.value() / 100)))
        self.y_box.valueChanged.connect(self.on_edit(lambda: update(y=self.y_box.value() / 100)))

    def _update(self, **changes: object) -> Command:
        return UpdateText.model_validate(
            {"timeline_id": self.timeline_id, "text_id": self.text_id, **changes}
        )

    def load(self, timeline_id: str, text_id: str) -> None:
        project = self.controller.project
        if project is None:
            return
        text = project.timeline(timeline_id).text(text_id)
        self._loading = True
        self.timeline_id, self.text_id = timeline_id, text_id
        self.text.setText(text.text)
        self.style_box.setCurrentText(text.style)
        self.start.setValue(text.start_us / US)
        self.end.setValue(text.end_us / US)
        self.x_box.setValue(round(text.x * 100))
        self.y_box.setValue(round(text.y * 100))
        self._loading = False


class Inspector(QWidget):
    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.empty = QLabel(self.tr("Select a zone, a segment or a text to edit it."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.zone_form = ZoneForm(controller)
        self.segment_form = SegmentForm(controller)
        self.text_form = TextForm(controller)
        self.stack = QStackedWidget()
        for widget in (self.empty, self.zone_form, self.segment_form, self.text_form):
            self.stack.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)
        layout.addStretch(1)
        controller.selectionChanged.connect(self.refresh)
        controller.projectChanged.connect(self.refresh)

    def refresh(self) -> None:
        selection: Selection | None = self.controller.selection
        try:
            if selection is None:
                self.stack.setCurrentWidget(self.empty)
            elif selection.kind is SelectionKind.ZONE:
                self.zone_form.load(selection.item_id)
                self.stack.setCurrentWidget(self.zone_form)
            elif selection.kind is SelectionKind.SEGMENT and selection.timeline_id:
                self.segment_form.load(selection.timeline_id, selection.item_id)
                self.stack.setCurrentWidget(self.segment_form)
            elif selection.kind is SelectionKind.TEXT and selection.timeline_id:
                self.text_form.load(selection.timeline_id, selection.item_id)
                self.stack.setCurrentWidget(self.text_form)
        except NotFoundError:
            self.stack.setCurrentWidget(self.empty)
