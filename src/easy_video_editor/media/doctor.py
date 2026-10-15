"""System check (SPEC §13): what is installed and how to fix what is missing."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from easy_video_editor.media.binaries import find_executable, find_libmpv
from easy_video_editor.media.errors import MediaError
from easy_video_editor.media.process import run

HARDWARE_ENCODERS = (
    "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_vaapi", "hevc_vaapi",
    "h264_amf", "hevc_amf", "h264_videotoolbox", "hevc_videotoolbox",
)  # fmt: skip


class Status(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    fix: str | None = None


CLAUDE_LOGIN = "# then run 'claude' once and log in with your Claude account"

FIXES: dict[str, dict[str, str]] = {
    "ffmpeg": {
        "fedora": "sudo dnf install ffmpeg-free  # or 'ffmpeg' from RPM Fusion",
        "debian": "sudo apt install ffmpeg",
        "arch": "sudo pacman -S ffmpeg",
        "windows": "winget install --id Gyan.FFmpeg -e  # or use an EVE release, which bundles it",
    },
    "libmpv": {
        "fedora": "sudo dnf install mpv-libs",
        "debian": "sudo apt install libmpv2  # libmpv1 on older releases",
        "arch": "sudo pacman -S mpv",
        "windows": "Download libmpv-2.dll from "
        "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/ "
        "and put it next to EVE or on PATH (EVE releases bundle it)",
    },
    "claude": {
        family: "curl -fsSL https://claude.ai/install.sh | bash  " + CLAUDE_LOGIN
        for family in ("fedora", "debian", "arch")
    }
    | {"windows": "In PowerShell: irm https://claude.ai/install.ps1 | iex  " + CLAUDE_LOGIN},
}


def platform_family() -> str | None:
    if sys.platform == "win32":
        return "windows"
    if not sys.platform.startswith("linux"):
        return None
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        return None
    ids = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    for family, members in (
        ("fedora", {"fedora", "rhel", "centos"}),
        ("debian", {"debian", "ubuntu"}),
        ("arch", {"arch", "manjaro", "endeavouros"}),
    ):
        if ids & members:
            return family
    return None


def fix_for(component: str, family: str | None = None) -> str:
    options = FIXES[component]
    if family in options:
        return options[family]
    return "\n".join(f"{name}: {command}" for name, command in options.items())


def _version(executable: Path) -> str:
    result = run([executable, "-hide_banner", "-version"])
    return result.stdout.splitlines()[0] if result.stdout else "unknown version"


def _binary_check(
    name: str, override: Path | None, family: str | None
) -> tuple[Check, Path | None]:
    path = find_executable(name, override)
    if path is None:
        return Check(name, Status.ERROR, "not found", fix_for("ffmpeg", family)), None
    try:
        detail = f"{path} ({_version(path)})"
    except (MediaError, OSError) as error:
        return Check(name, Status.ERROR, f"{path} does not run: {error}"), None
    return Check(name, Status.OK, detail), path


def _encoder_check(ffmpeg: Path) -> Check:
    try:
        output = run([ffmpeg, "-hide_banner", "-encoders"]).stdout
    except (MediaError, OSError) as error:
        return Check("GPU encoders", Status.WARNING, f"cannot list encoders: {error}")
    names = {line.split()[1] for line in output.splitlines() if len(line.split()) > 1}
    found = [encoder for encoder in HARDWARE_ENCODERS if encoder in names]
    if not found:
        return Check("GPU encoders", Status.OK, "none in this ffmpeg build; CPU encoding is used")
    return Check("GPU encoders", Status.OK, f"available in this ffmpeg build: {', '.join(found)}")


def _keyring_check() -> Check:
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return Check("Keyring", Status.WARNING, "the keyring package is not installed")
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        return Check(
            "Keyring",
            Status.WARNING,
            "no OS keyring available; AI provider API keys cannot be stored",
            "Install and unlock a Secret Service provider (GNOME Keyring or KWallet)",
        )
    return Check("Keyring", Status.OK, type(backend).__name__)


def _claude_check(claude: Path | None, family: str | None) -> Check:
    name = "Claude Code (AI mode)"
    if claude is None:
        return Check(
            name,
            Status.WARNING,
            "not found; AI mode needs Claude Code logged in with your Claude account "
            "(Auto mode works without it)",
            fix_for("claude", family),
        )
    try:
        result = run([claude, "--version"])
        version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "?"
    except MediaError as error:
        return Check(name, Status.WARNING, f"{claude} does not run: {error}")
    return Check(name, Status.OK, f"{version} ({claude}); log in once by running 'claude'")


def run_checks(
    ffmpeg: Path | None = None,
    ffprobe: Path | None = None,
    libmpv: Path | None = None,
    claude: Path | None = None,
) -> list[Check]:
    """``claude`` is the Claude Code executable found by the caller, or None if missing."""
    family = platform_family()
    ffmpeg_check, ffmpeg_path = _binary_check("ffmpeg", ffmpeg, family)
    ffprobe_check, _ = _binary_check("ffprobe", ffprobe, family)
    checks = [ffmpeg_check, ffprobe_check]
    library = find_libmpv(libmpv)
    checks.append(
        Check("libmpv", Status.OK, library)
        if library
        else Check(
            "libmpv",
            Status.WARNING,
            "not found; the GUI player will not work",
            fix_for("libmpv", family),
        )
    )
    if ffmpeg_path is not None:
        checks.append(_encoder_check(ffmpeg_path))
    checks.append(_keyring_check())
    checks.append(_claude_check(claude, family))
    return checks
