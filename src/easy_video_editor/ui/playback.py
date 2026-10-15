"""Playback panel: source, instant edit preview (EDL) and draft renders (SPEC §6.2)."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.core.jobs import Job, JobContext
from easy_video_editor.core.model import Clip, EditTimeline, OutputPreset, Project
from easy_video_editor.core.timecode import format_us
from easy_video_editor.media.preview import (
    EdlEntry,
    edl_entries,
    edl_time_to_output_us,
    edl_url,
    output_us_to_edl_time,
)
from easy_video_editor.media.render.build import RenderOptions
from easy_video_editor.media.render.renderer import ValidationFailed, render_timeline
from easy_video_editor.media.render.styles import DEFAULT_PRIMARY_COLOR, TEXT_LOOKS, resolve_color
from easy_video_editor.ui.controller import ProjectController
from easy_video_editor.ui.jobs import JobRunner
from easy_video_editor.ui.player import OverlayText, PlayerHost
from easy_video_editor.ui.source_view import SourceView
from easy_video_editor.ui.timeline_view import TimelineView

log = logging.getLogger(__name__)

US = 1_000_000
DRAFT_DELAY_MS = 1500
RELOAD_DELAY_MS = 300


class Mode(StrEnum):
    SOURCE = "source"
    TIMELINE = "timeline"
    DRAFT = "draft"


def preset_for(project: Project, timeline: EditTimeline) -> OutputPreset | None:
    presets = [p for p in project.settings.output_presets if p.aspect == timeline.aspect]
    enabled = [p for p in presets if p.id in project.settings.enabled_preset_ids]
    candidates = enabled or presets
    return candidates[0] if candidates else None


def aspect_value(aspect: str) -> float:
    width, _, height = aspect.partition(":")
    return int(width) / int(height)


class DraftRenderer(QObject):
    """Renders a draft of the current timeline in the background, debounced after changes."""

    draftReady = Signal(str)

    def __init__(self, controller: ProjectController, runner: JobRunner) -> None:
        super().__init__()
        self.controller = controller
        self.runner = runner
        self.auto = False
        self._job: Job | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DRAFT_DELAY_MS)
        self._timer.timeout.connect(self.render_now)
        controller.projectChanged.connect(self._on_change)

    def _on_change(self) -> None:
        if self.auto:
            self._timer.start()

    def draft_path(self, timeline_id: str) -> Path | None:
        if self.controller.project_dir is None:
            return None
        return self.controller.project_dir / "cache" / "draft" / f"{timeline_id}.mp4"

    def render_now(self) -> None:
        project, timeline = self.controller.project, self.controller.current_timeline
        project_dir = self.controller.project_dir
        if project is None or timeline is None or project_dir is None or not timeline.segments:
            return
        preset = preset_for(project, timeline)
        output = self.draft_path(timeline.id)
        if preset is None or output is None:
            return
        if self._job is not None and not self._job.state.finished:
            self.runner.cancel(self._job.id)
        snapshot = project.model_copy(deep=True)
        options = RenderOptions(brand=self.controller.brand_kit())

        def work(ctx: JobContext) -> str:
            try:
                render_timeline(
                    snapshot,
                    project_dir,
                    preset,
                    output,
                    self.controller.toolchain(),
                    options=options,
                    draft=True,
                    progress=ctx.progress,
                    token=ctx.token,
                )
            except ValidationFailed as error:
                raise RuntimeError(f"Draft not updated: {error}") from error
            return str(output)

        self._job = self.runner.run(
            self.tr("Draft preview"),
            work,
            on_success=self.draftReady.emit,
            on_error=lambda message: self.controller.notice.emit(message),
        )


class PlaybackPanel(QWidget):
    def __init__(
        self,
        controller: ProjectController,
        runner: JobRunner,
        host: PlayerHost,
        source_view: SourceView,
        timeline_view: TimelineView,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.host = host
        self.view = host.view
        self.source_view = source_view
        self.timeline_view = timeline_view
        self.mode = Mode.SOURCE
        self.entries: list[EdlEntry] = []
        self.loaded_url = ""
        self.draft = DraftRenderer(controller, runner)
        self.draft.draftReady.connect(self._on_draft_ready)

        self.buttons = QButtonGroup(self)
        self.mode_buttons: dict[Mode, QPushButton] = {}
        bar = QHBoxLayout()
        for mode, label in (
            (Mode.SOURCE, self.tr("Source")),
            (Mode.TIMELINE, self.tr("Edit")),
            (Mode.DRAFT, self.tr("Draft")),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(mode is self.mode)
            button.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.buttons.addButton(button)
            self.mode_buttons[mode] = button
            bar.addWidget(button)
        self.play = QPushButton("▶")
        self.play.setFixedWidth(40)
        self.play.clicked.connect(self.view.toggle)
        self.time = QLabel("00:00.000")
        render_draft = QPushButton(self.tr("Render draft"))
        render_draft.clicked.connect(self.draft.render_now)
        self.auto_draft = QCheckBox(self.tr("Auto"))
        self.auto_draft.setToolTip(self.tr("Re-render the draft automatically after changes"))
        self.auto_draft.toggled.connect(lambda on: setattr(self.draft, "auto", on))
        bar.addSpacing(12)
        bar.addWidget(self.play)
        bar.addWidget(self.time)
        bar.addStretch(1)
        bar.addWidget(render_draft)
        bar.addWidget(self.auto_draft)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(host, 1)
        layout.addLayout(bar)

        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.setInterval(RELOAD_DELAY_MS)
        self._reload.timeout.connect(self.reload)
        self.view.positionChanged.connect(self._on_position)
        self.view.pausedChanged.connect(lambda paused: self.play.setText("▶" if paused else "❚❚"))
        source_view.seekRequested.connect(self.seek_source)
        timeline_view.seekRequested.connect(self.seek_output)
        controller.currentClipChanged.connect(self._on_clip_changed)
        controller.currentTimelineChanged.connect(self._schedule_reload)
        controller.projectChanged.connect(self._on_project_changed)

    # --- Media selection -----------------------------------------------------------------------

    def media_path(self, clip: Clip) -> Path:
        cache = self.controller.cache
        if cache is not None:
            proxy = cache.proxy_path(clip, self.controller.settings.proxy_height)
            if proxy.is_file():
                return proxy
        return clip.file_path

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        self.loaded_url = ""
        self.reload()

    def _on_clip_changed(self) -> None:
        if self.mode is Mode.SOURCE:
            self.loaded_url = ""
            self.reload()

    def _on_project_changed(self) -> None:
        if self.mode is Mode.TIMELINE:
            self._schedule_reload()
        self.host.refresh_overlay()

    def _schedule_reload(self) -> None:
        self._reload.start()

    def reload(self) -> None:
        project = self.controller.project
        self.view.badge = None
        self.view.overlays = None
        if project is None:
            return
        if self.mode is Mode.SOURCE:
            clip_id = self.controller.current_clip_id
            if clip_id is None:
                return
            clip = project.clip(clip_id)
            if clip.width and clip.height:
                self.view.aspect = clip.width / clip.height
            self._load(str(self.media_path(clip)), self.source_view.playhead_us / US)
            return
        timeline = self.controller.current_timeline
        if timeline is None:
            return
        self.view.aspect = aspect_value(timeline.aspect)
        if self.mode is Mode.TIMELINE:
            self.entries = edl_entries(project, timeline, self.media_path)
            self.view.overlays = self._timeline_overlays
            if self.entries:
                start = output_us_to_edl_time(self.entries, self.timeline_view.playhead_us)
                self._load(edl_url(self.entries), start)
            return
        self.view.badge = self.tr("DRAFT")
        path = self.draft.draft_path(timeline.id)
        if path is not None and path.is_file():
            self._load(str(path), self.timeline_view.playhead_us / US)
        else:
            self.controller.notice.emit(self.tr("No draft yet: click “Render draft”."))
        self.host.refresh_overlay()

    def _load(self, url: str, start: float) -> None:
        if url != self.loaded_url:
            self.loaded_url = url
            self.view.load(url, start)

    def _on_draft_ready(self, path: str) -> None:
        if self.mode is Mode.DRAFT:
            self.loaded_url = ""
            self.reload()
        self.controller.notice.emit(self.tr("Draft preview updated."))

    # --- Position ------------------------------------------------------------------------------

    def _on_position(self, seconds: float) -> None:
        if self.mode is Mode.SOURCE:
            us = round(seconds * US)
            self.source_view.set_playhead(us)
        else:
            us = (
                edl_time_to_output_us(self.entries, seconds)
                if self.mode is Mode.TIMELINE
                else round(seconds * US)
            )
            self.timeline_view.set_playhead(us)
        self.time.setText(format_us(us))
        self.host.refresh_overlay()

    def seek_source(self, us: int) -> None:
        if self.mode is not Mode.SOURCE:
            self.set_mode(Mode.SOURCE)
        self.source_view.set_playhead(us)
        self.view.seek(us / US)

    def seek_output(self, us: int) -> None:
        if self.mode is Mode.SOURCE:
            self.set_mode(Mode.TIMELINE)
        self.timeline_view.set_playhead(us)
        seconds = output_us_to_edl_time(self.entries, us) if self.mode is Mode.TIMELINE else us / US
        self.view.seek(seconds)

    def _timeline_overlays(self, seconds: float) -> list[OverlayText]:
        """Approximate text overlays for the instant preview (the draft shows the real ones)."""
        project, timeline = self.controller.project, self.controller.current_timeline
        if project is None or timeline is None:
            return []
        output_us = edl_time_to_output_us(self.entries, seconds)
        kit = self.controller.brand_kit()
        primary = kit.primary_color if kit else DEFAULT_PRIMARY_COLOR
        styles = TEXT_LOOKS[project.settings.text_look]
        texts = []
        for text in timeline.texts:
            if text.start_us <= output_us < text.end_us and text.style in styles:
                style = styles[text.style]
                color = resolve_color(
                    style.color or (kit.text_color if kit else "#FFFFFF"), primary
                )
                box = resolve_color(style.box or style.band or "", primary) or None
                texts.append(
                    OverlayText(
                        text.text,
                        style.size,
                        text.x,
                        text.y,
                        color.split("@")[0],
                        box.split("@")[0] if box else None,
                    )
                )
        return texts
