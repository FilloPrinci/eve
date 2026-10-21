EVE - Easy Video Editor (Linux, x86-64)

Install for your user (no root needed):

    ./install.sh

EVE then appears in your application menu. From a terminal: eve-gui (the editor) and eve (the
command-line program, e.g. "eve doctor"). Run install.sh from a newer version to update.
To uninstall: ~/.local/share/EVE/uninstall.sh (your projects and settings are kept).

The video player uses your system's libmpv:
  Fedora:          sudo dnf install mpv-libs
  Debian/Ubuntu:   sudo apt install libmpv2

User guide: https://github.com/FilloPrinci/eve/blob/main/docs/USER_GUIDE.md
