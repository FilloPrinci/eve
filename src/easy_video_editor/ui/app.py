"""Entry point of the `eve-gui` command."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from easy_video_editor import __version__
from easy_video_editor.appsettings import load_app_settings
from easy_video_editor.logs import setup_logging
from easy_video_editor.resources import DEFAULT_FONT, RESOURCES_DIR
from easy_video_editor.ui.log_console import QtLogHandler
from easy_video_editor.ui.main_window import MainWindow

log = logging.getLogger(__name__)


def install_translations(app: QApplication) -> None:
    """Load Qt's and EVE's translations for the system language when available."""
    locale = QLocale.system()
    for name, folder in (
        ("qtbase", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)),
        ("eve", str(RESOURCES_DIR / "translations")),
    ):
        translator = QTranslator(app)
        if translator.load(locale, name, "_", folder):
            app.installTranslator(translator)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv
    app = QApplication(args)
    app.setApplicationName("EVE")
    app.setApplicationDisplayName("EVE")
    app.setOrganizationName("EVE")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")  # follows the system light/dark colour scheme
    install_translations(app)
    QFontDatabase.addApplicationFont(str(DEFAULT_FONT))

    setup_logging()
    handler = QtLogHandler()
    logging.getLogger("easy_video_editor").addHandler(handler)
    log.info("EVE %s started", __version__)

    window = MainWindow(load_app_settings(), handler)
    window.show()
    if len(args) > 1 and Path(args[1]).is_dir():
        window.open_project(Path(args[1]))
    window.maybe_first_run_check()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
