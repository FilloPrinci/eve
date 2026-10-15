# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
- Bundled Anton font (SIL Open Font License).
