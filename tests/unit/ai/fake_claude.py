"""Stands in for the `claude` program in tests: it reads the MCP configuration EVE passes, starts
EVE's MCP server like Claude Code would, makes a few tool calls and prints stream-json events.

Usage: python fake_claude.py <the arguments EVE passes to claude>
"""

import json
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


async def main(argv: list[str]) -> None:
    config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    server = config["mcpServers"]["eve"]
    params = StdioServerParameters(command=server["command"], args=server["args"])
    emit({"type": "system", "subtype": "init", "model": "fake-model"})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        emit(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": f"{len(tools)} tools"}]},
            }
        )
        calls = [
            (
                "create_candidate_zone",
                {"clip_id": "clip", "start_s": 2, "end_s": 6, "label": "Launch"},
            ),
            (
                "create_candidate_zone",
                {"clip_id": "clip", "start_s": 19, "end_s": 23, "label": "Red"},
            ),
        ]
        for index, (name, args) in enumerate(calls):
            emit(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": f"t{index}",
                                "name": f"mcp__eve__{name}",
                                "input": args,
                            }
                        ]
                    },
                }
            )
            result = await session.call_tool(name, args)
            emit(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"t{index}",
                                "is_error": result.is_error,
                                "content": [
                                    {"type": "text", "text": getattr(result.content[0], "text", "")}
                                ],
                            }
                        ]
                    },
                }
            )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Marked 1 zone.",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5},
            "total_cost_usd": 0.01,
        }
    )


anyio.run(main, sys.argv[1:])
