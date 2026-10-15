# Architecture

This document describes the code as it is today. The design target is `docs/SPEC.md` §7.

## Layers

```
ui  (PySide6)            ──► uses everything below; no business logic
cli (eve command)        ──► headless access to core/media/analysis/ai
mcp (MCP server)         ──► exposes the tool registry to external MCP clients
ai  (providers, agent)   ──► talks to models; only acts through the tool registry
analysis                 ──► numeric features, scoring, Auto-mode heuristics
media                    ──► ffprobe, proxies, thumbnails, waveforms, render graph → ffmpeg
core                     ──► project model, zones, edit timeline, commands, undo/redo, validation, persistence
```

`core`, `media`, `analysis` and `ai` must not import Qt. This is enforced by
`tests/unit/test_layering.py`.

## Current state (M0 — Bootstrap)

| Package | Status |
|---|---|
| `easy_video_editor.cli` | `eve` entry point (`cli/main.py`); only `--version` so far. |
| `easy_video_editor.ui` | `eve-gui` entry point (`ui/app.py`) opening an empty `MainWindow`. |
| `core`, `media`, `analysis`, `ai`, `mcp` | Empty packages. |

## Tooling

- `uv` manages the environment; `uv.lock` is committed.
- `ruff` for lint and formatting, `mypy` (strict on `core`, `media`, `analysis`, `ai`).
- `pytest` + `pytest-qt`; GUI tests run with `QT_QPA_PLATFORM=offscreen` (set in `tests/conftest.py`).
- CI (`.github/workflows/ci.yml`): lint + type check on Linux; tests on Linux and Windows.
  `release.yml` is added in M9.
