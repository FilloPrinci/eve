"""Video player (SPEC §6.2, §8.1): libmpv's software render API, with Qt-drawn overlays.

Rendering into a QImage (instead of embedding a native window or sharing an OpenGL context)
works on X11, Wayland and Windows and lets Qt paint text overlays on top of the video. Without
libmpv a placeholder explains how to install it; the rest of the application keeps working.
"""

from __future__ import annotations

import ctypes
import locale
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import QStackedLayout, QWidget

from easy_video_editor.media.binaries import find_libmpv

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverlayText:
    text: str
    size: float
    """Font size as a fraction of the short side of the video area."""
    x: float
    y: float
    color: str = "#FFFFFF"
    box: str | None = None


OverlayProvider = Callable[[float], list[OverlayText]]
"""Returns the texts to draw at a playback position (seconds)."""


def video_rect(widget_width: int, widget_height: int, aspect: float) -> QRectF:
    """Largest rectangle with ``aspect`` centred in the widget (letterboxing)."""
    if widget_width / max(widget_height, 1) > aspect:
        width = widget_height * aspect
        return QRectF((widget_width - width) / 2, 0, width, widget_height)
    height = widget_width / aspect
    return QRectF(0, (widget_height - height) / 2, widget_width, height)


def paint_overlays(
    painter: QPainter, area: QRectF, texts: list[OverlayText], badge: str | None, family: str
) -> None:
    for item in texts:
        font = QFont(family)
        font.setPixelSize(max(6, round(item.size * min(area.width(), area.height()))))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(item.text)
        height = metrics.height()
        x = area.left() + area.width() * item.x - width / 2
        y = area.top() + area.height() * item.y - height / 2
        if item.box:
            painter.fillRect(QRectF(x - 6, y - 3, width + 12, height + 6), QColor(item.box))
        painter.setPen(QColor(item.color))
        painter.drawText(QRectF(x, y, width, height), Qt.AlignmentFlag.AlignCenter, item.text)
    if badge:
        font = QFont()
        font.setBold(True)
        painter.setFont(font)
        rect = QRectF(area.left() + 8, area.top() + 8, 70, 22)
        painter.fillRect(rect, QColor("#F5C400"))
        painter.setPen(QColor("#000000"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, badge)


class PlayerView(QWidget):
    """Common interface of the mpv view and the placeholder."""

    positionChanged = Signal(float)
    durationChanged = Signal(float)
    pausedChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.overlays: OverlayProvider | None = None
        self.badge: str | None = None
        self.aspect = 16 / 9
        self.font_family = "Anton"
        self.position = 0.0
        self.duration = 0.0
        self.paused = True
        self.available = False

    def load(self, url: str, start: float = 0.0) -> None: ...
    def unload(self) -> None: ...
    def set_paused(self, paused: bool) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def frame_step(self, frames: int) -> None: ...
    def set_speed(self, speed: float) -> None: ...

    def toggle(self) -> None:
        self.set_paused(not self.paused)

    def current_overlays(self) -> list[OverlayText]:
        return self.overlays(self.position) if self.overlays else []


class PlaceholderView(PlayerView):
    """Shown when libmpv is missing (or disabled). Tracks the position so the UI stays usable."""

    def __init__(self, reason: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.reason = reason
        self.setMinimumSize(320, 180)

    def load(self, url: str, start: float = 0.0) -> None:
        self.seek(start)

    def seek(self, seconds: float) -> None:
        self.position = max(seconds, 0.0)
        self.positionChanged.emit(self.position)
        self.update()

    def frame_step(self, frames: int) -> None:
        self.seek(self.position + frames / 30)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.pausedChanged.emit(paused)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101010"))
        area = video_rect(self.width(), self.height(), self.aspect)
        painter.fillRect(area, QColor("#1E1E1E"))
        painter.setPen(QColor("#AAAAAA"))
        painter.drawText(
            area.adjusted(10, 10, -10, -10),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self.reason,
        )
        paint_overlays(painter, area, self.current_overlays(), self.badge, self.font_family)


SW_SIZE, SW_FORMAT, SW_STRIDE, SW_POINTER = 17, 18, 19, 20
"""``MPV_RENDER_PARAM_SW_*`` from libmpv's render.h (not wrapped by python-mpv)."""


def _render_param(module: Any, type_id: int, data: Any) -> Any:
    param = module.MpvRenderParam("invalid")
    param.type_id = type_id
    param.data = ctypes.cast(data, ctypes.c_void_p) if data is not None else None
    return param


class MpvView(QWidget):
    """libmpv's software render API drawing into a QImage.

    No OpenGL is involved, so playback works the same on X11, Wayland, Windows, virtual
    machines and headless test runs, and overlays are ordinary QPainter drawing. Previews play
    low-resolution proxies, so the CPU cost is small.
    """

    positionChanged = Signal(float)
    durationChanged = Signal(float)
    pausedChanged = Signal(bool)
    _frameReady = Signal()
    _property = Signal(str, object)

    def __init__(self, mpv_module: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.overlays: OverlayProvider | None = None
        self.badge: str | None = None
        self.aspect = 16 / 9
        self.font_family = "Anton"
        self.position = 0.0
        self.duration = 0.0
        self.paused = True
        self.available = True
        self._module = mpv_module
        self._pending_seek: float | None = None
        self._image: QImage | None = None
        locale.setlocale(locale.LC_NUMERIC, "C")  # required by libmpv
        self._mpv = mpv_module.MPV(
            vo="libmpv", keep_open="yes", idle="yes", hr_seek="yes", osc="no",
            input_default_bindings=False, input_vo_keyboard=False, pause=True,
            log_handler=self._mpv_log, loglevel="warn",
        )  # fmt: skip
        self._context = mpv_module.MpvRenderContext(self._mpv, "sw")
        self._frameReady.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self._property.connect(self._on_property, Qt.ConnectionType.QueuedConnection)
        self._context.update_cb = self._frameReady.emit
        for name in ("time-pos", "duration", "pause"):
            self._mpv.observe_property(name, lambda prop, value: self._property.emit(prop, value))
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    @staticmethod
    def _mpv_log(level: str, component: str, message: str) -> None:
        log.info("mpv %s [%s]: %s", level, component, message.strip())

    def _on_property(self, name: str, value: object) -> None:
        if name == "time-pos" and isinstance(value, int | float):
            self.position = float(value)
            self.positionChanged.emit(self.position)
        elif name == "duration" and isinstance(value, int | float):
            self.duration = float(value)
            self.durationChanged.emit(self.duration)
            if self._pending_seek is not None:
                target, self._pending_seek = self._pending_seek, None
                self.seek(target)
        elif name == "pause" and isinstance(value, bool):
            self.paused = value
            self.pausedChanged.emit(value)

    # --- Rendering -----------------------------------------------------------------------------

    def render_frame(self) -> QImage | None:
        """Render the current video frame at the widget's (device pixel) size."""
        if self._context is None:
            return None
        ratio = self.devicePixelRatioF()
        width, height = max(round(self.width() * ratio), 2), max(round(self.height() * ratio), 2)
        image = self._image
        if image is None or image.width() != width or image.height() != height:
            image = QImage(width, height, QImage.Format.Format_RGBX8888)
            image.setDevicePixelRatio(ratio)
            self._image = image
        size = (ctypes.c_int * 2)(width, height)
        stride = ctypes.c_size_t(image.bytesPerLine())
        pixel_format = ctypes.c_char_p(b"rgb0")
        buffer = (ctypes.c_char * image.sizeInBytes()).from_buffer(image.bits())
        params = (self._module.MpvRenderParam * 5)(
            _render_param(self._module, SW_SIZE, size),
            _render_param(self._module, SW_FORMAT, pixel_format),
            _render_param(self._module, SW_STRIDE, ctypes.pointer(stride)),
            _render_param(self._module, SW_POINTER, ctypes.addressof(buffer)),
            self._module.MpvRenderParam("invalid"),
        )
        self._module._mpv_render_context_render(self._context.handle, params)
        return image

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        image = self.render_frame()
        if image is not None:
            painter.drawImage(0, 0, image)
        else:
            painter.fillRect(self.rect(), QColor("#000000"))
        area = video_rect(self.width(), self.height(), self.aspect)
        paint_overlays(painter, area, self.current_overlays(), self.badge, self.font_family)

    def shutdown(self) -> None:
        if self._context is not None:
            self._context.free()
            self._context = None
        self._mpv.terminate()

    # --- Playback ------------------------------------------------------------------------------

    def _command(self, *args: object) -> bool:
        """Run an mpv command; mpv refuses some while a file is loading, which is not an error."""
        try:
            self._mpv.command(*args)
        except SystemError as error:
            log.debug("mpv command %s refused: %s", args[0], error)
            return False
        return True

    def load(self, url: str, start: float = 0.0) -> None:
        self._pending_seek = None
        self._mpv.loadfile(url, start=f"{start:.6f}")

    def unload(self) -> None:
        self._command("stop")

    def set_paused(self, paused: bool) -> None:
        self._mpv.pause = paused

    def toggle(self) -> None:
        self.set_paused(not self.paused)

    def seek(self, seconds: float) -> None:
        target = max(seconds, 0.0)
        if not self._command("seek", f"{target:.6f}", "absolute", "exact"):
            self._pending_seek = target  # applied once the file is loaded

    def frame_step(self, frames: int) -> None:
        self._command("frame-step" if frames > 0 else "frame-back-step")

    def set_speed(self, speed: float) -> None:
        self._mpv.play_direction = "backward" if speed < 0 else "forward"
        self._mpv.speed = abs(speed) or 1.0
        self.set_paused(speed == 0)

    def current_overlays(self) -> list[OverlayText]:
        return self.overlays(self.position) if self.overlays else []


def load_mpv_module(libmpv_override: Path | None) -> tuple[Any | None, str]:
    """Import python-mpv, returning (module, reason it is unavailable)."""
    if os.environ.get("EVE_PLAYER") == "none":
        return None, "Playback is disabled (EVE_PLAYER=none)."
    library = find_libmpv(libmpv_override)
    if library is None:
        return None, (
            "libmpv was not found, so videos cannot be played here. "
            "Editing and rendering still work. Open Help → System check for installation help."
        )
    folder = Path(library).parent
    if sys.platform == "win32" and folder.is_dir():
        os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"
        os.add_dll_directory(str(folder))
    try:
        import mpv
    except (ImportError, OSError) as error:
        return None, f"libmpv could not be loaded: {error}"
    return mpv, ""


def create_view(libmpv_override: Path | None = None) -> Any:
    """An `MpvView` when libmpv is usable, otherwise a `PlaceholderView`."""
    module, reason = load_mpv_module(libmpv_override)
    if module is not None:
        try:
            return MpvView(module)
        except Exception as error:
            log.exception("Cannot start mpv")
            reason = f"mpv could not be started: {error}"
    return PlaceholderView(reason)


class PlayerHost(QWidget):
    """Holds the video view (which draws its own overlays)."""

    def __init__(self, view: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = view
        layout = QStackedLayout(self)
        layout.addWidget(view)

    def refresh_overlay(self) -> None:
        self.view.update()
