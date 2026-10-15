"""MCP server (SPEC §11): exposes the tool registry to MCP clients over stdio.

Used by EVE's Claude Code provider and by any MCP client (Claude Code, Claude Desktop) the user
connects to a project. It works on the project folder: before every call it reloads the project
if it changed on disk, and after every change it saves atomically, so the GUI can follow along.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from easy_video_editor import __version__
from easy_video_editor.ai.tools import ToolError, ToolPhase, tools_for
from easy_video_editor.ai.workspace import Workspace

log = logging.getLogger(__name__)

INSTRUCTIONS = (
    "EVE edits short promotional videos from gameplay clips. Zones mark time ranges of clips: "
    "exclude (red) zones must never be used, candidate (blue) zones are rough interesting "
    "regions, highlight (yellow) zones are trimmed best moments; edit timelines assemble "
    "highlights into the final video. Locked zones belong to the user and cannot be changed. "
    "Start with get_project_state. Times are in seconds."
)


class ProjectTools:
    """The registry bound to a project folder, in MCP terms."""

    def __init__(
        self, project_dir: Path, phase: ToolPhase, frame_budget: int | None = None
    ) -> None:
        self.workspace = Workspace(project_dir, phase, frame_budget=frame_budget)
        self.phase = phase

    def list(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name, description=spec.description, input_schema=spec.input_schema()
            )
            for spec in tools_for(self.phase)
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        try:
            result = self.workspace.call(name, arguments)
        except ToolError as error:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {error}")], is_error=True
            )
        content: list[Any] = [types.TextContent(type="text", text=result.text)]
        for image in result.images:
            content.append(
                types.ImageContent(
                    type="image",
                    data=base64.b64encode(image.data).decode("ascii"),
                    mime_type=image.mime_type,
                )
            )
        return types.CallToolResult(content=content, is_error=False)


def build_server(tools: ProjectTools) -> Server[Any]:
    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools.list())

    async def call_tool_handler(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        arguments = dict(params.arguments or {})
        return await anyio.to_thread.run_sync(tools.call, params.name, arguments)

    return Server(
        "eve",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool_handler,
    )


def serve(
    project_dir: Path, phase: ToolPhase = ToolPhase.FOLLOWUP, frame_budget: int | None = None
) -> None:
    tools = ProjectTools(project_dir, phase, frame_budget)
    server = build_server(tools)

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(main)
