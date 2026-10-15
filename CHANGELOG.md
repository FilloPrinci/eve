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
