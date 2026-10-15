"""The agent loop and the API providers, with scripted models (no real API calls)."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from easy_video_editor.ai.agent import (
    AgentEvent,
    AnthropicProvider,
    FakeProvider,
    describe_call,
    run_agent,
)
from easy_video_editor.ai.runner import AiSettings, phase_prompt, run_ai_phase
from easy_video_editor.ai.tools import Image, ToolError, ToolPhase, ToolResult
from easy_video_editor.ai.workspace import Workspace
from easy_video_editor.core.model import Origin
from easy_video_editor.core.persistence import load_project
from easy_video_editor.core.progress import CancellationToken, CancelledError


def test_agent_runs_tools_until_the_model_stops(project_dir: Path) -> None:
    provider = FakeProvider(
        [
            [("text", "Looking at the project."), ("tool", "get_project_state", {})],
            [
                (
                    "tool",
                    "create_candidate_zone",
                    {"clip_id": "clip", "start_s": 1, "end_s": 4, "label": "Launch"},
                )
            ],
            [("text", "Marked the launch.")],
        ]
    )
    events: list[AgentEvent] = []
    workspace = Workspace(project_dir, ToolPhase.ANALYZE)
    result = run_agent(
        provider, ToolPhase.ANALYZE, "system", "Find highlights", workspace.call, events.append
    )
    assert result.error is None and result.summary == "Marked the launch."
    assert result.steps == 3 and result.usage.input_tokens == 300
    assert [e.kind for e in events] == ["text", "tool", "tool", "text"]
    saved = load_project(project_dir)  # every change is saved for the GUI to follow
    assert [z.label for z in saved.zones if z.origin is Origin.AI] == ["Launch"]
    first_result = provider.requests[1][-1]["content"][0]
    assert first_result["type"] == "tool_result" and '"clip"' in first_result["content"][0]["text"]


def test_rejected_tool_calls_are_reported_to_the_model(project_dir: Path) -> None:
    provider = FakeProvider(
        [
            [
                (
                    "tool",
                    "create_candidate_zone",
                    {"clip_id": "clip", "start_s": 19, "end_s": 23, "label": "Red"},
                )
            ],
            [("text", "Sorry.")],
        ]
    )
    events: list[AgentEvent] = []
    workspace = Workspace(project_dir, ToolPhase.ANALYZE)
    result = run_agent(provider, ToolPhase.ANALYZE, "s", "p", workspace.call, events.append)
    assert result.error is None
    tool_result = provider.requests[1][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert events[-2].kind == "error"
    assert not [z for z in load_project(project_dir).zones if z.origin is Origin.AI]


def test_agent_stops_after_max_steps() -> None:
    provider = FakeProvider([[("tool", "get_beats", {})]] * 10)
    result = run_agent(
        provider, ToolPhase.ANALYZE, "s", "p", lambda name, args: ToolResult("ok"), max_steps=3
    )
    assert result.error == "Stopped after 3 steps." and result.steps == 3


def test_agent_can_be_cancelled() -> None:
    token = CancellationToken()

    def run_tool(name: str, args: dict[str, Any]) -> ToolResult:
        token.cancel()
        return ToolResult("ok")

    provider = FakeProvider([[("tool", "get_beats", {})]] * 5)
    with pytest.raises(CancelledError):
        run_agent(provider, ToolPhase.ANALYZE, "s", "p", run_tool, token=token)
    assert len(provider.requests) == 1


def test_images_only_go_to_models_that_can_see() -> None:
    def run_tool(name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult("Frames", [Image(b"jpeg")])

    for vision, expected in ((True, "image"), (False, "text")):
        provider = FakeProvider([[("tool", "get_frames", {"clip_id": "c", "times_s": [1]})]])
        provider.supports_vision = vision
        run_agent(provider, ToolPhase.ANALYZE, "s", "p", run_tool)
        content = provider.requests[1][-1]["content"][0]["content"]
        assert content[1]["type"] == expected


def test_tool_errors_do_not_escape_the_loop() -> None:
    def run_tool(name: str, args: dict[str, Any]) -> ToolResult:
        raise ToolError("nope")

    provider = FakeProvider([[("tool", "get_beats", {})], [("text", "ok")]])
    assert run_agent(provider, ToolPhase.ANALYZE, "s", "p", run_tool).summary == "ok"


def test_describe_call_is_short() -> None:
    assert describe_call("get_frames", {"clip_id": "c", "times_s": [1, 2]}) == (
        "get_frames(clip_id=c, 2 frame(s))"
    )
    assert describe_call("x", {"label": "a" * 300}) == "x(…)"


def test_run_ai_phase_with_a_given_provider(project_dir: Path) -> None:
    provider = FakeProvider([[("text", "Nothing to do.")]])
    result = run_ai_phase(
        ToolPhase.FOLLOWUP,
        project_dir,
        AiSettings(),
        request="Make the intro shorter",
        provider=provider,
    )
    assert result.summary == "Nothing to do."
    assert "Make the intro shorter" in provider.requests[0][0]["content"]
    assert "{request}" not in phase_prompt(ToolPhase.FOLLOWUP)


# --- Anthropic API provider ----------------------------------------------------------------------


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def test_anthropic_provider_builds_requests_and_reads_responses() -> None:
    from anthropic.types.beta import BetaMessage

    response = BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [
                {"type": "text", "text": "Checking."},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "get_zones",
                    "input": {"kind": "candidate"},
                },
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 1_000_000, "output_tokens": 100_000},
        }
    )
    messages = FakeMessages(response)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    provider = AnthropicProvider("unused", client=client)
    tools = [{"name": "get_zones", "description": "d", "input_schema": {"type": "object"}}]
    reply = provider.complete("system", [{"role": "user", "content": "hi"}], tools)

    request = messages.calls[0]
    assert request["model"] == "claude-sonnet-5-5" and request["tools"] == tools
    assert request["output_config"] == {"effort": "medium"} and request["system"] == "system"
    assert reply.texts == ["Checking."] and reply.stop_reason == "tool_use"
    assert reply.tool_calls[0].name == "get_zones"
    assert reply.tool_calls[0].input == {"kind": "candidate"}
    assert reply.assistant_content[1]["type"] == "tool_use"
    assert reply.usage.cost_usd == pytest.approx(2.0 + 1.0)
