"""The agent loop for API providers (SPEC §7.5).

Provider-neutral: messages use the Messages API shape (text, tool_use and tool_result blocks),
which `AnthropicProvider` sends as is and other adapters translate. Tests use `FakeProvider`, which
replays scripted turns: no real API calls in tests.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from easy_video_editor.ai.tools import ToolError, ToolPhase, ToolResult, tools_for
from easy_video_editor.core.errors import EveError
from easy_video_editor.core.progress import CancellationToken

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 60


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    """Known only for some providers (and only an estimate for subscriptions)."""

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ProviderResponse:
    texts: list[str]
    tool_calls: list[ToolCall]
    assistant_content: list[dict[str, Any]]
    """The assistant turn as content blocks, echoed back unchanged on the next request."""
    usage: Usage
    stop_reason: str = "end_turn"


class Provider(Protocol):
    name: str
    supports_vision: bool

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse: ...


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    """``text`` (the agent's notes), ``tool`` (a tool call), ``error`` (a refused tool call)."""
    message: str


@dataclass
class AgentResult:
    summary: str = ""
    usage: Usage = field(default_factory=Usage)
    steps: int = 0
    error: str | None = None


class AgentError(EveError):
    """The agent could not run (configuration, authentication, provider failure)."""


EventSink = Callable[[AgentEvent], None]
ToolRunner = Callable[[str, dict[str, Any]], ToolResult]


def describe_call(name: str, arguments: dict[str, Any]) -> str:
    """Short readable form of a tool call for the activity panel."""
    shown = ", ".join(f"{key}={value}" for key, value in arguments.items() if key != "times_s")
    if "times_s" in arguments:
        shown = ", ".join(filter(None, [shown, f"{len(arguments['times_s'])} frame(s)"]))
    return f"{name}({shown})" if len(shown) < 160 else f"{name}(…)"


def run_agent(
    provider: Provider,
    phase: ToolPhase,
    system: str,
    prompt: str,
    run_tool: ToolRunner,
    on_event: EventSink = lambda event: None,
    token: CancellationToken | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> AgentResult:
    """Loop: ask the model, run the tools it calls, feed back the results, until it stops."""
    tools = [
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema()}
        for spec in tools_for(phase)
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    result = AgentResult()
    for step in range(1, max_steps + 1):
        if token is not None:
            token.raise_if_cancelled()
        response = provider.complete(system, messages, tools)
        result.usage.add(response.usage)
        result.steps = step
        for text in response.texts:
            on_event(AgentEvent("text", text))
        messages.append({"role": "assistant", "content": response.assistant_content})
        if response.stop_reason == "refusal":
            result.error = "The model declined this request."
            return result
        if not response.tool_calls:
            result.summary = "\n".join(response.texts).strip()
            return result
        results = []
        for call in response.tool_calls:
            if token is not None:
                token.raise_if_cancelled()
            on_event(AgentEvent("tool", describe_call(call.name, call.input)))
            try:
                output = run_tool(call.name, call.input)
            except ToolError as error:
                on_event(AgentEvent("error", str(error)))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "is_error": True,
                        "content": [{"type": "text", "text": str(error)}],
                    }
                )
                continue
            content: list[dict[str, Any]] = [{"type": "text", "text": output.text}]
            if provider.supports_vision:
                for image in output.images:
                    content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image.mime_type,
                                "data": base64.b64encode(image.data).decode("ascii"),
                            },
                        }
                    )
            elif output.images:
                content.append({"type": "text", "text": "(This model cannot see images.)"})
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
        messages.append({"role": "user", "content": results})
    result.error = f"Stopped after {max_steps} steps."
    return result


# --- Providers -----------------------------------------------------------------------------------


@dataclass
class FakeProvider:
    """Replays scripted turns. Each turn is a list of ``("text", str)`` and
    ``("tool", name, input)`` items. Records every request for assertions."""

    turns: list[list[tuple[Any, ...]]]
    name: str = "fake"
    supports_vision: bool = True
    requests: list[list[dict[str, Any]]] = field(default_factory=list)

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse:
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        turn = self.turns.pop(0) if self.turns else [("text", "Done.")]
        texts, calls, content = [], [], []
        for index, item in enumerate(turn):
            if item[0] == "text":
                texts.append(item[1])
                content.append({"type": "text", "text": item[1]})
            else:
                call = ToolCall(f"call_{len(self.requests)}_{index}", item[1], item[2])
                calls.append(call)
                content.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                )
        return ProviderResponse(
            texts, calls, content, Usage(100, 20), "tool_use" if calls else "end_turn"
        )


ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5-5"
ANTHROPIC_PRICES = {  # USD per million tokens (input, output)
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


class AnthropicProvider:
    """Claude through the Anthropic API with an API key (optional; most users use Claude Code)."""

    name = "anthropic"
    supports_vision = True

    def __init__(
        self,
        api_key: str,
        model: str = ANTHROPIC_DEFAULT_MODEL,
        effort: str = "medium",
        client: Any = None,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
        self.client = client
        self.model = model
        self.effort = effort

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderResponse:
        import anthropic

        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as error:
            raise AgentError(
                "The Anthropic API key was rejected. Check it in Preferences."
            ) from error
        except anthropic.APIConnectionError as error:
            raise AgentError(
                "Cannot reach the Anthropic API. Check the internet connection."
            ) from error
        except anthropic.APIStatusError as error:
            raise AgentError(f"The Anthropic API returned an error: {error.message}") from error
        texts, calls, content = [], [], []
        for block in response.content:
            data = block.model_dump(mode="json", exclude_none=True)
            content.append(data)
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input)))
        usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        prices = ANTHROPIC_PRICES.get(self.model)
        if prices:
            usage.cost_usd = (
                usage.input_tokens * prices[0] + usage.output_tokens * prices[1]
            ) / 1e6
        return ProviderResponse(texts, calls, content, usage, response.stop_reason or "end_turn")
