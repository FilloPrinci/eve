# Third-party software in EVE releases

EVE is free software under the GNU General Public License v3.0 or later (see `LICENSE`).
Release builds include the following third-party software, unmodified:

| Component | Licence | Source |
|---|---|---|
| FFmpeg and FFprobe (GPL build by BtbN) | GPL-3.0-or-later (see `licenses/ffmpeg/LICENSE.txt`) | https://ffmpeg.org/, builds: https://github.com/BtbN/FFmpeg-Builds |
| libmpv (Windows only; GPL build by zhongfly) | GPL-2.0-or-later | https://mpv.io/, builds: https://github.com/zhongfly/mpv-winbuild |
| Python and the Python packages listed in `licenses/python/INDEX.txt` (including Qt for Python / PySide6, LGPL-3.0) | see each package's folder in `licenses/python/` | https://pypi.org/ |
| Anton font | SIL Open Font License 1.1 (`Anton-OFL.txt` next to the font) | https://fonts.google.com/specimen/Anton |

The source code of each component is available from the links above. On request, the
maintainers of EVE will provide the corresponding source code of the bundled binaries.
