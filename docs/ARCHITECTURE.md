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

## Current state (M3 — Render engine)

| Package | Status |
|---|---|
| `easy_video_editor.core` | Project model, commands, session (undo/redo), rules, validation, persistence. |
| `easy_video_editor.media` | Binary discovery, process runner, ffprobe, fingerprints, relinking, proxies, thumbnail strips, waveforms, system check. |
| `easy_video_editor.media.render` | Edit timeline → render graph → ffmpeg; final and draft renders. |
| `easy_video_editor.cli` | `eve` entry point (`cli/main.py`): `doctor`, `new`, `import`, `relink`, `validate`, `render`. |
| `easy_video_editor.ui` | `eve-gui` entry point (`ui/app.py`) opening an empty `MainWindow`. |
| `analysis`, `ai`, `mcp` | Empty packages. |

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
| `core/progress.py` | `Progress` callback protocol, `CancellationToken`, `sub_progress`. |
| `core/jobs.py` | `JobManager`: background jobs on worker threads with state, progress, ETA and cancellation. Listeners run on worker threads; the UI must marshal them to its own thread. |

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

## Media

| Module | Responsibility |
|---|---|
| `media/binaries.py` | Finds ffmpeg, ffprobe and libmpv: settings override → bundled with a frozen release → system `PATH`. |
| `media/process.py` | `run()`: argument lists only, logs command line, exit code, duration, stdout/stderr; terminates the process on cancellation. `FfmpegProgress` parses `-progress pipe:1`. |
| `media/probe.py` | ffprobe JSON → `ProbeResult` (duration, display size with rotation, frame rate, VFR flag, audio). |
| `media/fingerprint.py` | `v1:<size>:<mtime_ns>:<sha256 of first/middle/last MiB>`. The *content key* (size + hash) ignores location and mtime. |
| `media/relink.py` | Detects missing/changed sources; finds moved files by content (same name first). |
| `media/cache.py` | Paths of generated files inside the project, named by content key so they survive moves. |
| `media/generate.py` | Proxies (H.264, CFR at the source rate, ≤ 540p, never upscaled, keyframe every second), thumbnail strips (one JPEG + JSON layout; thumbnail *i* shows the frame displayed at *i* × interval), waveforms (min/max peaks, 100 per second, `.npy`). Writes go to a temporary file renamed on success. |
| `media/importer.py` | Builds the `AddClip` command from probe + fingerprint; generates missing media for a clip. |
| `media/doctor.py` | System check with copy-pasteable fixes per platform (Fedora, Debian/Ubuntu, Arch, Windows). |

## Render engine (`media/render/`)

```
EditTimeline ──build.py──► RenderGraph ──ffmpeg.py──► filter script + argument list ──renderer.py──► ffmpeg
```

- **`graph.py`** — the intermediate representation: inputs, clip nodes (trim via input-level `-ss/-t`,
  speed, reframe plan, fades), the end card, joins (concat or crossfade), texts, logo, music cues and
  the loudness target. Lengths are in output frames and audio samples.
- **`build.py`** — compiles a timeline for an output preset. Core lengths come from cumulative frame
  boundaries, so rounding never accumulates and the output lasts exactly the timeline duration on the
  frame grid. Crossfade handles follow the convention of `core/rules.py` (centred, scaled by speed), so
  the frames read are exactly the frames validated against red zones. Dips and flashes are fades and
  read no extra frames.
- **`ffmpeg.py`** — pure translation to a filter graph. Each clip is normalised to the output size,
  frame rate, timebase and pixel format and cut/padded to its exact frame count; joins are chained
  pairwise (`concat` or `xfade`/`acrossfade`). Audio: per-clip gain and `atempo`, music cues with fades
  and delays, ducking with `sidechaincompress`, then `loudnorm`. Texts and fonts are files in the work
  folder referenced by relative names, so user text is never escaped into the graph.
- **`renderer.py`** — final validation, music probing, a temporary work folder under
  `<project>/cache/render/`, the loudness measuring pass (audio-only graph), the encode with progress,
  atomic rename into place, cleanup (also on failure or cancellation).
- Golden tests (`tests/unit/media/golden/`) pin the generated filter graphs and arguments; regenerate them
  with `EVE_UPDATE_GOLDEN=1 uv run pytest tests/unit/media/test_render_build.py` and review the diff.
- Integration tests render a synthetic clip whose frames encode their own index in grey bands and check,
  frame by frame, which source frame every output frame comes from.

`logs.py` configures logging: a rotating `eve.log` in the user log folder (`platformdirs`) and the
console.

## Tooling

- `uv` manages the environment; `uv.lock` is committed.
- `ruff` for lint and formatting, `mypy` (strict on `core`, `media`, `analysis`, `ai`).
- `pytest` + `pytest-qt`; GUI tests run with `QT_QPA_PLATFORM=offscreen` (set in `tests/conftest.py`).
- CI (`.github/workflows/ci.yml`): lint + type check on Linux; tests on Linux and Windows.
  `release.yml` is added in M9.
