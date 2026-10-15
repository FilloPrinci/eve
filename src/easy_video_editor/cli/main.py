"""Entry point of the `eve` command (SPEC §10)."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from easy_video_editor import __version__
from easy_video_editor.core.errors import EveError
from easy_video_editor.core.model import Origin, Project
from easy_video_editor.core.persistence import (
    create_project,
    load_project,
    project_dir_for,
    save_project,
)
from easy_video_editor.core.progress import CancelledError
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.logs import setup_logging

EXIT_OK, EXIT_ERROR, EXIT_CANCELLED = 0, 1, 130

log = logging.getLogger(__name__)


class TerminalProgress:
    """Prints progress on one line of stderr (rewritten in place on a terminal)."""

    def __init__(self, title: str, stream: TextIO | None = None) -> None:
        self._title = title
        self._stream = stream or sys.stderr
        self._last = 0.0
        self._interactive = self._stream.isatty()

    def __call__(self, fraction: float | None, message: str = "") -> None:
        now = time.monotonic()
        if fraction is not None and fraction < 1.0 and now - self._last < 0.2:
            return
        self._last = now
        percent = "" if fraction is None else f"{fraction * 100:3.0f}% "
        text = f"{self._title}: {percent}{message}"
        if self._interactive:
            self._stream.write(f"\r\033[K{text}")
            if fraction is not None and fraction >= 1.0:
                self._stream.write("\n")
        elif fraction is None or fraction >= 1.0:
            self._stream.write(text + "\n")
        self._stream.flush()


# --- Commands ----------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    from easy_video_editor.media.doctor import Status, run_checks

    checks = run_checks()
    symbols = {Status.OK: "OK  ", Status.WARNING: "WARN", Status.ERROR: "FAIL"}
    for check in checks:
        print(f"[{symbols[check.status]}] {check.name}: {check.detail}")
        if check.fix and check.status is not Status.OK:
            for line in check.fix.splitlines():
                print(f"       fix: {line}")
    return EXIT_ERROR if any(check.status is Status.ERROR for check in checks) else EXIT_OK


def cmd_new(args: argparse.Namespace) -> int:
    directory = project_dir_for(args.dir, args.name)
    create_project(directory, Project(name=args.name))
    print(directory)
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    from easy_video_editor.media.binaries import Toolchain
    from easy_video_editor.media.cache import MediaCache, content_key
    from easy_video_editor.media.fingerprint import Fingerprint
    from easy_video_editor.media.importer import add_clip_command, prepare_clip_media

    toolchain = Toolchain.discover()
    session = ProjectSession(load_project(args.project))
    cache = MediaCache(args.project)
    known = {content_key(clip) for clip in session.project.clips}
    added = []
    for path in args.clips:
        command, _ = add_clip_command(path, toolchain)
        key = Fingerprint.parse(command.fingerprint or "").content_key
        if key in known:
            print(f"Skipped {path}: already in the project.", file=sys.stderr)
            continue
        known.add(key)
        clip_id = session.execute(command, Origin.USER).created_ids[0]
        clip = session.project.clip(clip_id)
        added.append(clip_id)
        save_project(session.project, args.project)
        print(f"Imported {clip.name} as {clip.id}")
    if args.no_media:
        return EXIT_OK
    for clip_id in added:
        clip = session.project.clip(clip_id)
        prepare_clip_media(clip, cache, toolchain, TerminalProgress(clip.name))
    return EXIT_OK


def cmd_relink(args: argparse.Namespace) -> int:
    from easy_video_editor.core.commands import RelinkClip
    from easy_video_editor.media.fingerprint import fingerprint
    from easy_video_editor.media.relink import SourceState, check_source, find_candidates

    session = ProjectSession(load_project(args.project))
    search_dirs = args.search or [args.project.resolve().parent]
    unresolved = 0
    for clip in list(session.project.clips):
        state = check_source(clip)
        if state is SourceState.OK:
            continue
        candidates = find_candidates(clip, search_dirs)
        if not candidates:
            unresolved += 1
            print(f"{clip.name}: {state.value}, no matching file found.", file=sys.stderr)
            continue
        new_path = candidates[0]
        session.execute(
            RelinkClip(clip_id=clip.id, path=str(new_path), fingerprint=str(fingerprint(new_path))),
            Origin.USER,
        )
        print(f"{clip.name}: relinked to {new_path}")
    if session.can_undo:
        save_project(session.project, args.project)
    return EXIT_ERROR if unresolved else EXIT_OK


# --- Parser ------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eve",
        description="EVE — Easy Video Editor (command-line interface).",
    )
    parser.add_argument("--version", action="version", version=f"eve {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed logs")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    def add(
        name: str, handler: Callable[[argparse.Namespace], int], help: str
    ) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help, description=help)
        sub.set_defaults(handler=handler)
        return sub

    add("doctor", cmd_doctor, "check ffmpeg, ffprobe, libmpv, GPU encoders and the keyring")

    new = add("new", cmd_new, "create a new project folder <dir>/<name>.eve")
    new.add_argument("dir", type=Path)
    new.add_argument("--name", required=True)

    imp = add("import", cmd_import, "import clips and generate proxies, thumbnails and waveforms")
    imp.add_argument("project", type=Path, help="project folder (.eve)")
    imp.add_argument("clips", type=Path, nargs="+")
    imp.add_argument("--no-media", action="store_true", help="skip proxy/thumbnail generation")

    relink = add("relink", cmd_relink, "find moved source files by content and relink them")
    relink.add_argument("project", type=Path, help="project folder (.eve)")
    relink.add_argument(
        "--search",
        type=Path,
        action="append",
        help="folder to search (repeatable; default: the folder containing the project)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    log_file = setup_logging(logging.DEBUG if args.verbose else logging.WARNING)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (CancelledError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return EXIT_CANCELLED
    except EveError as error:
        print(f"Error: {error}", file=sys.stderr)
        if log_file is not None:
            print(f"Details: {log_file}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
