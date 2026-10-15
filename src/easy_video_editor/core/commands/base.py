"""Command base classes.

A command is an atomic, validated operation on a project. Commands are pydantic models so that
their arguments have a JSON Schema (reused later by the tool registry, the MCP server and the CLI).
They are executed through `ProjectSession`, which applies them to a copy of the project, checks
the invariants of `core.rules` and records them for undo/redo.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.ids import new_id
from easy_video_editor.core.model import Origin, Project


@dataclass(frozen=True)
class CommandContext:
    origin: Origin
    id_factory: Callable[[str], str] = new_id

    def new_id(self, prefix: str) -> str:
        return self.id_factory(prefix)

    def require_user(self, action: str) -> None:
        if self.origin is not Origin.USER:
            raise CommandError(f"Only the user can {action}.")


@dataclass
class CommandResult:
    created_ids: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    """User-facing notes about side effects (e.g. zones trimmed by a new exclude zone)."""

    def extend(self, other: CommandResult) -> None:
        self.created_ids.extend(other.created_ids)
        self.notices.extend(other.notices)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def describe(self) -> str:
        """Short English description shown in the history panel."""
        words = re.sub(r"(?<!^)(?=[A-Z])", " ", type(self).__name__).lower()
        return words[:1].upper() + words[1:]

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        """Mutate ``project`` (a working copy) or raise `CommandError`."""
        raise NotImplementedError
