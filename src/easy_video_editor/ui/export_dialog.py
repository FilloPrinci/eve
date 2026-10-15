"""Export dialog (SPEC §4.4 rule 5, §8): validate, then render every selected preset."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.analysis.follow import follow_paths
from easy_video_editor.core.jobs import JobContext
from easy_video_editor.core.model import OutputPreset
from easy_video_editor.core.validation import Issue, Severity, validate_for_render
from easy_video_editor.media.cache import MediaCache
from easy_video_editor.media.render.build import RenderError, RenderOptions
from easy_video_editor.media.render.renderer import render_timeline, timeline_for
from easy_video_editor.ui.controller import ProjectController, Selection, SelectionKind
from easy_video_editor.ui.jobs import JobRunner


class ExportDialog(QDialog):
    def __init__(
        self, controller: ProjectController, runner: JobRunner, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.runner = runner
        self.setWindowTitle(self.tr("Export"))
        self.resize(560, 420)
        project = controller.project
        assert project is not None and controller.project_dir is not None
        presets_box = QGroupBox(self.tr("Formats"))
        presets_layout = QVBoxLayout(presets_box)
        self.checks: list[tuple[OutputPreset, QCheckBox]] = []
        for preset in project.settings.output_presets:
            check = QCheckBox(f"{preset.name} ({preset.width}×{preset.height})")
            check.setChecked(preset.id in project.settings.enabled_preset_ids)
            check.toggled.connect(self.validate)
            presets_layout.addWidget(check)
            self.checks.append((preset, check))
        self.folder = QLineEdit(str(controller.project_dir / "exports"))
        browse = QPushButton(self.tr("Browse…"))
        browse.clicked.connect(self.browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(self.tr("Folder")))
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        self.draft = QCheckBox(self.tr("Draft quality (fast, low resolution)"))
        self.issues = QListWidget()
        self.issues.itemDoubleClicked.connect(self.select_issue)
        self.summary = QLabel()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.export_button = self.buttons.addButton(
            self.tr("Export"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.accepted.connect(self.export)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(presets_box)
        layout.addLayout(folder_row)
        layout.addWidget(self.draft)
        layout.addWidget(self.summary)
        layout.addWidget(self.issues, 1)
        layout.addWidget(self.buttons)
        self.validate()

    def selected(self) -> list[OutputPreset]:
        return [preset for preset, check in self.checks if check.isChecked()]

    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Export folder"), self.folder.text()
        )
        if folder:
            self.folder.setText(folder)

    def validate(self) -> bool:
        project = self.controller.project
        assert project is not None
        self.issues.clear()
        errors = 0
        for preset in self.selected():
            try:
                report = validate_for_render(project, timeline_for(project, preset), preset)
                issues = report.issues
            except RenderError as error:
                issues = [Issue(Severity.ERROR, "no_timeline", str(error))]
            for issue in issues:
                symbol = "✖" if issue.severity is Severity.ERROR else "⚠"
                item = QListWidgetItem(f"{symbol} {preset.name}: {issue.message}")
                item.setData(Qt.ItemDataRole.UserRole, issue)
                self.issues.addItem(item)
                errors += issue.severity is Severity.ERROR
        ok = errors == 0 and bool(self.selected())
        self.export_button.setEnabled(ok)
        if not self.selected():
            self.summary.setText(self.tr("Select at least one format."))
        elif errors:
            self.summary.setText(
                self.tr("Export is blocked: fix the errors below (double-click to select).")
            )
        else:
            self.summary.setText(self.tr("Ready to export."))
        return ok

    def select_issue(self, item: QListWidgetItem) -> None:
        issue: Issue = item.data(Qt.ItemDataRole.UserRole)
        project = self.controller.project
        if project is None:
            return
        for item_id in issue.item_ids:
            for timeline in project.timelines:
                if any(s.id == item_id for s in timeline.segments):
                    self.controller.set_current_timeline(timeline.id)
                    self.controller.select(Selection(SelectionKind.SEGMENT, item_id, timeline.id))
                    return
            if any(z.id == item_id for z in project.zones):
                zone = project.zone(item_id)
                self.controller.set_current_clip(zone.clip_id)
                self.controller.select(Selection(SelectionKind.ZONE, item_id))
                return

    def export(self) -> None:
        if not self.validate():
            return
        project = self.controller.project
        project_dir = self.controller.project_dir
        assert project is not None and project_dir is not None
        snapshot = project.model_copy(deep=True)
        folder = Path(self.folder.text())
        draft = self.draft.isChecked()
        options = RenderOptions(
            brand=self.controller.brand_kit(),
            follow_paths=follow_paths(project, MediaCache(project_dir)),
        )
        for preset in self.selected():
            output = folder / f"{project.name}-{preset.id}{'-draft' if draft else ''}.mp4"

            def work(ctx: JobContext, preset: OutputPreset = preset, output: Path = output) -> Path:
                return render_timeline(
                    snapshot,
                    project_dir,
                    preset,
                    output,
                    self.controller.toolchain(),
                    options=options,
                    draft=draft,
                    progress=ctx.progress,
                    token=ctx.token,
                )

            self.runner.run(
                self.tr("Export {name}").replace("{name}", preset.name),
                work,
                on_success=lambda path: self.controller.notice.emit(
                    self.tr("Exported {path}").replace("{path}", str(path))
                ),
                on_error=lambda message: self.controller.error.emit(message),
            )
        self.accept()
