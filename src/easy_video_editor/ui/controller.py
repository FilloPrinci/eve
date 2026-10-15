"""The UI's single entry point to the project: commands, undo/redo, selection and autosave.

Widgets never mutate the model: they call `ProjectController.execute` with a command and redraw
when `projectChanged` fires.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from easy_video_editor.analysis.plan import Plan, PlanResult, apply_plan
from easy_video_editor.appsettings import AppSettings, save_app_settings
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.commands import Command, CommandResult
from easy_video_editor.core.errors import CommandError, EveError, NotFoundError
from easy_video_editor.core.model import EditTimeline, Origin, Project
from easy_video_editor.core.persistence import (
    PROJECT_FILE,
    create_project,
    load_project,
    save_project,
)
from easy_video_editor.core.session import HistoryEntry, ProjectSession
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache

log = logging.getLogger(__name__)

AUTOSAVE_DELAY_MS = 800
EXTERNAL_POLL_MS = 300


class SelectionKind(StrEnum):
    ZONE = "zone"
    SEGMENT = "segment"
    TEXT = "text"


@dataclass(frozen=True)
class Selection:
    kind: SelectionKind
    item_id: str
    timeline_id: str | None = None


class ProjectController(QObject):
    projectOpened = Signal()
    projectChanged = Signal()
    selectionChanged = Signal()
    currentClipChanged = Signal()
    currentTimelineChanged = Signal()
    notice = Signal(str)
    """User-facing information (e.g. zones trimmed by a new exclude zone)."""
    error = Signal(str)
    """User-facing error message (e.g. a rejected command)."""
    _changedFromAnyThread = Signal()

    def __init__(self, settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.session: ProjectSession | None = None
        self.project_dir: Path | None = None
        self.selection: Selection | None = None
        self.current_clip_id: str | None = None
        self.current_timeline_id: str | None = None
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(AUTOSAVE_DELAY_MS)
        self._autosave.timeout.connect(self.save)
        self._changedFromAnyThread.connect(self._on_changed, Qt.ConnectionType.QueuedConnection)
        self._external_poll = QTimer(self)
        self._external_poll.setInterval(EXTERNAL_POLL_MS)
        self._external_poll.timeout.connect(self.follow_external)
        self._external_stamp: tuple[int, int] | None = None

    # --- Project lifecycle ---------------------------------------------------------------------

    @property
    def project(self) -> Project | None:
        return self.session.project if self.session else None

    @property
    def cache(self) -> MediaCache | None:
        return MediaCache(self.project_dir) if self.project_dir else None

    def new_project(self, directory: Path, name: str) -> None:
        create_project(directory, Project(name=name))
        self.open_project(directory)

    def open_project(self, directory: Path) -> None:
        self.save()
        project = load_project(directory)
        self.session = ProjectSession(project)
        self.session.subscribe(lambda _: self._notify_changed())
        self.project_dir = directory
        self.selection = None
        self.current_clip_id = project.clips[0].id if project.clips else None
        self.current_timeline_id = project.timelines[0].id if project.timelines else None
        self.settings.add_recent(directory)
        self._save_settings()
        log.info("Opened project %s", directory)
        self.projectOpened.emit()
        self.projectChanged.emit()
        self.currentClipChanged.emit()
        self.currentTimelineChanged.emit()

    def save(self) -> None:
        self._autosave.stop()
        if self.session is None or self.project_dir is None or self.session.external_active:
            return
        try:
            save_project(self.session.project, self.project_dir)
        except OSError as error:
            self.error.emit(
                self.tr("Cannot save the project: {error}").replace("{error}", str(error))
            )

    def _save_settings(self) -> None:
        try:
            save_app_settings(self.settings)
        except OSError as error:
            log.warning("Cannot save settings: %s", error)

    def update_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self._save_settings()
        self.projectChanged.emit()

    # --- Commands ------------------------------------------------------------------------------

    def execute(self, command: Command) -> CommandResult | None:
        """Run a user command. Rejections are reported through `error` and return None."""
        if self.session is None:
            return None
        try:
            result = self.session.execute(command, Origin.USER)
        except CommandError as rejection:
            log.info("Command rejected: %s", rejection)
            self.error.emit(str(rejection))
            return None
        for message in result.notices:
            log.info("%s", message)
            self.notice.emit(message)
        return result

    def apply_plan(self, plan: Plan) -> PlanResult | None:
        """Apply an automation plan as one undo step (origin: auto)."""
        if self.session is None:
            return None
        outcome = apply_plan(self.session, plan, Origin.AUTO)
        for message in outcome.notices:
            self.notice.emit(message)
        if outcome.rejected:
            self.error.emit(
                self.tr("{count} automatic change(s) were rejected; see the log.").replace(
                    "{count}", str(len(outcome.rejected))
                )
            )
        return outcome

    # --- Changes made by another process (the AI) ----------------------------------------------

    @property
    def external_active(self) -> bool:
        return self.session is not None and self.session.external_active

    def begin_external(self, description: str) -> None:
        """Save, then follow the project file while the AI edits it (local editing is paused)."""
        if self.session is None:
            return
        self.save()
        self.session.begin_external(description, Origin.AI)
        self._external_stamp = self._project_stamp()
        self._external_poll.start()

    def follow_external(self, force: bool = False) -> None:
        """Show the AI's latest saved state, if the project file changed."""
        if self.session is None or self.project_dir is None or not self.session.external_active:
            return
        stamp = self._project_stamp()
        if not force and stamp == self._external_stamp:
            return
        self._external_stamp = stamp
        try:
            self.session.set_external(load_project(self.project_dir))
        except EveError as problem:
            log.warning("Ignoring an unreadable project update: %s", problem)

    def end_external(self) -> HistoryEntry | None:
        """Take the final state and record the AI's work as one undo step."""
        if self.session is None or not self.session.external_active:
            return None
        self._external_poll.stop()
        self.follow_external(force=True)
        entry = self.session.end_external()
        self._autosave.start()
        return entry

    def _project_stamp(self) -> tuple[int, int] | None:
        if self.project_dir is None:
            return None
        try:
            info = (self.project_dir / PROJECT_FILE).stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

    def undo(self) -> None:
        if self.session and self.session.can_undo:
            entry = self.session.undo()
            self.notice.emit(self.tr("Undid: {what}").replace("{what}", entry.description))

    def redo(self) -> None:
        if self.session and self.session.can_redo:
            entry = self.session.redo()
            self.notice.emit(self.tr("Redid: {what}").replace("{what}", entry.description))

    def _notify_changed(self) -> None:
        """Refresh at once on the UI thread; changes from worker threads are queued."""
        if QThread.currentThread() is self.thread():
            self._on_changed()
        else:
            self._changedFromAnyThread.emit()

    def _on_changed(self) -> None:
        project = self.project
        if project is None:
            return
        if self.selection and not self._exists(self.selection):
            self.selection = None
            self.selectionChanged.emit()
        if self.current_clip_id and not any(c.id == self.current_clip_id for c in project.clips):
            self.set_current_clip(project.clips[0].id if project.clips else None)
        timeline_ids = [t.id for t in project.timelines]
        if self.current_timeline_id not in timeline_ids:
            self.set_current_timeline(timeline_ids[0] if timeline_ids else None)
        self.projectChanged.emit()
        if not self.external_active:
            self._autosave.start()

    # --- Selection and current items -----------------------------------------------------------

    def _exists(self, selection: Selection) -> bool:
        project = self.project
        if project is None:
            return False
        try:
            if selection.kind is SelectionKind.ZONE:
                project.zone(selection.item_id)
            elif selection.timeline_id is None:
                return False
            elif selection.kind is SelectionKind.SEGMENT:
                project.timeline(selection.timeline_id).segment(selection.item_id)
            else:
                project.timeline(selection.timeline_id).text(selection.item_id)
        except NotFoundError:
            return False
        return True

    def select(self, selection: Selection | None) -> None:
        if selection != self.selection:
            self.selection = selection
            self.selectionChanged.emit()

    def set_current_clip(self, clip_id: str | None) -> None:
        if clip_id != self.current_clip_id:
            self.current_clip_id = clip_id
            self.currentClipChanged.emit()

    def set_current_timeline(self, timeline_id: str | None) -> None:
        if timeline_id != self.current_timeline_id:
            self.current_timeline_id = timeline_id
            self.currentTimelineChanged.emit()

    @property
    def current_timeline(self) -> EditTimeline | None:
        project = self.project
        if project is None or self.current_timeline_id is None:
            return None
        try:
            return project.timeline(self.current_timeline_id)
        except NotFoundError:
            return None

    # --- Environment ---------------------------------------------------------------------------

    def toolchain(self) -> Toolchain:
        settings = self.settings
        return Toolchain.discover(
            Path(settings.ffmpeg_path) if settings.ffmpeg_path else None,
            Path(settings.ffprobe_path) if settings.ffprobe_path else None,
        )

    def brand_kit(self) -> BrandKit | None:
        project = self.project
        if project is None or project.settings.brand_kit_id is None:
            return None
        try:
            return self.settings.brand_kit(project.settings.brand_kit_id)
        except EveError:
            return None
