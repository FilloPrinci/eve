# EVE — Easy Video Editor

EVE turns raw gameplay recordings into short trailers for YouTube, Shorts, Reels and TikTok.
Mark what to exclude, let the AI (or the built-in Auto mode) find and trim the best moments,
preview the edit, tweak it, and render every format in one go. Works on Linux and Windows.

> Status: first release, 0.1.0. See the [roadmap](docs/SPEC.md#16-milestones-the-mvp-is-m0m9).

## Download
Pre-built binaries are on the [Releases page](https://github.com/FilloPrinci/eve/releases)
(early versions are marked *pre-release*):
- **Windows**: `EVE-<version>-windows-x64.zip`. Unzip it anywhere and run `EVE\eve-gui.exe`.
  FFmpeg and libmpv are included.
- **Linux, installed** (recommended): `EVE-<version>-linux-x64.tar.gz`. Extract it and run
  `./install.sh` inside the folder: EVE is installed for your user (no root needed) and appears in
  the application menu; `eve-gui` and `eve` work from a terminal. Run a newer `install.sh` to update.
- **Linux, portable**: `EVE-<version>-x86_64.AppImage`. Make it executable
  (`chmod +x EVE-*.AppImage`) and run it. Command-line use: `./EVE-<version>-x86_64.AppImage cli doctor`.

On Linux, FFmpeg is included and the video player uses your system's libmpv (Fedora:
`sudo dnf install mpv-libs`, Debian/Ubuntu: `sudo apt install libmpv2`).

Verify downloads with the `SHA256SUMS` file attached to each release. AI mode needs Claude Code
logged in with your Claude account; see the [user guide](docs/USER_GUIDE.md).

## Features
- Three-step workflow with colour-coded zones: exclude (red), candidates (blue), highlights (yellow)
- AI mode with Claude, through your own Claude subscription (Claude Code) or an Anthropic API key
- Auto mode (no AI) and full manual editing
- Instant preview and draft renders before the final export
- Output presets for 16:9, 9:16 and 4:5 with automatic reframing
- Brand kits (logo, title, colours, font, music) and an end card with a live preview
- Command-line interface and MCP server

Planned: more AI providers (OpenAI-compatible APIs, local models with Ollama or LM Studio).

## Build from source
Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg, libmpv.

    git clone <repo-url>
    cd eve
    uv sync
    uv run eve doctor
    uv run eve-gui

## Documentation
- [Specification](docs/SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [User guide](docs/USER_GUIDE.md)

## License
GPL-3.0-or-later. Bundled third-party components are listed in `packaging/third_party/`.
