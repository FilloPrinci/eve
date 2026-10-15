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

## Current state (M6 — AI with Claude)

| Package | Status |
|---|---|
| `easy_video_editor.core` | Project model, commands, session (undo/redo), rules, validation, persistence. |
| `easy_video_editor.media` | Binary discovery, process runner, ffprobe, fingerprints, relinking, proxies, thumbnail strips, waveforms, system check. |
| `easy_video_editor.media.render` | Edit timeline → render graph → ffmpeg; final and draft renders. |
| `easy_video_editor.cli` | `eve` entry point (`cli/main.py`): `doctor`, `new`, `import`, `relink`, `analyze`, `refine`, `assemble`, `refresh`, `ask`, `mcp`, `validate`, `render`. |
| `easy_video_editor.ui` | PySide6 GUI for manual editing (see below). |
| `easy_video_editor.appsettings` | Application settings (binary paths, proxy height, brand kits, recent projects) in the user config folder. |
| `easy_video_editor.analysis` | Numeric features, beat detection, scoring, Auto-mode planners for phases 1–3 and "Refresh stale", follow-crop paths. |
| `easy_video_editor.ai` | Tool registry, workspace, agent loop, providers (Claude Code, Anthropic API, fake), prompts, keyring access. |
| `easy_video_editor.mcp` | stdio MCP server over the tool registry (`eve mcp`). |

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

## Analysis and Auto mode (`analysis/`)

| Module | Responsibility |
|---|---|
| `features.py` | One ffmpeg pass for video (64×36 grey at 10 Hz → motion, motion centroid, scene cuts by histogram distance) and one for audio (RMS energy, librosa onset strength). Cached as `cache/<content key>_features_v1.npz`. |
| `beats.py` | librosa beat tracking on the music; the beat period is the median beat interval. Cached by content. |
| `scoring.py` | Robust normalisation, mood weights, smoothing (edge-preserving), masking of red/locked ranges. |
| `auto.py` | Pure planners: `plan_phase1` (candidates from score runs), `plan_phase2` (best shot-length windows inside candidates ± tolerance, priorities by rank), `plan_phase3` (one timeline per aspect family, hook/build-up/climax, mood transitions and speeds, beat-length shots), `plan_refresh_stale`. |
| `plan.py` | `Plan` (commands + activity messages) and `apply_plan`: one transaction = one undo step with origin `auto`; optional commands (e.g. transitions that would need red frames) may be rejected silently. |
| `pipeline.py` | Runs what a phase needs (features, beats) and returns its plan. |
| `follow.py` | Smoothed motion paths for follow-crop renders, from cached features. |

Plans are computed off the UI thread from a project snapshot and applied on the UI thread.

## AI (`ai/`) and MCP (`mcp/`)

| Module | Responsibility |
|---|---|
| `tools.py` | The tool registry: typed arguments (pydantic → JSON Schema, no extra fields), the phases each tool belongs to, read tools (project state, zones, features, candidate windows, frames, beats, timelines) and thin wrappers over commands. There is no red-zone tool; frames inside red zones are refused; a per-run frame budget limits image requests. All AI changes have origin `ai`, so the core's lock and red-zone rules apply. |
| `workspace.py` | A project folder the tools work on: reloads `project.json` when it changed on disk before each call and saves atomically after each change. Shared by the MCP server and the in-process agent. |
| `agent.py` | Provider-neutral agent loop (`run_agent`: tool calls, `tool_result` blocks, images only for vision models, max steps, cancellation, usage), `FakeProvider` for tests and the optional `AnthropicProvider`. |
| `claude_code.py` | The default provider: runs the user's `claude -p` with built-in tools disabled and only `eve mcp --phase <phase>` attached; parses its `stream-json` output into activity events and usage. |
| `runner.py` | `AiSettings` (stored in the application settings) and `run_ai_phase`, which picks the provider and the phase prompt (`prompts/*.md`). |
| `secrets.py` | API keys in the OS keyring (service `EVE`). |
| `mcp/server.py` | `eve mcp`: lists the phase's tools and runs them through a `Workspace`; frames are returned as JPEG image content. |

How an AI run works in the GUI: the controller saves the project and calls
`ProjectSession.begin_external`; a background job runs the provider, which edits `project.json`
through the MCP server (Claude Code) or the workspace (API); the controller polls the file and shows
each saved state with `set_external`; local editing and undo are paused; `end_external` records the
whole run as one undo step with origin `ai`.

## User interface (`ui/`)

| Module | Responsibility |
|---|---|
| `controller.py` | `ProjectController`: the UI's only way to change a project. Wraps `ProjectSession`, reports rejected commands (`error`) and side effects (`notice`), tracks selection, current clip and timeline, and autosaves 0.8 s after each change. Changes made on the UI thread refresh at once; changes from worker threads are queued. |
| `jobs.py` | `JobRunner` bridges `core.jobs` to Qt (callbacks on the UI thread); `JobsPanel` lists jobs with progress, ETA and cancel. |
| `timescale.py` | Pure geometry (time ↔ pixels, zoom, frame/edge snapping, hit testing, drag limits), unit tested. |
| `source_view.py` | Thumbnails, waveform and the red/blue/yellow lanes; drag to create/move/resize zones, marks. |
| `timeline_view.py` | Segments, transitions, texts and music; drag to reorder and trim. |
| `inspector.py` | Forms for the selected zone, segment or text; every edit is a command. |
| `settings_panel.py` | Project settings, end card and music of the current timeline, brand kits, preferences. |
| `player.py` | libmpv software rendering into a `QImage` plus Qt-painted overlays; placeholder without libmpv. |
| `playback.py` | Source / Edit (EDL instant preview from proxies) / Draft modes; `DraftRenderer` re-renders drafts in the background (debounced, cancellable). |
| `automation_panel.py` | The *Agent* tab: Auto or AI mode, phase buttons, Run all, Refresh stale, AI requests, Stop, activity log and token usage. |
| `media_bin.py`, `history_panel.py`, `export_dialog.py`, `system_check.py`, `log_console.py` | The remaining panels and dialogs. |
| `main_window.py`, `app.py` | Layout, menus, shortcuts, status bar activity indicator; application start-up. |

GUI tests run offscreen (`tests/gui/`); the player test renders real frames with libmpv when it is
installed and checks that the frame shown is the one requested.

`logs.py` configures logging: a rotating `eve.log` in the user log folder (`platformdirs`) and the
console.

## Tooling

- `uv` manages the environment; `uv.lock` is committed.
- `ruff` for lint and formatting, `mypy` (strict on `core`, `media`, `analysis`, `ai`).
- `pytest` + `pytest-qt`; GUI tests run with `QT_QPA_PLATFORM=offscreen` (set in `tests/conftest.py`).
- CI (`.github/workflows/ci.yml`): lint + type check on Linux; tests on Linux and Windows.
  `release.yml` is added in M9.
