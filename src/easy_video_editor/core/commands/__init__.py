"""Every project mutation is a command (SPEC §7.3)."""

from easy_video_editor.core.commands.base import Command, CommandContext, CommandResult
from easy_video_editor.core.commands.clips import AddClip, RemoveClip
from easy_video_editor.core.commands.settings import SetOutputPresets, UpdateSettings
from easy_video_editor.core.commands.timeline import (
    AddSegment,
    AddText,
    CreateTimeline,
    DeleteTimeline,
    MoveSegment,
    RemoveSegment,
    RemoveText,
    SetEndCard,
    SetMusic,
    SetTransition,
    UpdateSegment,
    UpdateText,
)
from easy_video_editor.core.commands.zones import CreateZone, DeleteZone, SetZoneLock, UpdateZone

__all__ = [
    "AddClip",
    "AddSegment",
    "AddText",
    "Command",
    "CommandContext",
    "CommandResult",
    "CreateTimeline",
    "CreateZone",
    "DeleteTimeline",
    "DeleteZone",
    "MoveSegment",
    "RemoveClip",
    "RemoveSegment",
    "RemoveText",
    "SetEndCard",
    "SetMusic",
    "SetOutputPresets",
    "SetTransition",
    "SetZoneLock",
    "UpdateSegment",
    "UpdateSettings",
    "UpdateText",
    "UpdateZone",
]
