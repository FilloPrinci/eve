#!/usr/bin/env bash
# Removes EVE installed by install.sh. Projects, settings and logs are kept.
set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
INSTALL_DIR="${DATA_HOME}/EVE"
BIN_DIR="${HOME}/.local/bin"

if [[ ! -f "${INSTALL_DIR}/AppRun" ]]; then
  echo "EVE is not installed in ${INSTALL_DIR}." >&2
  exit 1
fi
rm -f "${DATA_HOME}/applications/eve.desktop"
if [[ "$(readlink "${BIN_DIR}/eve-gui" 2> /dev/null)" == "${INSTALL_DIR}/AppRun" ]]; then
  rm -f "${BIN_DIR}/eve-gui"
fi
if grep -q "installed by EVE's install.sh" "${BIN_DIR}/eve" 2> /dev/null; then
  rm -f "${BIN_DIR}/eve"
fi
rm -rf "${INSTALL_DIR}"
if command -v update-desktop-database > /dev/null; then
  update-desktop-database "${DATA_HOME}/applications" > /dev/null 2>&1 || true
fi
echo "EVE was removed. Your projects, settings and logs were kept."
