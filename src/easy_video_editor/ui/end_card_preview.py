"""End card stills rendered with ffmpeg exactly as in an export (brand kits, inspector)."""

from __future__ import annotations

import contextlib
import logging
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.model import EndCard, OutputPreset, TextLook
from easy_video_editor.core.progress import CancellationToken, CancelledError
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.render.renderer import render_end_card_still

log = logging.getLogger(__name__)

PREVIEW_DELAY_MS = 300
PREVIEW_SIZE = 240
"""Longest side of each end card preview, in pixels."""

_Request = tuple[BrandKit | None, EndCard | None, TextLook, tuple[OutputPreset, ...]]


class EndCardPreview(QGroupBox):
    """One end card still per output preset, rendered on a worker thread and debounced."""

    _rendered = Signal(int, object)
    """Generation and a dict of preset id to PNG bytes, or an error message."""

    def __init__(
        self,
        toolchain: Callable[[], Toolchain],
        presets: list[OutputPreset],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(self.tr("End card preview"), parent)
        self.toolchain = toolchain
        self.presets: tuple[OutputPreset, ...] = ()
        self._request: _Request | None = None
        self._generation = 0
        self._token: CancellationToken | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._render)
        self._rendered.connect(self._show, Qt.ConnectionType.QueuedConnection)

        self.images: dict[str, QLabel] = {}
        self.row = QHBoxLayout()
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumHeight(8)
        self.busy.setTextVisible(False)
        self.busy.setVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        layout = QVBoxLayout(self)
        layout.addLayout(self.row)
        layout.addWidget(self.busy)
        layout.addWidget(self.status)
        layout.addWidget(self.note)
        self.set_presets(presets)

    @staticmethod
    def _size(preset: OutputPreset) -> QSize:
        scale = PREVIEW_SIZE / max(preset.width, preset.height)
        return QSize(round(preset.width * scale), round(preset.height * scale))

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def set_presets(self, presets: list[OutputPreset]) -> None:
        if tuple(presets) == self.presets:
            return
        self.presets = tuple(presets)
        while (item := self.row.takeAt(0)) is not None:
            column = item.layout()
            while column is not None and (child := column.takeAt(0)) is not None:
                if (widget := child.widget()) is not None:
                    widget.deleteLater()
        self.images.clear()
        for preset in self.presets:
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setFixedSize(self._size(preset))
            image.setStyleSheet("background-color: #222; border: 1px solid #444")
            caption = QLabel(f"{preset.name}\n{preset.width}×{preset.height}")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column = QVBoxLayout()
            column.addWidget(image, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            column.addWidget(caption)
            self.row.addLayout(column)
            self.images[preset.id] = image
        if self._request is not None:
            kit, card, look, _ = self._request
            self._request = None
            self.show_card(kit, card, look)

    def show_card(
        self,
        kit: BrandKit | None,
        end_card: EndCard | None = None,
        text_look: TextLook = TextLook.CLASSIC,
    ) -> None:
        """Schedule a preview of ``kit``'s end card (debounced). ``end_card`` carries a project's
        own settings (call to action, background, logo); None shows the brand kit alone."""
        request = (kit, end_card, text_look, self.presets)
        if request == self._request:
            return
        self._request = request
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._generation += 1
        if self._token is not None:
            self._token.cancel()

    def _render(self) -> None:
        self.stop()
        if self._request is None or not self._request[3]:
            return
        kit, end_card, text_look, presets = self._request
        if kit is None and end_card is None:
            for image in self.images.values():
                image.clear()
            self.busy.setVisible(False)
            return
        generation, token = self._generation, CancellationToken()
        self._token = token
        self.busy.setVisible(True)
        toolchain = self.toolchain

        def work() -> None:
            result: dict[str, bytes] | str
            try:
                tools = toolchain()
                stills: dict[str, bytes] = {}
                with tempfile.TemporaryDirectory(prefix="eve-preview-") as folder:
                    for preset in presets:
                        output = render_end_card_still(
                            kit,
                            preset,
                            Path(folder) / f"{preset.id}.png",
                            tools,
                            end_card=end_card,
                            text_look=text_look,
                            scale=PREVIEW_SIZE / max(preset.width, preset.height),
                            token=token,
                        )
                        stills[preset.id] = output.read_bytes()
                result = stills
            except CancelledError:
                return
            except Exception as error:
                log.warning("End card preview failed: %s", error)
                result = str(error)
            # RuntimeError: the widget was deleted meanwhile.
            with contextlib.suppress(RuntimeError):
                self._rendered.emit(generation, result)

        threading.Thread(target=work, name="end-card-preview", daemon=True).start()

    def _show(self, generation: int, result: dict[str, bytes] | str) -> None:
        if generation != self._generation:
            return
        self.busy.setVisible(False)
        if isinstance(result, str):
            self.status.setText(self.tr("Preview failed: {error}").replace("{error}", result))
            return
        self.status.clear()
        for preset_id, data in result.items():
            label = self.images.get(preset_id)
            if label is None:
                continue
            pixmap = QPixmap()
            pixmap.loadFromData(data, "PNG")
            label.setPixmap(
                pixmap.scaled(
                    label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
