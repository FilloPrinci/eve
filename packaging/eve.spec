# PyInstaller spec for EVE: one folder with the `eve` command-line program and the GUI.
# Build with: uv run pyinstaller packaging/eve.spec --noconfirm
import subprocess
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


def shared_libraries(path):
    """Names of the shared libraries ``path`` loads (Linux), from ldd."""
    output = subprocess.run(["ldd", path], capture_output=True, text=True, check=False).stdout
    return {line.split()[0] for line in output.splitlines() if "=>" in line}


def without_system_libmpv(binaries):
    """On Linux the system libmpv is used (it pulls in a whole desktop stack): drop it and the
    libraries only it needs. Windows releases bundle libmpv-2.dll separately."""
    mpv = [entry for entry in binaries if Path(entry[0]).name.startswith("libmpv.so")]
    if not mpv:
        return binaries
    only_mpv = shared_libraries(mpv[0][1]) | {Path(mpv[0][0]).name}
    kept = [entry for entry in binaries if Path(entry[0]).name not in only_mpv]
    needed = set()
    for _, source, _ in kept:
        needed |= shared_libraries(source)
    return [
        entry
        for entry in binaries
        if Path(entry[0]).name not in only_mpv or Path(entry[0]).name in needed
    ]


cli = analysis("entry_cli.py")
gui = analysis("entry_gui.py")
if sys.platform.startswith("linux"):
    cli.binaries = without_system_libmpv(cli.binaries)
    gui.binaries = without_system_libmpv(gui.binaries)
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
