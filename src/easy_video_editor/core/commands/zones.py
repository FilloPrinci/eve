"""Zone commands (SPEC §4).

Rules enforced here, in addition to the invariants of `core.rules`:

- only the user creates, modifies or deletes exclude (red) zones;
- AI/Auto never modify or delete locked zones; zones created or modified by the user are locked;
- only the user locks or unlocks zones;
- a highlight zone stays within its parent candidate zone plus the configured tolerance;
- changing a zone's range marks what was derived from it as stale.
"""

from __future__ import annotations

from pydantic import Field

from easy_video_editor.core.commands.base import Command, CommandContext, CommandResult
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.model import Origin, Project, Zone, ZoneKind
from easy_video_editor.core.rules import apply_exclusion, detach_dependents, mark_dependents_stale
from easy_video_editor.core.rules import span as _span


def _check_mutable(zone: Zone, ctx: CommandContext, action: str) -> None:
    if zone.kind is ZoneKind.EXCLUDE:
        ctx.require_user(f"{action} exclude (red) zones")
    if zone.locked and ctx.origin is not Origin.USER:
        raise CommandError(
            f"The {zone.kind.value} zone {_span(zone.start_us, zone.end_us)} is locked; "
            f"only the user can {action} it."
        )


def _check_tolerance(project: Project, zone: Zone) -> None:
    if zone.kind is not ZoneKind.HIGHLIGHT or zone.parent_id is None:
        return
    parent = project.zone(zone.parent_id)
    if parent.kind is not ZoneKind.CANDIDATE:
        raise CommandError("The parent of a highlight zone must be an existing candidate zone.")
    tolerance = project.settings.yellow_tolerance_us
    low, high = parent.start_us - tolerance, parent.end_us + tolerance
    if zone.start_us < low or zone.end_us > high:
        raise CommandError(
            f"The highlight zone {_span(zone.start_us, zone.end_us)} extends beyond its candidate "
            f"zone {_span(parent.start_us, parent.end_us)} by more than the tolerance "
            f"({tolerance / 1_000_000:g} s)."
        )


class CreateZone(Command):
    kind: ZoneKind
    clip_id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    label: str = ""
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: int | None = Field(default=None, ge=1, le=3)
    parent_id: str | None = None
    zone_id: str | None = None

    def describe(self) -> str:
        return f"Create {self.kind.value} zone"

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        if self.kind is ZoneKind.EXCLUDE:
            ctx.require_user("create exclude (red) zones")
        project.clip(self.clip_id)
        if self.parent_id is not None:
            project.zone(self.parent_id)
        zone = Zone(
            id=self.zone_id or ctx.new_id("zone"),
            kind=self.kind,
            clip_id=self.clip_id,
            start_us=self.start_us,
            end_us=self.end_us,
            origin=ctx.origin,
            locked=ctx.origin is Origin.USER and self.kind is not ZoneKind.EXCLUDE,
            score=self.score,
            priority=self.priority,
            label=self.label,
            parent_id=self.parent_id,
        )
        _check_tolerance(project, zone)
        project.zones.append(zone)
        result = CommandResult(created_ids=[zone.id])
        if zone.kind is ZoneKind.EXCLUDE:
            result.notices = apply_exclusion(
                project, zone.clip_id, zone.start_us, zone.end_us, ctx.new_id
            )
        return result


class UpdateZone(Command):
    """Change a zone. Fields left as None are unchanged. Updating a zone clears its stale flag."""

    zone_id: str
    start_us: int | None = Field(default=None, ge=0)
    end_us: int | None = Field(default=None, gt=0)
    label: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: int | None = Field(default=None, ge=1, le=3)

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        zone = project.zone(self.zone_id)
        _check_mutable(zone, ctx, "modify")
        old_range = (zone.start_us, zone.end_us)
        before = zone.model_copy()
        if self.start_us is not None:
            zone.start_us = self.start_us
        if self.end_us is not None:
            zone.end_us = self.end_us
        if zone.end_us <= zone.start_us:
            raise CommandError("A zone's end must be after its start.")
        if self.label is not None:
            zone.label = self.label
        if self.score is not None:
            zone.score = self.score
        if self.priority is not None:
            zone.priority = self.priority
        changed = zone != before
        zone.stale = False
        # Only a real change locks the zone (not e.g. leaving an unchanged label field).
        if changed and ctx.origin is Origin.USER and zone.kind is not ZoneKind.EXCLUDE:
            zone.locked = True

        result = CommandResult()
        if (zone.start_us, zone.end_us) != old_range:
            _check_tolerance(project, zone)
            mark_dependents_stale(project, zone)
            if zone.kind is ZoneKind.EXCLUDE:
                result.notices = apply_exclusion(
                    project, zone.clip_id, zone.start_us, zone.end_us, ctx.new_id
                )
        return result


class DeleteZone(Command):
    zone_id: str

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        zone = project.zone(self.zone_id)
        _check_mutable(zone, ctx, "delete")
        detach_dependents(project, zone)
        project.zones.remove(zone)
        return CommandResult()


class SetZoneLock(Command):
    zone_id: str
    locked: bool

    def describe(self) -> str:
        return "Lock zone" if self.locked else "Unlock zone"

    def apply(self, project: Project, ctx: CommandContext) -> CommandResult:
        ctx.require_user("lock or unlock zones")
        project.zone(self.zone_id).locked = self.locked
        return CommandResult()
