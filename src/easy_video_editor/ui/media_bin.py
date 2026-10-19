"""Media bin (SPEC §8.1): project clips with thumbnail, duration and media status."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.core.commands import AddClip, RemoveClip
from easy_video_editor.core.jobs import JobContext
from easy_video_editor.core.model import Clip
from easy_video_editor.core.timecode import format_us
from easy_video_editor.media.fingerprint import Fingerprint
from easy_video_editor.media.generate import StripInfo
from easy_video_editor.media.importer import add_clip_command, prepare_clip_media
from easy_video_editor.ui.controller import ProjectController
from easy_video_editor.ui.jobs import JobRunner

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = ("mp4", "mkv", "mov", "webm", "avi", "m4v", "flv", "ts")
# File dialog filters are case-sensitive on Linux: list upper case too (CLIP.MP4 from cameras).
VIDEO_FILTER = (
    "Videos ("
    + " ".join(f"*.{ext} *.{ext.upper()}" for ext in VIDEO_EXTENSIONS)
    + ");;All files (*)"
)


class MediaBin(QWidget):
    def __init__(
        self, controller: ProjectController, runner: JobRunner, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.runner = runner
        self.list = QListWidget()
        self.list.setIconSize(QSize(96, 54))
        self.list.currentItemChanged.connect(self._on_current)
        self.setAcceptDrops(True)
        import_button = QPushButton(self.tr("Import…"))
        import_button.clicked.connect(self.choose_files)
        remove = QPushButton(self.tr("Remove"))
        remove.clicked.connect(self.remove_current)
        buttons = QHBoxLayout()
        buttons.addWidget(import_button)
        buttons.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        controller.projectChanged.connect(self.refresh)
        controller.currentClipChanged.connect(self._select_current)

    # --- Import --------------------------------------------------------------------------------

    def choose_files(self) -> None:
        if self.controller.project is None:
            self.controller.error.emit(
                self.tr("Create or open a project first (File → New project…), then import clips.")
            )
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Import clips"), "", self.tr(VIDEO_FILTER)
        )
        self.import_files([Path(p) for p in paths])

    def import_files(self, paths: list[Path]) -> None:
        if self.controller.project is None:
            if paths:
                self.controller.error.emit(
                    self.tr("Create or open a project first, then import clips.")
                )
            return
        for path in paths:
            self._import_one(path)

    def _import_one(self, path: Path) -> None:
        def work(ctx: JobContext) -> AddClip:
            return add_clip_command(path, self.controller.toolchain(), ctx.token)[0]

        def failed(message: str) -> None:
            self.controller.error.emit(
                self.tr("Cannot import {name}: {error}")
                .replace("{name}", path.name)
                .replace("{error}", message)
            )

        self.runner.run(
            self.tr("Analyse {name}").replace("{name}", path.name),
            work,
            on_success=self._add_clip,
            on_error=failed,
        )

    def _add_clip(self, command: AddClip) -> None:
        project = self.controller.project
        if project is None or command.fingerprint is None:
            return
        key = Fingerprint.parse(command.fingerprint).content_key
        for clip in project.clips:
            if clip.fingerprint and Fingerprint.parse(clip.fingerprint).content_key == key:
                self.controller.notice.emit(
                    self.tr("{name} is already in the project.").replace("{name}", clip.name)
                )
                return
        result = self.controller.execute(command)
        if result:
            clip_id = result.created_ids[0]
            self.controller.set_current_clip(clip_id)
            self.prepare_media(clip_id)

    def prepare_media(self, clip_id: str) -> None:
        project, cache = self.controller.project, self.controller.cache
        if project is None or cache is None:
            return
        clip = project.clip(clip_id)
        height = self.controller.settings.proxy_height

        def work(ctx: JobContext) -> None:
            prepare_clip_media(
                clip, cache, self.controller.toolchain(), ctx.progress, ctx.token, height
            )

        self.runner.run(
            self.tr("Prepare {name}").replace("{name}", clip.name),
            work,
            on_success=lambda _: self._media_ready(clip_id),
            on_error=lambda message: self.controller.error.emit(message),
        )

    def _media_ready(self, clip_id: str) -> None:
        self.refresh()
        if self.controller.current_clip_id == clip_id:
            self.controller.currentClipChanged.emit()

    def remove_current(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.controller.execute(RemoveClip(clip_id=item.data(Qt.ItemDataRole.UserRole)))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self.import_files([p for p in paths if p.is_file()])

    # --- List ----------------------------------------------------------------------------------

    def _icon(self, clip: Clip) -> QIcon:
        cache = self.controller.cache
        if cache is None:
            return QIcon()
        strip, info_path = cache.strip_path(clip), cache.strip_info_path(clip)
        if not (strip.is_file() and info_path.is_file()):
            return QIcon()
        try:
            info = StripInfo.load(info_path)
        except (OSError, ValueError, TypeError):
            return QIcon()
        pixmap = QPixmap(str(strip)).copy(0, 0, info.thumb_width, info.thumb_height)
        return QIcon(pixmap)

    def refresh(self) -> None:
        project, cache = self.controller.project, self.controller.cache
        current = self.controller.current_clip_id
        self.list.blockSignals(True)
        self.list.clear()
        if project is not None and cache is not None:
            for clip in project.clips:
                status = cache.status(clip, self.controller.settings.proxy_height)
                state = self.tr("ready") if status.proxy_ready else self.tr("preparing…")
                missing = "" if clip.file_path.is_file() else self.tr(" — source missing")
                item = QListWidgetItem(
                    self._icon(clip),
                    f"{clip.name}\n{format_us(clip.duration_us)} · {state}{missing}",
                )
                item.setData(Qt.ItemDataRole.UserRole, clip.id)
                self.list.addItem(item)
                if clip.id == current:
                    self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def _select_current(self) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == self.controller.current_clip_id:
                self.list.blockSignals(True)
                self.list.setCurrentItem(item)
                self.list.blockSignals(False)

    def _on_current(self, item: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if item is not None:
            self.controller.set_current_clip(item.data(Qt.ItemDataRole.UserRole))
