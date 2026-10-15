# CLAUDE.md — working rules for EVE

Read `docs/SPEC.md` before any change. It is the source of truth; update it in the same commit when a
design decision changes.

## Non-negotiable rules
1. Everything is in English: code, identifiers, comments, docstrings, commit messages, docs, UI strings,
   log messages, prompts, issues and release notes.
2. All project mutations go through commands (`core/commands`). Never mutate the model directly from UI,
   AI or CLI code.
3. Red-zone and lock rules (SPEC §4.4) are enforced in `core`, covered by tests, and never relaxed.
   The AI has no tool that can create, modify or delete red zones.
4. `core`, `media`, `analysis` and `ai` never import Qt.
5. External processes: argument lists only, never `shell=True`; capture stdout/stderr/exit code to the log.
6. Paths via `pathlib`; no hardcoded separators; remember Linux is case-sensitive.
7. Times are integer microseconds in the model; convert only at boundaries.
8. No secrets in the repo, in project files, in settings files or in logs. API keys live in the OS keyring.
9. No real AI API calls in tests; use `FakeProvider`.
10. Non-instant operations show an activity indicator; operations that may exceed ~10 s show a progress bar
    and are cancellable. The UI thread never blocks.
11. Never commit binaries, generated media, proxies or caches.

## Workflow
- Follow the milestone plan in `docs/SPEC.md` §16, one milestone at a time.
- Small, focused commits using Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `build:`, `ci:`, `chore:`).
- Before moving on: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest` all green.
- Add an entry to `CHANGELOG.md` under "Unreleased" for user-visible changes.
- Justify every new dependency in the commit message and check its licence.

## Useful commands
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run eve doctor
QT_QPA_PLATFORM=offscreen uv run pytest tests/gui
uv run eve-gui
