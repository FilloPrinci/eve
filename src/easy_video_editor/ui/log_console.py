"""In-app log console (SPEC §8.1): all application logs and external process output."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.logs import LOG_FORMAT

MAX_LINES = 20_000


class _Emitter(QObject):
    record = Signal(str)


class QtLogHandler(logging.Handler):
    """Forwards formatted log records to the UI thread."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.emitter = _Emitter()
        self.setFormatter(logging.Formatter(LOG_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(RuntimeError):  # the console was destroyed during shutdown
            self.emitter.record.emit(self.format(record))


class LogConsole(QWidget):
    def __init__(self, handler: QtLogHandler, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        copy = QPushButton(self.tr("Copy"))
        copy.clicked.connect(self.copy_all)
        export = QPushButton(self.tr("Export…"))
        export.clicked.connect(self.export)
        clear = QPushButton(self.tr("Clear"))
        clear.clicked.connect(self.view.clear)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for button in (copy, export, clear):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        layout.addLayout(buttons)
        handler.emitter.record.connect(self.append, Qt.ConnectionType.QueuedConnection)

    def append(self, line: str) -> None:
        self.view.appendPlainText(line)

    def copy_all(self) -> None:
        QGuiApplication.clipboard().setText(self.view.toPlainText())

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export log"), "eve-log.txt", self.tr("Text files (*.txt)")
        )
        if path:
            Path(path).write_text(self.view.toPlainText(), encoding="utf-8")
