"""Entry point of the `eve-gui` command."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from easy_video_editor import __version__
from easy_video_editor.appsettings import load_app_settings
from easy_video_editor.logs import setup_logging
from easy_video_editor.media.binaries import find_libmpv
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


SMOKE_TEST = "--smoke-test"
"""Start, show the main window and quit after a moment (used to check release builds)."""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv
    smoke_test = SMOKE_TEST in args
    args = [arg for arg in args if arg != SMOKE_TEST]
    app = QApplication(args)
    app.setApplicationName("EVE")
    app.setApplicationDisplayName("EVE")
    app.setOrganizationName("EVE")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")  # follows the system light/dark colour scheme
    app.setWindowIcon(QIcon(str(RESOURCES_DIR / "icons" / "eve.svg")))
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
    elif not smoke_test:
        window.reopen_last_project()
    if smoke_test:
        QTimer.singleShot(1500, app.quit)
        app.exec()
        return smoke_test_result(window)
    window.maybe_first_run_check()
    return app.exec()


def smoke_test_result(window: MainWindow) -> int:
    """0, or 3 when a libmpv is installed (or bundled) but the player could not use it."""
    view = window.player_host.view
    reason = getattr(view, "reason", None)
    print(f"player: {reason or type(view).__name__}", flush=True)
    libmpv = window.controller.settings.libmpv_path
    return 3 if reason and find_libmpv(Path(libmpv) if libmpv else None) else 0


if __name__ == "__main__":
    raise SystemExit(main())
