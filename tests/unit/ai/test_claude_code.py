"""The Claude Code provider: command line, stream parsing and a full run with a stand-in `claude`
that drives EVE's real MCP server."""

import json
import sys
from pathlib import Path
from typing import Any

from easy_video_editor.ai.agent import AgentEvent
from easy_video_editor.ai.claude_code import (
    StreamParser,
    build_command,
    find_claude,
    mcp_server_command,
    run_claude_code,
)
from easy_video_editor.ai.tools import ToolPhase
from easy_video_editor.core.model import Origin
from easy_video_editor.core.persistence import load_project
from easy_video_editor.media.process import ProcessResult, run

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


def test_command_line_allows_only_eves_tools(tmp_path: Path) -> None:
    command = build_command(Path("claude"), "prompt", "system", tmp_path / "mcp.json", "sonnet")
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--allowedTools") + 1] == "mcp__eve"
    assert "--strict-mcp-config" in command and "--no-session-persistence" in command
    assert command[-2:] == ["--model", "sonnet"]
    assert not any("dangerously" in part for part in command)
    assert "--model" not in build_command(Path("claude"), "p", "s", tmp_path, None)


def test_mcp_server_command_runs_this_eve(tmp_path: Path) -> None:
    server = mcp_server_command(tmp_path, ToolPhase.REFINE, 12)
    assert server["command"] == sys.executable
    assert server["args"][:2] == ["-m", "easy_video_editor.mcp"]
    assert server["args"][server["args"].index("--phase") + 1] == "refine"
    assert server["args"][server["args"].index("--frame-budget") + 1] == "12"


def test_find_claude_honours_the_override(tmp_path: Path) -> None:
    assert find_claude(tmp_path / "missing") is None
    program = tmp_path / "claude"
    program.write_text("")
    assert find_claude(program) == program


def test_stream_parser_reports_activity_and_usage() -> None:
    events: list[AgentEvent] = []
    parser = StreamParser(events.append)
    lines = [
        "not json",
        {"type": "system", "subtype": "init", "model": "m"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hi"},
            {"type": "tool_use", "name": "mcp__eve__get_zones", "input": {"kind": "candidate"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "No"}]}
        ]}},
        {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "",
         "usage": {"input_tokens": 3, "output_tokens": 4}},
    ]  # fmt: skip
    for line in lines:
        parser.feed(line if isinstance(line, str) else json.dumps(line))
    assert [(e.kind, e.message) for e in events] == [
        ("text", "Claude Code is using m."),
        ("text", "Hi"),
        ("tool", "get_zones(kind=candidate)"),
        ("error", "No"),
    ]
    assert parser.finished and parser.result.steps == 1
    assert parser.result.error == "Claude Code stopped (error_max_turns)."
    assert (parser.result.usage.input_tokens, parser.result.usage.output_tokens) == (3, 4)


def test_a_process_that_dies_is_an_error(tmp_path: Path) -> None:
    def runner(args: list[str], **kwargs: Any) -> ProcessResult:
        return ProcessResult(args, 1, "", "Not logged in\n", 0.1)

    result = run_claude_code(Path("claude"), tmp_path, ToolPhase.ANALYZE, "p", "s", runner=runner)
    assert result.error is not None and "Not logged in" in result.error


def test_full_run_through_the_mcp_server(project_dir: Path) -> None:
    def runner(args: list[str], **kwargs: Any) -> ProcessResult:
        return run([sys.executable, FAKE_CLAUDE, *args[1:]], **kwargs)

    events: list[AgentEvent] = []
    result = run_claude_code(
        Path("claude"), project_dir, ToolPhase.ANALYZE, "p", "s", None, events.append,
        runner=runner,
    )  # fmt: skip
    assert result.error is None, result.error
    assert result.summary == "Marked 1 zone."
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 5)
    assert ("text", "10 tools") in [(e.kind, e.message) for e in events]
    errors = [e.message for e in events if e.kind == "error"]
    assert len(errors) == 1 and "exclude" in errors[0].lower()
    zones = [z for z in load_project(project_dir).zones if z.origin is Origin.AI]
    assert [z.label for z in zones] == ["Launch"]
