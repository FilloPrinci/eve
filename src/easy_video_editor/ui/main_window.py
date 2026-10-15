"""The EVE main window."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QWidget

from easy_video_editor import __version__


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self.setWindowTitle(self.tr("EVE — Easy Video Editor"))
        self.resize(1280, 800)

        placeholder = QLabel(self.tr("No project open."), self)
        placeholder.setObjectName("placeholder")
        self.setCentralWidget(placeholder)

        self.statusBar().showMessage(self.tr("EVE {version}").replace("{version}", __version__))
