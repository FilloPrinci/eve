"""Project settings commands (SPEC §9)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from easy_video_editor.core.commands.base import Command, CommandContext, CommandResult
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.model import OutputPreset, Project, ProjectSettings


def _replace_settings(project: Project, changes: dict[str, Any]) -> None:
    try:
        project.settings = ProjectSettings.model_validate(project.settings.model_dump() | changes)
    except ValidationError as error:
        raise CommandError(f"Invalid settings: {error}") from error


class UpdateSettings(Command):
    """Change project settings. ``changes`` maps `ProjectSettings` field names to new values."""

    changes: dict[str, Any]

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        ctx.require_user("change project settings")
        _replace_settings(project, self.changes)
        return CommandResult()


class SetOutputPresets(Command):
    presets: list[OutputPreset]
    enabled_preset_ids: list[str]

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        ctx.require_user("change output presets")
        _replace_settings(
            project,
            {
                "output_presets": [preset.model_dump() for preset in self.presets],
                "enabled_preset_ids": self.enabled_preset_ids,
            },
        )
        return CommandResult()
