"""Render the SVG app icon to the PNG and ICO files the release build needs.

Usage: python packaging/make_icons.py <output folder>
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SOURCE = Path(__file__).resolve().parents[1] / "src/easy_video_editor/resources/icons/eve.svg"


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(SOURCE)).render(painter)
    painter.end()
    return image


def main() -> None:
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication([])  # noqa: F841 - QPainter needs an application object
    if not render(256).save(str(output / "eve.png")):
        raise SystemExit("cannot write eve.png")
    if not render(256).save(str(output / "eve.ico")):
        raise SystemExit("cannot write eve.ico")


main()
