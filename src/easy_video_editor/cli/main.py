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
from easy_video_editor.core.model import Origin, OutputPreset, Project
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
        self._done_message: str | None = None

    def __call__(self, fraction: float | None, message: str = "") -> None:
        now = time.monotonic()
        if fraction is not None and fraction < 1.0 and now - self._last < 0.2:
            return
        self._last = now
        if fraction is not None and fraction >= 1.0 and message == self._done_message:
            return
        if fraction is not None and fraction >= 1.0:
            self._done_message = message
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
    from easy_video_editor.ai.claude_code import find_claude
    from easy_video_editor.appsettings import load_app_settings
    from easy_video_editor.media.doctor import Status, run_checks

    path = load_app_settings().ai.claude_path
    checks = run_checks(claude=find_claude(Path(path) if path else None))
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


def _selected_presets(project: Project, preset_ids: list[str] | None) -> list[OutputPreset]:
    ids = preset_ids or project.settings.enabled_preset_ids
    if not ids:
        raise EveError("No output preset selected. Use --preset or enable one in the settings.")
    return [project.settings.preset(preset_id) for preset_id in ids]


def cmd_validate(args: argparse.Namespace) -> int:
    from easy_video_editor.core.validation import validate_for_render
    from easy_video_editor.media.render.build import RenderError
    from easy_video_editor.media.render.renderer import timeline_for

    project = load_project(args.project)
    failed = False
    for preset in _selected_presets(project, args.preset):
        try:
            report = validate_for_render(project, timeline_for(project, preset), preset)
        except RenderError as error:
            print(f"{preset.name}: error: {error}")
            failed = True
            continue
        if not report.issues:
            print(f"{preset.name}: OK")
        for issue in report.issues:
            print(f"{preset.name}: {issue.severity.value}: {issue.message}")
        failed |= not report.ok
    return EXIT_ERROR if failed else EXIT_OK


def cmd_render(args: argparse.Namespace) -> int:
    from easy_video_editor.analysis.follow import follow_paths
    from easy_video_editor.media.binaries import Toolchain
    from easy_video_editor.media.cache import MediaCache
    from easy_video_editor.media.render.build import RenderOptions
    from easy_video_editor.media.render.renderer import ValidationFailed, render_timeline

    toolchain = Toolchain.discover()
    project = load_project(args.project)
    output_dir = args.output or args.project / "exports"
    options = RenderOptions(follow_paths=follow_paths(project, MediaCache(args.project)))
    for preset in _selected_presets(project, args.preset):
        suffix = "-draft" if args.draft else ""
        output = output_dir / f"{project.name}-{preset.id}{suffix}.mp4"
        try:
            render_timeline(
                project,
                args.project,
                preset,
                output,
                toolchain,
                options=options,
                draft=args.draft,
                progress=TerminalProgress(preset.name),
            )
        except ValidationFailed as error:
            for issue in error.report.errors:
                print(f"{preset.name}: {issue.message}", file=sys.stderr)
            raise
        print(output)
    return EXIT_OK


def _run_phase(args: argparse.Namespace, phase_name: str) -> int:
    from easy_video_editor.analysis.pipeline import Phase, plan_phase
    from easy_video_editor.analysis.plan import apply_plan
    from easy_video_editor.media.binaries import Toolchain
    from easy_video_editor.media.cache import MediaCache

    if args.mode == "ai":
        return _run_ai(args, "followup" if phase_name == "refresh" else phase_name)
    phase = Phase(phase_name)
    toolchain = Toolchain.discover()
    session = ProjectSession(load_project(args.project))
    plan = plan_phase(
        phase,
        session.project,
        MediaCache(args.project),
        toolchain,
        progress=TerminalProgress(phase.value.capitalize()),
    )
    outcome = apply_plan(session, plan, Origin.AUTO)
    for message in plan.messages + outcome.notices:
        print(message)
    for message in outcome.rejected:
        print(f"Rejected: {message}", file=sys.stderr)
    save_project(session.project, args.project)
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    return _run_phase(args, "analyze")


def cmd_refine(args: argparse.Namespace) -> int:
    return _run_phase(args, "refine")


def cmd_assemble(args: argparse.Namespace) -> int:
    return _run_phase(args, "assemble")


def cmd_refresh(args: argparse.Namespace) -> int:
    return _run_phase(args, "refresh")


def _run_ai(args: argparse.Namespace, phase_name: str, request: str | None = None) -> int:
    from easy_video_editor.ai.agent import AgentEvent
    from easy_video_editor.ai.runner import AiProviderKind, run_ai_phase
    from easy_video_editor.ai.tools import ToolPhase
    from easy_video_editor.analysis.pipeline import ensure_features
    from easy_video_editor.appsettings import load_app_settings
    from easy_video_editor.media.binaries import Toolchain
    from easy_video_editor.media.cache import MediaCache

    settings = load_app_settings().ai
    if args.provider:
        settings = settings.model_copy(update={"provider": AiProviderKind(args.provider)})
    toolchain = Toolchain.discover()
    project_dir = args.project.resolve()
    ensure_features(
        load_project(project_dir),
        MediaCache(project_dir),
        toolchain,
        TerminalProgress("Analysis"),
    )

    def show(event: AgentEvent) -> None:
        prefix = {"tool": "  → ", "error": "  ✗ "}.get(event.kind, "")
        print(f"{prefix}{event.message}", flush=True)

    result = run_ai_phase(
        ToolPhase(phase_name), project_dir, settings, show, request=request, toolchain=toolchain
    )
    usage = result.usage
    print(f"Tokens: {usage.input_tokens} in, {usage.output_tokens} out.", file=sys.stderr)
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def cmd_ask(args: argparse.Namespace) -> int:
    return _run_ai(args, "followup", args.request)


def cmd_mcp(args: argparse.Namespace) -> int:
    from easy_video_editor.ai.tools import ToolPhase
    from easy_video_editor.mcp.server import serve

    serve(args.project.resolve(), ToolPhase(args.phase), args.frame_budget)
    return EXIT_OK


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
    for name, handler, text in (
        ("analyze", cmd_analyze, "phase 1: find candidate (blue) zones"),
        ("refine", cmd_refine, "phase 2: refine highlight (yellow) zones"),
        ("assemble", cmd_assemble, "phase 3: assemble the edit timeline(s)"),
        ("refresh", cmd_refresh, "regenerate stale highlights and segments only"),
    ):
        phase = add(name, handler, text)
        phase.add_argument("project", type=Path, help="project folder (.eve)")
        phase.add_argument("--mode", choices=["auto", "ai"], default="auto")
        phase.add_argument(
            "--provider",
            choices=["claude_code", "anthropic"],
            help="AI provider for --mode ai (default: the one chosen in Preferences)",
        )

    ask = add("ask", cmd_ask, "ask the AI for a specific change to the project")
    ask.add_argument("project", type=Path, help="project folder (.eve)")
    ask.add_argument("request", help='what to change, e.g. "make the intro shorter"')
    ask.add_argument("--provider", choices=["claude_code", "anthropic"])

    server = add("mcp", cmd_mcp, "serve the project's tools to an MCP client (stdio)")
    server.add_argument("--project", type=Path, required=True, help="project folder (.eve)")
    server.add_argument(
        "--phase",
        choices=["analyze", "refine", "assemble", "followup"],
        default="followup",
        help="which tools to expose (default: all editing tools)",
    )
    server.add_argument("--frame-budget", type=int, help="maximum frames the client may view")

    validate = add("validate", cmd_validate, "run the final validation for output presets")
    validate.add_argument("project", type=Path, help="project folder (.eve)")
    validate.add_argument("--preset", action="append", help="preset id (default: enabled ones)")

    render = add("render", cmd_render, "render the edit timeline for output presets")
    render.add_argument("project", type=Path, help="project folder (.eve)")
    render.add_argument("--preset", action="append", help="preset id (default: enabled ones)")
    render.add_argument("--draft", action="store_true", help="fast low-resolution render")
    render.add_argument("--output", type=Path, help="output folder (default: <project>/exports)")
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
