"""Clip commands."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from easy_video_editor.core.commands.base import Command, CommandContext, CommandResult
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.model import Clip, FrameRate, Project


class AddClip(Command):
    path: str
    duration_us: int = Field(gt=0)
    name: str | None = None
    frame_rate: FrameRate | None = None
    width: int | None = None
    height: int | None = None
    has_audio: bool = True
    fingerprint: str | None = None
    clip_id: str | None = None

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        ctx.require_user("import clips")
        path = Path(self.path)
        if not path.is_absolute():
            raise CommandError(f"Clip path must be absolute: {self.path}")
        clip = Clip(
            id=self.clip_id or ctx.new_id("clip"),
            path=str(path),
            name=self.name or path.name,
            duration_us=self.duration_us,
            frame_rate=self.frame_rate,
            width=self.width,
            height=self.height,
            has_audio=self.has_audio,
            fingerprint=self.fingerprint,
        )
        project.clips.append(clip)
        return CommandResult(created_ids=[clip.id])


class RemoveClip(Command):
    """Remove a clip together with its zones and the segments that use it."""

    clip_id: str

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        ctx.require_user("remove clips")
        clip = project.clip(self.clip_id)
        zones = project.zones_of(clip.id)
        removed_zone_ids = {zone.id for zone in zones}
        project.zones = [zone for zone in project.zones if zone.id not in removed_zone_ids]
        removed_segments = 0
        for timeline in project.timelines:
            kept = [segment for segment in timeline.segments if segment.clip_id != clip.id]
            removed_segments += len(timeline.segments) - len(kept)
            timeline.segments = kept
        project.clips.remove(clip)
        notices = []
        if zones or removed_segments:
            notices.append(
                f"Removed clip {clip.name} with {len(zones)} zone(s) "
                f"and {removed_segments} timeline segment(s)."
            )
        return CommandResult(notices=notices)


class RelinkClip(Command):
    """Point a clip to a new location of its source file."""

    clip_id: str
    path: str
    fingerprint: str | None = None

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        ctx.require_user("relink clips")
        path = Path(self.path)
        if not path.is_absolute():
            raise CommandError(f"Clip path must be absolute: {self.path}")
        clip = project.clip(self.clip_id)
        clip.path = str(path)
        if self.fingerprint is not None:
            clip.fingerprint = self.fingerprint
        return CommandResult()
