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

## Current state (M1 — Core model)

| Package | Status |
|---|---|
| `easy_video_editor.core` | Project model, commands, session (undo/redo), rules, validation, persistence. |
| `easy_video_editor.cli` | `eve` entry point (`cli/main.py`); only `--version` so far. |
| `easy_video_editor.ui` | `eve-gui` entry point (`ui/app.py`) opening an empty `MainWindow`. |
| `media`, `analysis`, `ai`, `mcp` | Empty packages. |

## Core

| Module | Responsibility |
|---|---|
| `core/model.py` | Pydantic models: `Project`, `Clip`, `Zone`, `EditTimeline` (`Segment`, `Transition`, `TextOverlay`, `MusicCue`, `EndCard`), `ProjectSettings`, `OutputPreset`. Times are `int` microseconds. |
| `core/commands/` | One pydantic model per command (`CreateZone`, `AddSegment`, …) with an `apply(project, ctx)` method. Arguments therefore have a JSON Schema, to be reused by the tool registry (M6). |
| `core/session.py` | `ProjectSession`: the only way to change a project. Runs commands, groups them in transactions, keeps undo/redo history and notifies listeners. |
| `core/rules.py` | Red-zone exclusion (trim/split/remove), transition handles, staleness propagation and the project invariants. |
| `core/validation.py` | Final validation before render; independent of the invariants. |
| `core/persistence.py` | Project folders, atomic `project.json` writes with a `.bak` copy, schema migrations. |
| `core/timecode.py`, `core/ranges.py` | Microsecond/frame conversions and half-open range arithmetic. |

### How a command runs

1. `ProjectSession.execute(command, origin)` deep-copies the current project.
2. `command.apply(copy, ctx)` mutates the copy. Transition rules that depend on *who* acts
   (red zones are user-only, locked zones are off-limits to AI/Auto, tolerance, staleness)
   are checked here and raise `CommandError`.
3. The copy is re-validated with pydantic and checked against the state invariants of
   `core/rules.py` (no zone or segment on red frames, handles included; references valid;
   ranges inside clips). Any violation rejects the command and the project is unchanged.
4. The copy becomes the current project, `revision` is incremented and a history entry is pushed
   (or appended as a step to the open transaction).

Undo/redo is snapshot-based: entries store the project before and after. Projects contain no
media data, so snapshots are small, and undo is exact by construction (including side effects
such as zones split by a red zone).

`ProjectSession.project` returns the live model; read it again after each command instead of
keeping references across commands.

## Tooling

- `uv` manages the environment; `uv.lock` is committed.
- `ruff` for lint and formatting, `mypy` (strict on `core`, `media`, `analysis`, `ai`).
- `pytest` + `pytest-qt`; GUI tests run with `QT_QPA_PLATFORM=offscreen` (set in `tests/conftest.py`).
- CI (`.github/workflows/ci.yml`): lint + type check on Linux; tests on Linux and Windows.
  `release.yml` is added in M9.
