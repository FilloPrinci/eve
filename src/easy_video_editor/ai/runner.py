"""Running a phase (or a follow-up request) with the configured AI provider."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from easy_video_editor.ai.agent import (
    ANTHROPIC_DEFAULT_MODEL,
    AgentError,
    AgentResult,
    AnthropicProvider,
    EventSink,
    Provider,
    run_agent,
)
from easy_video_editor.ai.claude_code import find_claude, run_claude_code
from easy_video_editor.ai.secrets import get_api_key
from easy_video_editor.ai.tools import DEFAULT_FRAME_BUDGET, ToolPhase
from easy_video_editor.ai.workspace import Workspace
from easy_video_editor.core.model import EveModel
from easy_video_editor.core.progress import CancellationToken
from easy_video_editor.media.binaries import Toolchain

PROMPTS = Path(__file__).resolve().parent / "prompts"
REFRESH_REQUEST = (
    "Some highlight zones or timeline segments are stale because the zone they came from was "
    "changed. Regenerate only the stale items so they fit their source again."
)


class AiProviderKind(StrEnum):
    CLAUDE_CODE = "claude_code"
    """The user's Claude Code, logged in with their Claude subscription."""
    ANTHROPIC = "anthropic"
    """The Anthropic API with an API key stored in the keyring."""


class AiSettings(EveModel):
    provider: AiProviderKind = AiProviderKind.CLAUDE_CODE
    claude_path: str | None = None
    claude_model: str = ""
    """Claude Code model alias or name; empty means the default of the user's plan."""
    anthropic_model: str = ANTHROPIC_DEFAULT_MODEL
    effort: Literal["low", "medium", "high"] = "medium"
    frame_budget: int = Field(default=DEFAULT_FRAME_BUDGET, ge=0, le=200)
    max_steps: int = Field(default=60, ge=5, le=300)


def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def phase_prompt(phase: ToolPhase, request: str | None = None) -> str:
    if phase is ToolPhase.FOLLOWUP:
        return load_prompt("followup").replace("{request}", (request or REFRESH_REQUEST).strip())
    return load_prompt(phase.value)


def run_ai_phase(
    phase: ToolPhase,
    project_dir: Path,
    settings: AiSettings,
    on_event: EventSink = lambda event: None,
    token: CancellationToken | None = None,
    request: str | None = None,
    toolchain: Toolchain | None = None,
    provider: Provider | None = None,
) -> AgentResult:
    """Run one phase on a saved project folder; changes are saved to it as they happen."""
    system = load_prompt("system")
    prompt = phase_prompt(phase, request)
    if provider is None and settings.provider is AiProviderKind.CLAUDE_CODE:
        claude = find_claude(Path(settings.claude_path) if settings.claude_path else None)
        if claude is None:
            raise AgentError(
                "Claude Code was not found. Install it and log in once with your Claude account "
                "(see Help → System check), or set its path in Preferences."
            )
        return run_claude_code(
            claude,
            project_dir,
            phase,
            prompt,
            system,
            settings.claude_model or None,
            on_event,
            token,
            frame_budget=settings.frame_budget,
        )
    if provider is None:
        key = get_api_key("anthropic")
        if not key:
            raise AgentError(
                "No Anthropic API key is set. Add one in Preferences, or use Claude Code."
            )
        provider = AnthropicProvider(key, settings.anthropic_model, settings.effort)
    workspace = Workspace(project_dir, phase, toolchain, settings.frame_budget)
    return run_agent(
        provider, phase, system, prompt, workspace.call, on_event, token, settings.max_steps
    )
