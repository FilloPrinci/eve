"""The EVE main window (SPEC §8.1): docks, menus, keyboard shortcuts and status bar."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor import __version__
from easy_video_editor.ai.claude_code import find_claude
from easy_video_editor.analysis.pipeline import Phase
from easy_video_editor.appsettings import AppSettings
from easy_video_editor.core.commands import (
    AddSegment,
    AddText,
    CreateTimeline,
    DeleteZone,
    RemoveSegment,
    RemoveText,
    SetEndCard,
)
from easy_video_editor.core.errors import EveError
from easy_video_editor.core.jobs import JobManager
from easy_video_editor.core.model import ZoneKind
from easy_video_editor.core.persistence import PROJECT_FILE, project_dir_for
from easy_video_editor.media.doctor import run_checks
from easy_video_editor.ui.automation_panel import ALL_PHASES, AutomationPanel
from easy_video_editor.ui.controller import ProjectController, Selection, SelectionKind
from easy_video_editor.ui.export_dialog import ExportDialog
from easy_video_editor.ui.history_panel import HistoryPanel
from easy_video_editor.ui.inspector import Inspector
from easy_video_editor.ui.jobs import JobRunner, JobsPanel
from easy_video_editor.ui.log_console import LogConsole, QtLogHandler
from easy_video_editor.ui.media_bin import MediaBin
from easy_video_editor.ui.playback import Mode, PlaybackPanel
from easy_video_editor.ui.player import PlayerHost, create_view
from easy_video_editor.ui.settings_panel import AppSettingsDialog, ProjectSettingsPanel
from easy_video_editor.ui.source_view import SourceView
from easy_video_editor.ui.system_check import SystemCheckDialog
from easy_video_editor.ui.timeline_view import TimelineView

log = logging.getLogger(__name__)

US = 1_000_000
STATUS_TIMEOUT_MS = 8000
SHUTTLE_SPEEDS = (1.0, 2.0, 4.0, 8.0)


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: AppSettings | None = None,
        log_handler: QtLogHandler | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self.resize(1440, 900)
        self.controller = ProjectController(settings or AppSettings(), self)
        self.jobs = JobManager(max_workers=2)
        self.runner = JobRunner(self.jobs, self)
        self.log_handler = log_handler or QtLogHandler()
        self._shuttle = 0

        # Central area: player, source view, edit timeline.
        libmpv = self.controller.settings.libmpv_path
        self.player_host = PlayerHost(create_view(Path(libmpv) if libmpv else None))
        self.source_view = SourceView(self.controller)
        self.timeline_view = TimelineView(self.controller)
        self.playback = PlaybackPanel(
            self.controller, self.runner, self.player_host, self.source_view, self.timeline_view
        )
        timeline_area = QWidget()
        timeline_layout = QVBoxLayout(timeline_area)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.addLayout(self._timeline_toolbar())
        timeline_layout.addWidget(self.timeline_view)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.playback)
        splitter.addWidget(self.source_view)
        splitter.addWidget(timeline_area)
        for index, stretch in enumerate((1, 0, 0)):
            splitter.setStretchFactor(index, stretch)
        # The source view and the timeline have a fixed height; extra space goes to the player.
        self.source_view.setFixedHeight(self.source_view.minimumHeight())
        self.timeline_view.setFixedHeight(self.timeline_view.minimumHeight())
        timeline_area.setMaximumHeight(timeline_area.sizeHint().height())
        self.setCentralWidget(splitter)

        # Docks.
        self.media_bin = MediaBin(self.controller, self.runner)
        self._dock(
            self.tr("Media"), "MediaDock", self.media_bin, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.settings_panel = ProjectSettingsPanel(self.controller)
        self.inspector = Inspector(self.controller)
        self.history = HistoryPanel(self.controller)
        self.jobs_panel = JobsPanel(self.runner)
        self.automation = AutomationPanel(self.controller, self.runner)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.settings_panel, self.tr("Settings"))
        self.tabs.addTab(self.inspector, self.tr("Inspector"))
        self.tabs.addTab(self.automation, self.tr("Agent"))
        self.tabs.addTab(self.history, self.tr("History"))
        self.tabs.addTab(self.jobs_panel, self.tr("Jobs"))
        self._dock(
            self.tr("Panels"), "PanelsDock", self.tabs, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.log_console = LogConsole(self.log_handler)
        self.log_dock = self._dock(
            self.tr("Log"), "LogDock", self.log_console, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.log_dock.hide()

        # Status bar with an activity indicator.
        self.activity = QProgressBar()
        self.activity.setRange(0, 0)
        self.activity.setMaximumWidth(120)
        self.activity.setMaximumHeight(14)
        self.activity.hide()
        self.statusBar().addPermanentWidget(self.activity)
        self.runner.jobChanged.connect(lambda _: self.activity.setVisible(self.runner.busy))

        self._menus()
        self._shortcuts()
        self.controller.notice.connect(
            lambda text: self.statusBar().showMessage(text, STATUS_TIMEOUT_MS)
        )
        self.controller.error.connect(self.show_error)
        self.controller.projectOpened.connect(self._on_project_opened)
        self.controller.projectChanged.connect(self._refresh_timeline_choice)
        self.controller.currentTimelineChanged.connect(self._refresh_timeline_choice)
        self.controller.selectionChanged.connect(self._show_inspector)
        self._update_title()
        self.statusBar().showMessage(self.tr("Create or open a project to start (File menu)."))

    # --- Layout helpers ------------------------------------------------------------------------

    def _dock(self, title: str, name: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _timeline_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.timeline_choice = QComboBox()
        self.timeline_choice.currentIndexChanged.connect(
            lambda _: self.controller.set_current_timeline(self.timeline_choice.currentData())
        )
        new_timeline = QPushButton(self.tr("New timeline…"))
        new_timeline.clicked.connect(self.new_timeline)
        add_range = QPushButton(self.tr("Add marked range"))
        add_range.clicked.connect(self.add_marked_range)
        add_text = QPushButton(self.tr("Add text"))
        add_text.clicked.connect(self.add_text)
        bar.addWidget(QLabel(self.tr("Edit timeline:")))
        bar.addWidget(self.timeline_choice)
        bar.addWidget(new_timeline)
        bar.addStretch(1)
        bar.addWidget(add_range)
        bar.addWidget(add_text)
        return bar

    def _menus(self) -> None:
        def action(menu, text, slot, shortcut=None) -> QAction:  # type: ignore[no-untyped-def]
            item = QAction(text, self)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            item.triggered.connect(slot)
            menu.addAction(item)
            return item

        file = self.menuBar().addMenu(self.tr("&File"))
        action(file, self.tr("New project…"), self.new_project, QKeySequence.StandardKey.New)
        action(
            file, self.tr("Open project…"), self.open_project_dialog, QKeySequence.StandardKey.Open
        )
        self.recent_menu = file.addMenu(self.tr("Open recent"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        action(file, self.tr("Save"), self.controller.save, QKeySequence.StandardKey.Save)
        file.addSeparator()
        action(file, self.tr("Import clips…"), self.media_bin.choose_files, "Ctrl+I")
        action(file, self.tr("Export…"), self.export, "Ctrl+E")
        file.addSeparator()
        action(file, self.tr("Preferences…"), self.preferences)
        action(file, self.tr("Quit"), self.close, QKeySequence.StandardKey.Quit)

        edit = self.menuBar().addMenu(self.tr("&Edit"))
        action(edit, self.tr("Undo"), self.controller.undo, "Ctrl+Z")
        action(edit, self.tr("Redo"), self.controller.redo, "Ctrl+Shift+Z")
        action(edit, self.tr("Delete selection"), self.delete_selection, "Del")

        clip = self.menuBar().addMenu(self.tr("&Clip"))
        action(clip, self.tr("Mark in"), self.source_view.set_mark_in, "I")
        action(clip, self.tr("Mark out"), self.source_view.set_mark_out, "O")
        action(
            clip,
            self.tr("Exclude zone from marks"),
            lambda: self.source_view.zone_from_marks(ZoneKind.EXCLUDE),
            "R",
        )
        action(
            clip,
            self.tr("Candidate zone from marks"),
            lambda: self.source_view.zone_from_marks(ZoneKind.CANDIDATE),
            "B",
        )
        action(
            clip,
            self.tr("Highlight zone from marks"),
            lambda: self.source_view.zone_from_marks(ZoneKind.HIGHLIGHT),
            "Y",
        )
        snap = action(
            clip,
            self.tr("Snap to zone edges"),
            lambda on: setattr(self.source_view, "snap_to_zones", on),
        )
        snap.setCheckable(True)
        snap.setChecked(True)

        timeline = self.menuBar().addMenu(self.tr("&Timeline"))
        action(timeline, self.tr("New timeline…"), self.new_timeline)
        action(timeline, self.tr("Add marked range"), self.add_marked_range, "A")
        action(timeline, self.tr("Add text"), self.add_text, "T")
        action(timeline, self.tr("Render draft"), self.playback.draft.render_now, "Ctrl+D")

        auto = self.menuBar().addMenu(self.tr("&Auto"))
        action(auto, self.tr("Phase 1: find candidates"), lambda: self.run_phases([Phase.ANALYZE]))
        action(auto, self.tr("Phase 2: refine highlights"), lambda: self.run_phases([Phase.REFINE]))
        action(
            auto, self.tr("Phase 3: assemble the edit"), lambda: self.run_phases([Phase.ASSEMBLE])
        )
        action(auto, self.tr("Run all phases"), lambda: self.run_phases(list(ALL_PHASES)))
        action(auto, self.tr("Refresh stale"), lambda: self.run_phases([Phase.REFRESH]))

        view = self.menuBar().addMenu(self.tr("&View"))
        log_toggle = self.log_dock.toggleViewAction()
        log_toggle.setShortcut(QKeySequence("F12"))
        view.addAction(log_toggle)
        for dock in self.findChildren(QDockWidget):
            if dock is not self.log_dock:
                view.addAction(dock.toggleViewAction())

        help_menu = self.menuBar().addMenu(self.tr("&Help"))
        action(help_menu, self.tr("System check…"), self.system_check)
        action(help_menu, self.tr("About EVE"), self.about)

    def _shortcuts(self) -> None:
        bindings = {
            "Space": self.player_host.view.toggle,
            "K": self.shuttle_stop,
            "L": lambda: self.shuttle(+1),
            "J": lambda: self.shuttle(-1),
            "Left": lambda: self.player_host.view.frame_step(-1),
            "Right": lambda: self.player_host.view.frame_step(+1),
        }
        for key, slot in bindings.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(slot)

    # --- Project -------------------------------------------------------------------------------

    def _update_title(self) -> None:
        project = self.controller.project
        name = f"{project.name} — " if project else ""
        self.setWindowTitle(f"{name}EVE — Easy Video Editor {__version__}")

    def _on_project_opened(self) -> None:
        self._update_title()
        self.media_bin.refresh()
        self.statusBar().showMessage(self.tr("Project opened."), STATUS_TIMEOUT_MS)

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, self.tr("New project"), self.tr("Project name:"))
        if not ok or not name.strip():
            return
        folder = QFileDialog.getExistingDirectory(self, self.tr("Where to create the project"))
        if not folder:
            return
        try:
            self.controller.new_project(project_dir_for(Path(folder), name.strip()), name.strip())
        except EveError as error:
            self.show_error(str(error), dialog=True)

    def open_project_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.tr("Open project (.eve folder)"))
        if folder:
            self.open_project(Path(folder))

    def open_project(self, folder: Path) -> None:
        try:
            self.controller.open_project(folder)
        except EveError as error:
            self.show_error(str(error), dialog=True)

    def reopen_last_project(self) -> None:
        """Open the most recent project, if it still exists (start-up)."""
        recent = self.controller.settings.recent_projects
        if recent and (Path(recent[0]) / PROJECT_FILE).is_file():
            self.open_project(Path(recent[0]))
        else:
            self.statusBar().showMessage(
                self.tr("Create a project (File → New project…) or open one to start."),
                STATUS_TIMEOUT_MS,
            )

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for path in self.controller.settings.recent_projects:
            item = self.recent_menu.addAction(path)
            item.triggered.connect(lambda _=False, p=path: self.open_project(Path(p)))
        if not self.controller.settings.recent_projects:
            self.recent_menu.addAction(self.tr("No recent projects")).setEnabled(False)

    def preferences(self) -> None:
        dialog = AppSettingsDialog(self.controller.settings, self)
        if dialog.exec():
            self.controller.update_settings(dialog.settings)
            self.automation.update_mode()

    def export(self) -> None:
        if self.controller.project is None:
            self.show_error(self.tr("Open a project first."))
            return
        ExportDialog(self.controller, self.runner, self).exec()

    def system_check(self) -> None:
        settings = self.controller.settings
        checks = run_checks(
            Path(settings.ffmpeg_path) if settings.ffmpeg_path else None,
            Path(settings.ffprobe_path) if settings.ffprobe_path else None,
            Path(settings.libmpv_path) if settings.libmpv_path else None,
            find_claude(Path(settings.ai.claude_path) if settings.ai.claude_path else None),
        )
        SystemCheckDialog(checks, self).exec()
        if not settings.system_check_done:
            settings.system_check_done = True
            self.controller.update_settings(settings)

    def about(self) -> None:
        QMessageBox.about(
            self,
            self.tr("About EVE"),
            self.tr(
                "EVE — Easy Video Editor {version}\n\nTurns gameplay recordings into trailers.\n"
                "Licensed under GPL-3.0-or-later."
            ).replace("{version}", __version__),
        )

    # --- Timeline ------------------------------------------------------------------------------

    def _refresh_timeline_choice(self) -> None:
        project = self.controller.project
        self.timeline_choice.blockSignals(True)
        self.timeline_choice.clear()
        if project is not None:
            for timeline in project.timelines:
                self.timeline_choice.addItem(f"{timeline.name} ({timeline.aspect})", timeline.id)
            index = self.timeline_choice.findData(self.controller.current_timeline_id)
            self.timeline_choice.setCurrentIndex(max(index, 0))
        self.timeline_choice.blockSignals(False)

    def new_timeline(self) -> None:
        project = self.controller.project
        if project is None:
            return
        existing = {t.aspect for t in project.timelines}
        options = sorted({p.aspect for p in project.settings.output_presets} - existing)
        if not options:
            self.show_error(self.tr("Every output format already has a timeline."))
            return
        aspect, ok = QInputDialog.getItem(
            self, self.tr("New timeline"), self.tr("Aspect ratio:"), options, 0, False
        )
        if ok:
            result = self.controller.execute(CreateTimeline(aspect=aspect))
            if result:
                self.controller.set_current_timeline(result.created_ids[0])

    def add_marked_range(self) -> None:
        clip, timeline = self.source_view.clip, self.controller.current_timeline
        marks = self.source_view.mark_in, self.source_view.mark_out
        if clip is None or timeline is None or None in marks:
            self.show_error(
                self.tr("Select a clip, set mark-in/out (I/O) and create a timeline first.")
            )
            return
        start, end = marks
        assert start is not None and end is not None
        self.controller.execute(
            AddSegment(timeline_id=timeline.id, clip_id=clip.id, in_us=start, out_us=end)
        )

    def add_text(self) -> None:
        timeline = self.controller.current_timeline
        if timeline is None:
            self.show_error(self.tr("Create an edit timeline first."))
            return
        text, ok = QInputDialog.getText(self, self.tr("Add text"), self.tr("Text:"))
        if ok and text.strip():
            start = self.timeline_view.playhead_us
            result = self.controller.execute(
                AddText(
                    timeline_id=timeline.id,
                    text=text.strip(),
                    start_us=start,
                    end_us=start + 2 * US,
                )
            )
            if result:
                self.controller.select(
                    Selection(SelectionKind.TEXT, result.created_ids[0], timeline.id)
                )

    def delete_selection(self) -> None:
        selection = self.controller.selection
        if selection is None:
            return
        if selection.kind is SelectionKind.ZONE:
            self.controller.execute(DeleteZone(zone_id=selection.item_id))
        elif selection.kind is SelectionKind.SEGMENT and selection.timeline_id:
            self.controller.execute(
                RemoveSegment(timeline_id=selection.timeline_id, segment_id=selection.item_id)
            )
        elif selection.kind is SelectionKind.TEXT and selection.timeline_id:
            self.controller.execute(
                RemoveText(timeline_id=selection.timeline_id, text_id=selection.item_id)
            )
        elif selection.kind is SelectionKind.END_CARD and self.controller.project:
            timeline = self.controller.project.timeline(selection.item_id)
            card = timeline.end_card.model_copy(update={"enabled": False})
            self.controller.execute(SetEndCard(timeline_id=timeline.id, end_card=card))

    def _show_inspector(self) -> None:
        if self.controller.selection is not None:
            self.tabs.setCurrentWidget(self.inspector)

    def run_phases(self, phases: list[Phase]) -> None:
        if self.controller.project is None:
            self.show_error(self.tr("Open a project first."))
            return
        self.tabs.setCurrentWidget(self.automation)
        self.automation.run(phases)

    # --- Playback ------------------------------------------------------------------------------

    def shuttle(self, direction: int) -> None:
        """J/L shuttle: each press speeds up in that direction; K stops."""
        if self._shuttle * direction > 0:
            self._shuttle = direction * min(abs(self._shuttle) + 1, len(SHUTTLE_SPEEDS))
        else:
            self._shuttle = direction
        self.player_host.view.set_speed(direction * SHUTTLE_SPEEDS[abs(self._shuttle) - 1])

    def shuttle_stop(self) -> None:
        self._shuttle = 0
        self.player_host.view.set_speed(0)

    # --- Messages and shutdown -----------------------------------------------------------------

    def show_error(self, message: str, dialog: bool = False) -> None:
        log.warning("%s", message)
        self.statusBar().showMessage("⚠ " + message, STATUS_TIMEOUT_MS)
        if dialog:
            QMessageBox.warning(self, self.tr("EVE"), message)

    def maybe_first_run_check(self) -> None:
        if not self.controller.settings.system_check_done:
            QTimer.singleShot(300, self.system_check)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.controller.save()
        self.jobs.shutdown(cancel=True)
        view = self.player_host.view
        if hasattr(view, "shutdown"):
            view.shutdown()
        super().closeEvent(event)

    def set_playback_mode(self, mode: Mode) -> None:
        self.playback.set_mode(mode)
