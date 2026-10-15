# EVE — Easy Video Editor

EVE turns raw gameplay recordings into short trailers for YouTube, Shorts, Reels and TikTok.
Mark what to exclude, let the AI (or the built-in Auto mode) find and trim the best moments,
preview the edit, tweak it, and render every format in one go. Works on Linux and Windows.

> Status: early development. See the [roadmap](docs/SPEC.md#16-milestones-the-mvp-is-m0m9).

## Download
Pre-built binaries will be available on the [Releases page](../../releases/latest):
- Windows: `EVE-<version>-windows-x64.zip`
- Linux: `EVE-<version>-x86_64.AppImage`

Verify downloads with the `SHA256SUMS` file attached to each release.

## Features (planned for v0.1)
- Three-step workflow with colour-coded zones: exclude (red), candidates (blue), highlights (yellow)
- AI providers: Anthropic, OpenAI and OpenAI-compatible local models (Ollama, LM Studio)
- Auto mode (no AI) and full manual editing
- Instant preview and draft renders before the final export
- Output presets for 16:9, 9:16 and 4:5 with automatic reframing
- Command-line interface and MCP server

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
