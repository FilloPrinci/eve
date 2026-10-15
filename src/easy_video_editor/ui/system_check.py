"""First-run "System check" dialog (SPEC §13): what is missing and how to install it."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.media.doctor import Check, Status


class SystemCheckDialog(QDialog):
    def __init__(self, checks: list[Check], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("System check"))
        self.resize(720, 360)
        self.checks = checks
        tree = QTreeWidget()
        tree.setHeaderLabels([self.tr("Component"), self.tr("Status"), self.tr("Details")])
        icons = {Status.OK: "✔", Status.WARNING: "⚠", Status.ERROR: "✖"}
        for check in checks:
            item = QTreeWidgetItem(
                [check.name, f"{icons[check.status]} {check.status.value}", check.detail]
            )
            if check.fix and check.status is not Status.OK:
                for line in check.fix.splitlines():
                    item.addChild(QTreeWidgetItem(["", self.tr("fix"), line]))
                item.setExpanded(True)
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(0)
        problems = [c for c in checks if c.status is not Status.OK]
        summary = QLabel(
            self.tr("Everything EVE needs was found.")
            if not problems
            else self.tr(
                "Some components are missing. Copy the fix commands and run them in a terminal."
            )
        )
        summary.setWordWrap(True)
        copy = QPushButton(self.tr("Copy fix commands"))
        copy.setEnabled(any(c.fix for c in problems))
        copy.clicked.connect(self.copy_fixes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(tree)
        layout.addWidget(buttons)

    def fix_text(self) -> str:
        return "\n".join(c.fix for c in self.checks if c.fix and c.status is not Status.OK)

    def copy_fixes(self) -> None:
        QGuiApplication.clipboard().setText(self.fix_text())
