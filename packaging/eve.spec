# PyInstaller spec for EVE: one folder with the `eve` command-line program and the GUI.
# Build with: uv run pyinstaller packaging/eve.spec --noconfirm
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
PACKAGE = ROOT / "src" / "easy_video_editor"
ICONS = ROOT / "build" / "icons"
WINDOWS = sys.platform == "win32"

datas = [
    (str(PACKAGE / "ai" / "prompts"), "easy_video_editor/ai/prompts"),
    (str(PACKAGE / "resources" / "fonts"), "easy_video_editor/resources/fonts"),
    (str(PACKAGE / "resources" / "icons"), "easy_video_editor/resources/icons"),
]
datas += collect_data_files("librosa")
hidden = (
    collect_submodules("easy_video_editor")
    + collect_submodules("keyring.backends")
    + ["mpv"]
)
excludes = ["tkinter", "matplotlib", "IPython", "pytest"]


def analysis(script):
    return Analysis(
        [str(ROOT / "packaging" / script)],
        pathex=[str(ROOT / "src")],
        datas=datas,
        hiddenimports=hidden,
        excludes=excludes,
        noarchive=False,
    )


SYSTEM_DIRS = ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/")
KEEP_SYSTEM = (
    # Rarely preinstalled, or named differently elsewhere (Debian's libbz2.so.1.0), and free of
    # version clashes. Never keep one library of a package whose siblings come from the host:
    # libxkbcommon-x11 must match the host's libxkbcommon exactly (it crashed on Fedora 43).
    "libpython", "libmpdec", "libbz2", "libxcb-cursor", "libxcb-icccm", "libxcb-image",
    "libxcb-keysyms", "libxcb-render-util", "libxcb-util",
)  # fmt: skip


def without_host_libraries(binaries):
    """Linux: leave out the build machine's system libraries (libstdc++, glib, freetype, OpenSSL…).

    Every desktop has them, newer than on the build machine. Bundled old copies would be loaded
    first and break the system libmpv (and anything else of the host EVE loads), e.g. "GLIBCXX_3.4.32
    not found". The system libmpv itself is used rather than bundled.
    """

    def host_library(entry):
        name, source = Path(entry[0]).name, entry[1]
        return (
            source.startswith(SYSTEM_DIRS)
            and "/python3" not in source  # Python's own extension modules stay
            and name.startswith("lib")
            and not name.startswith(KEEP_SYSTEM)
        )

    return [entry for entry in binaries if not host_library(entry)]


cli = analysis("entry_cli.py")
gui = analysis("entry_gui.py")
if sys.platform.startswith("linux"):
    cli.binaries = without_host_libraries(cli.binaries)
    gui.binaries = without_host_libraries(gui.binaries)
icon = str(ICONS / ("eve.ico" if WINDOWS else "eve.png"))

cli_exe = EXE(
    PYZ(cli.pure),
    cli.scripts,
    [],
    exclude_binaries=True,
    name="eve",
    console=True,
    icon=icon,
)
gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name="eve-gui",  # not "EVE": Windows file names ignore case and would clash with eve.exe
    console=False,
    icon=icon,
)
COLLECT(
    cli_exe,
    gui_exe,
    cli.binaries + gui.binaries,
    cli.datas + gui.datas,
    name="EVE",
)
