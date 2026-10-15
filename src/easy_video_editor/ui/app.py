"""Entry point of the `eve-gui` command."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from easy_video_editor import __version__
from easy_video_editor.ui.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("EVE")
    app.setApplicationDisplayName("EVE")
    app.setOrganizationName("EVE")
    app.setApplicationVersion(__version__)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
