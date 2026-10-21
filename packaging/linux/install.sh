#!/usr/bin/env bash
# Installs EVE for the current user: no root, no package manager, no FUSE.
# Run it from inside the extracted EVE-<version>-linux-x64 folder. Running it again updates EVE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
INSTALL_DIR="${DATA_HOME}/EVE"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${DATA_HOME}/applications"

if [[ ! -x "${SCRIPT_DIR}/app/AppRun" ]]; then
  echo "Error: run this script from inside the extracted EVE-<version>-linux-x64 folder." >&2
  exit 1
fi

echo "Installing EVE to ${INSTALL_DIR}"
# Replace the previous version completely; projects, settings and logs live elsewhere.
if [[ -f "${INSTALL_DIR}/AppRun" ]]; then
  rm -rf "${INSTALL_DIR}"
fi
mkdir -p "${INSTALL_DIR}"
cp -a "${SCRIPT_DIR}/app/." "${INSTALL_DIR}/"
cp -f "${SCRIPT_DIR}/uninstall.sh" "${INSTALL_DIR}/uninstall.sh"
chmod +x "${INSTALL_DIR}/AppRun" "${INSTALL_DIR}/uninstall.sh"

mkdir -p "${BIN_DIR}"
ln -sf "${INSTALL_DIR}/AppRun" "${BIN_DIR}/eve-gui"
cat >"${BIN_DIR}/eve" <<SCRIPT
#!/bin/sh
# EVE command-line program (installed by EVE's install.sh).
exec "${INSTALL_DIR}/AppRun" cli "\$@"
SCRIPT
chmod +x "${BIN_DIR}/eve"

mkdir -p "${DESKTOP_DIR}"
cat >"${DESKTOP_DIR}/eve.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=EVE
GenericName=Video Editor
Comment=Turn gameplay recordings into short trailers
Exec="${INSTALL_DIR}/AppRun" %F
TryExec=${INSTALL_DIR}/AppRun
Icon=${INSTALL_DIR}/eve.png
Categories=AudioVideo;Video;AudioVideoEditing;
StartupWMClass=EVE
Terminal=false
DESKTOP
if command -v update-desktop-database > /dev/null; then
  update-desktop-database "${DESKTOP_DIR}" > /dev/null 2>&1 || true
fi

echo "Done. Start EVE from your application menu, or run: eve-gui"
echo "Command-line program: eve (try: eve doctor). To uninstall: ${INSTALL_DIR}/uninstall.sh"
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
  echo "Note: ${BIN_DIR} is not on your PATH; add it to your shell profile to use 'eve' directly."
fi
if [[ "$(ldconfig -p 2> /dev/null)" != *"libmpv.so.2"* ]]; then
  echo "Note: the video player needs your system's libmpv (Fedora: sudo dnf install mpv-libs," \
    "Debian/Ubuntu: sudo apt install libmpv2)."
fi
