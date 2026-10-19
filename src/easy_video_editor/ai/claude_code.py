"""Claude through the user's own Claude Code (their Claude subscription; no API key).

EVE runs ``claude -p`` headless with every built-in tool disabled and only EVE's MCP server
attached, restricted to the tools of the current phase. Claude Code's stream of JSON events becomes
the activity shown to the user. The MCP server edits the project file; EVE follows the changes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from easy_video_editor.ai.agent import AgentEvent, AgentResult, EventSink, Usage, describe_call
from easy_video_editor.ai.tools import ToolPhase
from easy_video_editor.core.progress import CancellationToken
from easy_video_editor.media.process import run

log = logging.getLogger(__name__)

SERVER_NAME = "eve"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
CLAUDE_ENVIRONMENT = {
    # Wait for EVE's MCP server before the first model request; otherwise Claude Code starts
    # without EVE's tools while the server is still loading, and can change nothing.
    "MCP_CONNECTION_NONBLOCKING": "false",
    "MCP_TIMEOUT": "120000",  # ms; a cold start from an AppImage can take a while
}
NO_TOOLS_ERROR = (
    "Claude Code finished without using EVE's tools, so nothing was changed. "
    "Update Claude Code (run 'claude update') and try again; see the log for details."
)


def find_claude(override: Path | None = None) -> Path | None:
    """The ``claude`` executable: settings override, PATH, then the usual install locations."""
    if override is not None:
        return override if override.is_file() else None
    found = shutil.which("claude")
    if found:
        return Path(found)
    home = Path.home()
    candidates = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude"]
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home))
        candidates = [home / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd"]
    return next((path for path in candidates if path.is_file()), None)


def mcp_server_command(project_dir: Path, phase: ToolPhase, frame_budget: int) -> dict[str, Any]:
    """How Claude Code starts EVE's MCP server for this project and phase."""
    args = [
        "mcp",
        "--project",
        str(project_dir),
        "--phase",
        phase.value,
        "--frame-budget",
        str(frame_budget),
    ]
    if getattr(sys, "frozen", False):
        # Releases ship the `eve` command-line program next to the GUI executable.
        name = "eve.exe" if sys.platform == "win32" else "eve"
        return {"command": str(Path(sys.executable).with_name(name)), "args": args}
    return {"command": sys.executable, "args": ["-m", "easy_video_editor.mcp", *args[1:]]}


def build_command(
    claude: Path, prompt: str, system: str, mcp_config: Path, model: str | None
) -> list[str]:
    command = [
        str(claude),
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--tools",
        "",
        "--allowedTools",
        f"mcp__{SERVER_NAME}",
        "--append-system-prompt",
        system,
        "--no-session-persistence",
    ]
    if model:
        command += ["--model", model]
    return command


class StreamParser:
    """Turns Claude Code's ``stream-json`` lines into activity events and a final result."""

    def __init__(self, on_event: EventSink) -> None:
        self.on_event = on_event
        self.result = AgentResult()
        self.finished = False
        self.server_status: str | None = None
        self.tool_calls = 0

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            servers = {s.get("name"): s.get("status") for s in event.get("mcp_servers") or []}
            self.server_status = servers.get(SERVER_NAME)
            tools = [t for t in event.get("tools") or [] if str(t).startswith(TOOL_PREFIX)]
            log.info(
                "Claude Code %s: EVE tools %s (%d available), model %s",
                event.get("claude_code_version", "?"),
                self.server_status,
                len(tools),
                event.get("model"),
            )
            model = event.get("model")
            if model:
                self.on_event(AgentEvent("text", f"Claude Code is using {model}."))
        elif kind == "assistant":
            self.result.steps += 1
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    self.on_event(AgentEvent("text", block["text"].strip()))
                elif block.get("type") == "tool_use":
                    if str(block.get("name", "")).startswith(TOOL_PREFIX):
                        self.tool_calls += 1
                    name = str(block.get("name", "")).removeprefix(TOOL_PREFIX)
                    self.on_event(AgentEvent("tool", describe_call(name, block.get("input") or {})))
        elif kind == "user":
            for block in event.get("message", {}).get("content", []):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("is_error")
                ):
                    self.on_event(AgentEvent("error", _text_of(block.get("content"))[:300]))
        elif kind == "result":
            self.finished = True
            usage = event.get("usage") or {}
            self.result.usage = Usage(
                int(usage.get("input_tokens", 0))
                + int(usage.get("cache_read_input_tokens", 0))
                + int(usage.get("cache_creation_input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
                event.get("total_cost_usd"),
            )
            text = str(event.get("result") or "")
            log.info("Claude Code finished: %s, %d EVE tool call(s)", event.get("subtype"),
                     self.tool_calls)  # fmt: skip
            if event.get("is_error") or event.get("subtype") != "success":
                self.result.error = text or f"Claude Code stopped ({event.get('subtype')})."
            elif self.tool_calls == 0:
                status = f" (EVE's tools: {self.server_status})" if self.server_status else ""
                self.result.error = NO_TOOLS_ERROR.replace(", so", f"{status}, so", 1)
                self.result.summary = text
            else:
                self.result.summary = text


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def run_claude_code(
    claude: Path,
    project_dir: Path,
    phase: ToolPhase,
    prompt: str,
    system: str,
    model: str | None = None,
    on_event: EventSink = lambda event: None,
    token: CancellationToken | None = None,
    frame_budget: int = 40,
    runner: Callable[..., Any] = run,
) -> AgentResult:
    """Run one phase with Claude Code. The project must be saved on disk before calling."""
    config = {"mcpServers": {SERVER_NAME: mcp_server_command(project_dir, phase, frame_budget)}}
    parser = StreamParser(on_event)
    with tempfile.TemporaryDirectory(prefix="eve-claude-") as folder:
        config_path = Path(folder) / "mcp.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        process = runner(
            build_command(claude, prompt, system, config_path, model),
            token=token,
            on_line=parser.feed,
            check=False,
            cwd=Path(folder),
            log_stdout=False,
            env=CLAUDE_ENVIRONMENT,
        )
    if not parser.finished:
        last = (process.stderr or "").strip().splitlines()[-1:] or ["no output"]
        parser.result.error = (
            f"Claude Code did not finish (exit code {process.returncode}): {last[0]}"
        )
    return parser.result
