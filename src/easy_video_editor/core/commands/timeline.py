"""Edit-timeline commands (SPEC §6.1).

Red-zone and media-range rules for segments are enforced by the invariants in `core.rules`.
Updating a segment clears its stale flag.
"""

from __future__ import annotations

from pydantic import Field

from easy_video_editor.core.commands.base import Command, CommandContext, CommandResult
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.model import (
    ASPECT_PATTERN,
    EditTimeline,
    EndCard,
    MusicCue,
    Project,
    Reframe,
    Segment,
    TextOverlay,
    Transition,
)


class CreateTimeline(Command):
    aspect: str = Field(pattern=ASPECT_PATTERN)
    name: str | None = None
    timeline_id: str | None = None

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        if any(timeline.aspect == self.aspect for timeline in project.timelines):
            raise CommandError(f"A timeline for aspect ratio {self.aspect} already exists.")
        timeline = EditTimeline(
            id=self.timeline_id or ctx.new_id("tl"),
            name=self.name or self.aspect,
            aspect=self.aspect,
        )
        project.timelines.append(timeline)
        return CommandResult(created_ids=[timeline.id])


class DeleteTimeline(Command):
    timeline_id: str

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        project.timelines.remove(project.timeline(self.timeline_id))
        return CommandResult()


class AddSegment(Command):
    timeline_id: str
    clip_id: str
    in_us: int = Field(ge=0)
    out_us: int = Field(gt=0)
    index: int | None = Field(default=None, ge=0)
    """Insert position; None appends at the end."""
    speed: float = Field(default=1.0, ge=0.1, le=10.0)
    zone_id: str | None = None
    reframe: Reframe = Field(default_factory=Reframe)
    transition_out: Transition = Field(default_factory=Transition)
    game_audio_db: float = 0.0
    segment_id: str | None = None

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        timeline = project.timeline(self.timeline_id)
        project.clip(self.clip_id)
        if self.zone_id is not None:
            project.zone(self.zone_id)
        segment = Segment(
            id=self.segment_id or ctx.new_id("seg"),
            clip_id=self.clip_id,
            in_us=self.in_us,
            out_us=self.out_us,
            speed=self.speed,
            zone_id=self.zone_id,
            reframe=self.reframe,
            transition_out=self.transition_out,
            game_audio_db=self.game_audio_db,
            origin=ctx.origin,
        )
        index = len(timeline.segments) if self.index is None else self.index
        timeline.segments.insert(min(index, len(timeline.segments)), segment)
        return CommandResult(created_ids=[segment.id])


class UpdateSegment(Command):
    """Change a segment. Fields left as None are unchanged."""

    timeline_id: str
    segment_id: str
    in_us: int | None = Field(default=None, ge=0)
    out_us: int | None = Field(default=None, gt=0)
    speed: float | None = Field(default=None, ge=0.1, le=10.0)
    reframe: Reframe | None = None
    game_audio_db: float | None = None

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        segment = project.timeline(self.timeline_id).segment(self.segment_id)
        if self.in_us is not None:
            segment.in_us = self.in_us
        if self.out_us is not None:
            segment.out_us = self.out_us
        if segment.out_us <= segment.in_us:
            raise CommandError("A segment's out point must be after its in point.")
        if self.speed is not None:
            segment.speed = self.speed
        if self.reframe is not None:
            segment.reframe = self.reframe
        if self.game_audio_db is not None:
            segment.game_audio_db = self.game_audio_db
        segment.stale = False
        return CommandResult()


class MoveSegment(Command):
    timeline_id: str
    segment_id: str
    index: int = Field(ge=0)

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        timeline = project.timeline(self.timeline_id)
        segment = timeline.segments.pop(timeline.segment_index(self.segment_id))
        timeline.segments.insert(min(self.index, len(timeline.segments)), segment)
        return CommandResult()


class RemoveSegment(Command):
    timeline_id: str
    segment_id: str

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        timeline = project.timeline(self.timeline_id)
        timeline.segments.pop(timeline.segment_index(self.segment_id))
        return CommandResult()


class SetTransition(Command):
    """Set the transition from a segment into the next one (or into the end card)."""

    timeline_id: str
    segment_id: str
    transition: Transition

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        segment = project.timeline(self.timeline_id).segment(self.segment_id)
        segment.transition_out = self.transition
        return CommandResult()


class AddText(Command):
    timeline_id: str
    text: str = Field(min_length=1)
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    style: str = "title"
    x: float = Field(default=0.5, ge=0.0, le=1.0)
    y: float = Field(default=0.5, ge=0.0, le=1.0)
    text_id: str | None = None

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        timeline = project.timeline(self.timeline_id)
        overlay = TextOverlay(
            id=self.text_id or ctx.new_id("text"),
            text=self.text,
            start_us=self.start_us,
            end_us=self.end_us,
            style=self.style,
            x=self.x,
            y=self.y,
            origin=ctx.origin,
        )
        timeline.texts.append(overlay)
        return CommandResult(created_ids=[overlay.id])


class UpdateText(Command):
    """Change a text overlay. Fields left as None are unchanged."""

    timeline_id: str
    text_id: str
    text: str | None = Field(default=None, min_length=1)
    start_us: int | None = Field(default=None, ge=0)
    end_us: int | None = Field(default=None, gt=0)
    style: str | None = None
    x: float | None = Field(default=None, ge=0.0, le=1.0)
    y: float | None = Field(default=None, ge=0.0, le=1.0)

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        overlay = project.timeline(self.timeline_id).text(self.text_id)
        changes = self.model_dump(exclude={"timeline_id", "text_id"}, exclude_none=True)
        for name, value in changes.items():
            setattr(overlay, name, value)
        if overlay.end_us <= overlay.start_us:
            raise CommandError("A text's end must be after its start.")
        return CommandResult()


class RemoveText(Command):
    timeline_id: str
    text_id: str

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        timeline = project.timeline(self.timeline_id)
        timeline.texts.remove(timeline.text(self.text_id))
        return CommandResult()


class SetMusic(Command):
    """Replace the music cues of a timeline. Cues without an id get a new one."""

    timeline_id: str
    cues: list[MusicCue]

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        timeline = project.timeline(self.timeline_id)
        cues = [
            cue if cue.id else cue.model_copy(update={"id": ctx.new_id("music")})
            for cue in self.cues
        ]
        timeline.music = cues
        return CommandResult(created_ids=[cue.id for cue in cues])


class SetEndCard(Command):
    timeline_id: str
    end_card: EndCard

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        project.timeline(self.timeline_id).end_card = self.end_card
        return CommandResult()
