"""History panel (SPEC §7.3): undoable steps with their origin and finer steps."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.ui.controller import ProjectController


class HistoryPanel(QWidget):
    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([self.tr("Change"), self.tr("By")])
        self.undo_button = QPushButton(self.tr("Undo"))
        self.undo_button.clicked.connect(controller.undo)
        self.redo_button = QPushButton(self.tr("Redo"))
        self.redo_button.clicked.connect(controller.redo)
        buttons = QHBoxLayout()
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.redo_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        layout.addLayout(buttons)
        controller.projectChanged.connect(self.refresh)

    def refresh(self) -> None:
        self.tree.clear()
        session = self.controller.session
        self.undo_button.setEnabled(bool(session and session.can_undo))
        self.redo_button.setEnabled(bool(session and session.can_redo))
        if session is None:
            return
        for entry in reversed(session.history):
            item = QTreeWidgetItem([entry.description, entry.origin.value])
            for step in entry.steps:
                item.addChild(QTreeWidgetItem([step, ""]))
            for notice in entry.notices:
                item.addChild(QTreeWidgetItem(["⚠ " + notice, ""]))
            self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(1)
