# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- The brand kit editor shows a preview of the end card for YouTube (16:9), Shorts / Reels / TikTok
  (9:16) and Instagram feed (4:5), rendered exactly as in the exported video and updated while you
  edit.
- Click the end card on the edit timeline to edit it in the inspector: length, call to action, logo
  and background image, with a live preview in the timeline's format. Delete removes it.

### Fixed
- Rendering a video with crossfades failed with FFmpeg 7.1 (for example Fedora's RPM Fusion build)
  with "Conversion failed!".

## [0.1.0-alpha.5] - 2026-10-19

### Fixed
- AI mode changed nothing: Claude Code could start working before EVE's tools were ready (EVE takes a
  few seconds to start from the AppImage) and then finished without them. EVE now makes Claude Code
  wait for its tools, and a run in which Claude used none of them is reported as an error instead of
  a silent "done".
- Programs started by the Linux AppImage (FFmpeg, Claude Code) no longer inherit EVE's internal
  library path.

## [0.1.0-alpha.4] - 2026-10-19

### Changed
- EVE reopens the last project at start-up.

### Fixed
- Importing clips with no project open did nothing; it now says to create or open a project first.
- The import dialog hid videos with upper-case extensions on Linux (e.g. `CLIP.MP4`), and now also
  offers "All files".

## [0.1.0-alpha.3] - 2026-10-15

### Fixed
- Linux AppImage: the editor crashed at start-up in X11 sessions (in `libxkbcommon`), because a
  bundled X11 keyboard library did not match the system's. Release tests now also start the editor
  on an X server.

## [0.1.0-alpha.2] - 2026-10-15

### Fixed
- Linux AppImage: the video player failed with "libmpv could not be loaded" on current distributions
  (e.g. Fedora), because older copies of system libraries bundled in the AppImage were loaded first.
  The AppImage now uses the system's own libraries; each release is tested on Fedora before it is
  published.
- Leaving a zone's label field in the inspector without changing anything no longer locks the zone.
- Rendering a draft failed when the project was given with a relative path on the command line
  (e.g. `eve render my.eve --draft`).

## [0.1.0-alpha.1] - 2026-10-15

First test build. Not feature-complete: other AI providers (M7) and reloading changes made by other
programs while the editor is open (M8) are still missing.

### Added
- Release builds: a Windows zip with FFmpeg and libmpv included and a Linux AppImage with FFmpeg
  included, published with `SHA256SUMS` to GitHub Releases.
- Project bootstrap: repository layout, packaging metadata, CI on Linux and Windows.
- `eve --version` command-line entry point.
- `eve-gui` entry point that opens an empty main window.
- Core project model: clips, red/blue/yellow zones, edit timelines with segments, transitions,
  text overlays, music cues and end card, project settings and output presets (16:9, 9:16, 4:5).
- Undoable commands with transactions and history; red-zone, lock, tolerance and staleness rules.
- Final validation before render.
- Atomic project save/load with backup and schema versioning.
- `eve doctor`: checks ffmpeg, ffprobe, libmpv, GPU encoders and the OS keyring, with fix commands
  for Fedora, Debian/Ubuntu, Arch and Windows.
- `eve new`, `eve import` (probe, fingerprint, proxies, thumbnail strips, waveforms) and
  `eve relink` (find moved source files by content).
- Background job system with progress, ETA and cancellation.
- Rotating log file in the user log folder.
- Render engine: cuts, speed changes, reframing (centre crop, follow crop, blurred background),
  crossfades, dips to black/white, flashes, text overlays, logo watermark, end card, music with fades,
  game-audio ducking and two-pass loudness normalisation (default -14 LUFS).
- Output presets 16:9, 9:16 and 4:5; draft renders from proxies.
- `eve validate` and `eve render`.
- Three selectable text looks (`classic`, `band`, `bold`) using the brand kit's accent colour.
- Graphical editor for manual editing: media bin with background import, player (libmpv) with source,
  instant edit preview and draft modes, source view with editable red/blue/yellow zone lanes, edit
  timeline with drag to reorder and trim, inspector, project settings, brand kits, preferences,
  history with undo/redo, job list, log console (F12), export dialog with validation, system check.
- Autosave of projects; application settings and brand kits in the user config folder.
- Analysis of clips (motion, scene cuts, motion centroid, audio energy and onsets) with caching, and
  beat detection on music.
- Auto mode: Phase 1 (candidate zones), Phase 2 (highlights snapped to the music beat) and Phase 3
  (assembled timelines per aspect ratio with mood-based transitions and speed), plus "Refresh stale",
  from the Agent panel, the Auto menu and `eve analyze/refine/assemble/refresh`.
- Follow-crop reframing uses the motion path found by the analysis.
- Bundled Anton font (SIL Open Font License).
- AI mode with Claude: phases 1–3, Refresh stale and targeted requests ("Ask") run with the user's own
  Claude Code and Claude subscription (no API key), or optionally with an Anthropic API key stored in
  the system keyring. The project updates live, editing pauses during a run, Stop cancels it, and each
  run is one undo step. Token usage is shown.
- `eve analyze/refine/assemble/refresh --mode ai`, `eve ask` and `eve mcp` (an MCP server exposing
  EVE's editing tools, restricted per phase, to MCP clients such as Claude Code).
- `eve doctor` and the system check report Claude Code and how to install it.
