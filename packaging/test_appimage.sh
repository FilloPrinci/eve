#!/bin/sh
# Checks a release AppImage on a clean, current distribution (the release workflow runs this in a
# Fedora container): the system libmpv must load next to EVE's bundled libraries, and the Auto
# pipeline must run end to end with the bundled FFmpeg.
# Usage: test_appimage.sh <path to EVE-*.AppImage>
set -eu
dnf install -y --setopt=install_weak_deps=False mpv-libs mesa-libEGL mesa-libGL fontconfig \
    libxkbcommon dbus-libs > /dev/null
work=$(mktemp -d)
cp "$1" "$work/EVE.AppImage"
cd "$work"
chmod +x EVE.AppImage
./EVE.AppImage --appimage-extract > /dev/null  # no FUSE in containers
export QT_QPA_PLATFORM=offscreen
echo "Libraries the bundle needs but this system lacks:"
find squashfs-root/usr/lib/eve/_internal -name "*.so*" -type f | while read -r library; do
    LD_LIBRARY_PATH=squashfs-root/usr/lib/eve/_internal ldd "$library" 2>/dev/null \
        | grep "not found" | sed "s|^|  $(basename "$library"):|"
done | sort -u | grep -v -E "tbbpool|qgtk3|libQt6(Pdf|Quick|Qml|VirtualKeyboard|WlShell)|egldeviceintegrations|qxcb|xcbglintegrations" || true
eve() { ./squashfs-root/AppRun cli "$@"; }
eve --version
eve doctor
./squashfs-root/AppRun --smoke-test  # fails if the system libmpv cannot be used
./squashfs-root/usr/lib/eve/bin/ffmpeg -v error -f lavfi -i "testsrc2=s=640x360:r=25:d=12" \
    -f lavfi -i "sine=frequency=440:d=12" -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
    -c:a aac clip.mp4
eve new . --name Smoke
eve import Smoke.eve clip.mp4
eve analyze Smoke.eve
eve refine Smoke.eve
eve assemble Smoke.eve
eve render Smoke.eve --draft
ls Smoke.eve/exports
echo "AppImage OK on $(. /etc/os-release && echo "$PRETTY_NAME")"
