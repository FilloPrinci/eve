"""Discovery of external binaries (SPEC §13).

Discovery order: path set in the settings → binaries bundled with the release → system PATH.
"""

from __future__ import annotations

import ctypes.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from easy_video_editor.media.errors import MissingBinaryError

IS_WINDOWS = sys.platform == "win32"

LIBMPV_NAMES_WINDOWS = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")
LIBMPV_NAMES_POSIX = ("libmpv.so.2", "libmpv.so.1", "libmpv.so")


def bundled_dirs() -> list[Path]:
    """Folders that hold binaries bundled with a frozen (PyInstaller/AppImage) release."""
    if not getattr(sys, "frozen", False):
        return []
    roots = [Path(sys.executable).resolve().parent]
    bundle = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle, str):
        roots.append(Path(bundle))
    return [folder for root in roots for folder in (root, root / "bin")]


def executable_name(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


def find_executable(name: str, override: Path | None = None) -> Path | None:
    if override is not None:
        return override if override.is_file() else None
    for folder in bundled_dirs():
        candidate = folder / executable_name(name)
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def find_libmpv(override: Path | None = None) -> str | None:
    """Path (or loadable library name) of libmpv, or None."""
    if override is not None:
        return str(override) if override.is_file() else None
    names = LIBMPV_NAMES_WINDOWS if IS_WINDOWS else LIBMPV_NAMES_POSIX
    folders = bundled_dirs()
    if IS_WINDOWS:
        folders += [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    for folder in folders:
        for name in names:
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    return None if IS_WINDOWS else ctypes.util.find_library("mpv")


@dataclass(frozen=True)
class Toolchain:
    """Paths of the external programs EVE runs."""

    ffmpeg: Path
    ffprobe: Path

    @classmethod
    def discover(cls, ffmpeg: Path | None = None, ffprobe: Path | None = None) -> Toolchain:
        found_ffmpeg = find_executable("ffmpeg", ffmpeg)
        found_ffprobe = find_executable("ffprobe", ffprobe)
        missing = [
            name
            for name, path in (("ffmpeg", found_ffmpeg), ("ffprobe", found_ffprobe))
            if path is None
        ]
        if found_ffmpeg is None or found_ffprobe is None:
            raise MissingBinaryError(
                f"{' and '.join(missing)} not found. Run `eve doctor` for installation help."
            )
        return cls(ffmpeg=found_ffmpeg, ffprobe=found_ffprobe)
