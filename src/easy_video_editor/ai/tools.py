"""The tool registry (SPEC §7.4): every tool the AI can use, defined once.

The same definitions serve the MCP server (used by Claude Code and other MCP clients) and the
API-based agent loop. Each phase sees only the tools it needs. There is no tool that creates,
changes or deletes exclude (red) zones, and every mutation is a core command executed with origin
``ai``, so the rules of SPEC §4.4 are enforced by the core whatever the model asks.

Times are seconds at this boundary (what a model reads naturally) and integer microseconds inside.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from easy_video_editor.analysis.auto import plan_phase1
from easy_video_editor.analysis.beats import BeatGrid
from easy_video_editor.analysis.features import ClipFeatures, analyze_clip
from easy_video_editor.analysis.scoring import score
from easy_video_editor.core.commands import (
    AddSegment,
    AddText,
    Command,
    CreateTimeline,
    CreateZone,
    DeleteZone,
    MoveSegment,
    RemoveSegment,
    RemoveText,
    SetEndCard,
    SetTransition,
    UpdateSegment,
    UpdateText,
    UpdateZone,
)
from easy_video_editor.core.commands.base import CommandResult
from easy_video_editor.core.errors import CommandError, EveError
from easy_video_editor.core.model import (
    Origin,
    Project,
    Transition,
    TransitionKind,
    Zone,
    ZoneKind,
)
from easy_video_editor.core.ranges import overlaps
from easy_video_editor.core.rules import span
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.core.timecode import snap_to_frame
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache
from easy_video_editor.media.frames import extract_jpeg

log = logging.getLogger(__name__)

US = 1_000_000
MAX_FRAMES_PER_CALL = 6
MAX_FEATURE_ROWS = 400
DEFAULT_FRAME_BUDGET = 40


class ToolError(EveError):
    """A tool call was refused or failed; the message goes back to the model."""


class ToolPhase(StrEnum):
    ANALYZE = "analyze"
    REFINE = "refine"
    ASSEMBLE = "assemble"
    FOLLOWUP = "followup"
    """Targeted requests after phase 3, and interactive use from an MCP client."""


ALL_PHASES = frozenset(ToolPhase)


@dataclass(frozen=True)
class Image:
    data: bytes
    mime_type: str = "image/jpeg"


@dataclass
class ToolResult:
    text: str
    images: list[Image] = field(default_factory=list)


@dataclass
class ToolContext:
    """What tools work on. The session belongs to the caller's thread."""

    session: ProjectSession
    project_dir: Path
    toolchain: Toolchain | None = None
    beats: BeatGrid | None = None
    frame_budget: int = DEFAULT_FRAME_BUDGET
    proxy_height: int = 540
    log: Callable[[str], None] = lambda message: None
    features: dict[str, ClipFeatures] = field(default_factory=dict)

    @property
    def project(self) -> Project:
        return self.session.project

    def execute(self, command: Command) -> CommandResult:
        try:
            return self.session.execute(command, Origin.AI)
        except CommandError as error:
            raise ToolError(str(error)) from error

    def clip_features(self, clip_id: str) -> ClipFeatures:
        if clip_id not in self.features:
            if self.toolchain is None:
                raise ToolError("Feature analysis is not available here.")
            clip = self.project.clip(clip_id)
            self.features[clip_id] = analyze_clip(
                clip, MediaCache(self.project_dir), self.toolchain, proxy_height=self.proxy_height
            )
        return self.features[clip_id]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


Handler = Callable[[ToolContext, Any], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[Args]
    handler: Handler
    phases: frozenset[ToolPhase]
    mutating: bool = False

    def input_schema(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return schema


REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    args: type[Args],
    phases: frozenset[ToolPhase] = ALL_PHASES,
    mutating: bool = False,
) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        REGISTRY[name] = ToolSpec(name, description, args, handler, phases, mutating)
        return handler

    return register


def tools_for(phase: ToolPhase) -> list[ToolSpec]:
    return [spec for spec in REGISTRY.values() if phase in spec.phases]


def call_tool(
    ctx: ToolContext, phase: ToolPhase, name: str, arguments: dict[str, Any]
) -> ToolResult:
    """Validate and run one tool call. Every failure becomes a `ToolError` for the model."""
    spec = REGISTRY.get(name)
    if spec is None or phase not in spec.phases:
        raise ToolError(f"Tool {name} is not available in the {phase.value} phase.")
    try:
        parsed = spec.args.model_validate(arguments or {})
    except ValidationError as error:
        raise ToolError(f"Invalid arguments for {name}: {error}") from error
    try:
        return spec.handler(ctx, parsed)
    except ToolError:
        raise
    except EveError as error:
        raise ToolError(str(error)) from error


# --- Helpers -----------------------------------------------------------------------------------


def _us(seconds: float) -> int:
    return round(seconds * US)


def _s(us: int) -> float:
    return round(us / US, 3)


def _snap(ctx: ToolContext, clip_id: str, seconds: float) -> int:
    clip = ctx.project.clip(clip_id)
    rate = clip.frame_rate.as_fraction() if clip.frame_rate else None
    us = _us(seconds)
    return min(max(snap_to_frame(us, rate) if rate else us, 0), clip.duration_us)


def _zone_dict(zone: Zone) -> dict[str, Any]:
    return {
        "id": zone.id,
        "kind": zone.kind.value,
        "clip_id": zone.clip_id,
        "start_s": _s(zone.start_us),
        "end_s": _s(zone.end_us),
        "label": zone.label,
        "score": zone.score,
        "priority": zone.priority,
        "locked": zone.locked,
        "stale": zone.stale,
        "parent_id": zone.parent_id,
        "origin": zone.origin.value,
    }


def _json(data: Any) -> ToolResult:
    return ToolResult(json.dumps(data, indent=1))


def _zone_of_kind(ctx: ToolContext, zone_id: str, kind: ZoneKind) -> Zone:
    zone = ctx.project.zone(zone_id)
    if zone.kind is not kind:
        raise ToolError(f"Zone {zone_id} is a {zone.kind.value} zone, not a {kind.value} zone.")
    return zone


def _done(ctx: ToolContext, message: str, result: CommandResult) -> ToolResult:
    ctx.log(message)
    notes = "".join(f"\nNote: {notice}" for notice in result.notices)
    created = f" (id: {', '.join(result.created_ids)})" if result.created_ids else ""
    return ToolResult(f"{message}{created}{notes}")


# --- Read tools ----------------------------------------------------------------------------------


class NoArgs(Args):
    pass


@tool(
    "get_project_state",
    "Overview of the project: settings, clips, zone counts and timelines. Call this first.",
    NoArgs,
)
def get_project_state(ctx: ToolContext, args: NoArgs) -> ToolResult:
    project = ctx.project
    settings = project.settings
    enabled = [p for p in settings.output_presets if p.id in settings.enabled_preset_ids]
    return _json(
        {
            "name": project.name,
            "settings": {
                "target_duration_s": _s(settings.target_duration_us),
                "mood": settings.mood,
                "shot_length_s": [_s(settings.shot_length_min_us), _s(settings.shot_length_max_us)],
                "highlight_tolerance_s": _s(settings.yellow_tolerance_us),
                "output_formats": [
                    {
                        "name": p.name,
                        "aspect": p.aspect,
                        "max_duration_s": _s(p.max_duration_us) if p.max_duration_us else None,
                    }
                    for p in enabled
                ],
                "instructions_from_user": settings.prompt,
            },
            "clips": [
                {
                    "id": c.id,
                    "name": c.name,
                    "duration_s": _s(c.duration_us),
                    "size": [c.width, c.height],
                    "has_audio": c.has_audio,
                    "zones": {kind.value: len(project.zones_of(c.id, kind)) for kind in ZoneKind},
                }
                for c in project.clips
            ],
            "timelines": [
                {
                    "id": t.id,
                    "aspect": t.aspect,
                    "segments": len(t.segments),
                    "duration_s": _s(t.duration_us),
                }
                for t in project.timelines
            ],
            "music_beat_s": _s(ctx.beats.period_us) if ctx.beats else None,
        }
    )


class ZonesArgs(Args):
    clip_id: str | None = Field(None, description="Only zones of this clip.")
    kind: Literal["exclude", "candidate", "highlight"] | None = Field(
        None, description="Zone kind."
    )


@tool(
    "get_zones",
    "List zones. Exclude (red) zones must never be used; locked zones are the "
    "user's and cannot be changed.",
    ZonesArgs,
)
def get_zones(ctx: ToolContext, args: ZonesArgs) -> ToolResult:
    zones = [
        z
        for z in ctx.project.zones
        if (args.clip_id is None or z.clip_id == args.clip_id)
        and (args.kind is None or z.kind.value == args.kind)
    ]
    return _json([_zone_dict(z) for z in zones])


class FeaturesArgs(Args):
    clip_id: str
    start_s: float = Field(0.0, ge=0)
    end_s: float | None = Field(None, gt=0)
    step_s: float = Field(1.0, gt=0, description="Seconds per row (averaged).")


@tool(
    "get_features",
    "Numeric features of a clip over time: motion, audio energy, onsets "
    "(impacts) and a combined interest score, all 0..1, plus scene cut times. Rows inside "
    "exclude zones are omitted.",
    FeaturesArgs,
)
def get_features(ctx: ToolContext, args: FeaturesArgs) -> ToolResult:
    clip = ctx.project.clip(args.clip_id)
    features = ctx.clip_features(clip.id)
    hop = features.hop_us
    start = _us(args.start_s)
    end = min(_us(args.end_s) if args.end_s else clip.duration_us, clip.duration_us)
    step = max(_us(args.step_s), hop, (end - start) // MAX_FEATURE_ROWS)
    interest = score(features, ctx.project.settings.mood)
    red = [(z.start_us, z.end_us) for z in ctx.project.zones_of(clip.id, ZoneKind.EXCLUDE)]
    rows = ["time_s motion audio onset interest"]
    for position in range(start, end, step):
        if any(overlaps(position, position + step, a, b) for a, b in red):
            continue
        first, last = position // hop, max(position // hop + 1, (position + step) // hop)
        values = [
            float(np.mean(series[first:last]))
            for series in (features.motion, features.audio_energy, features.onset, interest)
        ]
        rows.append(f"{_s(position):.1f} " + " ".join(f"{v:.2f}" for v in values))
    cuts = [_s(int(c)) for c in features.cuts_us if start <= c < end]
    return ToolResult("\n".join(rows) + f"\nscene_cuts_s: {cuts}")


class ClipArgs(Args):
    clip_id: str


@tool(
    "get_candidate_windows",
    "Suggested interesting time windows of a clip from the numeric "
    "analysis (the same heuristic Auto mode uses). A starting point, not an answer.",
    ClipArgs,
)
def get_candidate_windows(ctx: ToolContext, args: ClipArgs) -> ToolResult:
    ctx.project.clip(args.clip_id)
    plan = plan_phase1(ctx.project, {args.clip_id: ctx.clip_features(args.clip_id)})
    windows = [
        {"start_s": _s(c.start_us), "end_s": _s(c.end_us), "score": c.score, "label": c.label}
        for c in plan.commands
        if isinstance(c, CreateZone)
    ]
    return _json(windows)


class FramesArgs(Args):
    clip_id: str
    times_s: list[float] = Field(min_length=1, max_length=MAX_FRAMES_PER_CALL)
    width: int = Field(512, ge=128, le=768)


@tool(
    "get_frames",
    f"Downscaled frames of a clip at given times (at most {MAX_FRAMES_PER_CALL} "
    "per call; a per-run budget applies). Frames inside exclude zones are never returned.",
    FramesArgs,
)
def get_frames(ctx: ToolContext, args: FramesArgs) -> ToolResult:
    if ctx.toolchain is None:
        raise ToolError("Frames are not available here.")
    clip = ctx.project.clip(args.clip_id)
    red = [(z.start_us, z.end_us) for z in ctx.project.zones_of(clip.id, ZoneKind.EXCLUDE)]
    cache = MediaCache(ctx.project_dir)
    proxy = cache.proxy_path(clip, ctx.proxy_height)
    source = proxy if proxy.is_file() else clip.file_path
    images, notes = [], []
    for time_s in args.times_s:
        us = _us(time_s)
        if not 0 <= us < clip.duration_us:
            notes.append(f"{time_s:.2f} s is outside the clip.")
        elif any(a <= us < b for a, b in red):
            notes.append(f"{time_s:.2f} s is inside an exclude zone: not shown.")
        elif ctx.frame_budget <= 0:
            notes.append("Frame budget for this run is used up; rely on the numeric features.")
            break
        else:
            images.append(Image(extract_jpeg(source, time_s, args.width, ctx.toolchain)))
            ctx.frame_budget -= 1
    ctx.log(f"Looking at {len(images)} frame(s) of {clip.name}")
    shown = [
        t
        for t in args.times_s
        if 0 <= _us(t) < clip.duration_us and not any(a <= _us(t) < b for a, b in red)
    ][: len(images)]
    text = f"Frames at {', '.join(f'{t:.2f} s' for t in shown)} (budget left: {ctx.frame_budget})."
    return ToolResult(" ".join([text, *notes]), images)


@tool("get_beats", "Beat grid of the selected music (if any): period and first beat times.", NoArgs)
def get_beats(ctx: ToolContext, args: NoArgs) -> ToolResult:
    if ctx.beats is None:
        return ToolResult("No music with a steady beat is selected.")
    return _json(
        {
            "tempo_bpm": round(ctx.beats.tempo_bpm, 1),
            "beat_period_s": _s(ctx.beats.period_us),
            "first_beats_s": [_s(b) for b in ctx.beats.beats_us[:32]],
        }
    )


class TimelineArgs(Args):
    timeline_id: str | None = Field(None, description="Default: every timeline.")


@tool(
    "get_timeline",
    "The edit timeline(s): segments with their source ranges, speed and "
    "transitions, texts and end card.",
    TimelineArgs,
)
def get_timeline(ctx: ToolContext, args: TimelineArgs) -> ToolResult:
    project = ctx.project
    timelines = [project.timeline(args.timeline_id)] if args.timeline_id else project.timelines
    result = []
    for timeline in timelines:
        segments = []
        for segment in timeline.segments:
            label = project.zone(segment.zone_id).label if segment.zone_id else ""
            segments.append(
                {
                    "id": segment.id,
                    "clip_id": segment.clip_id,
                    "zone_id": segment.zone_id,
                    "label": label,
                    "start_s": _s(segment.in_us),
                    "end_s": _s(segment.out_us),
                    "speed": segment.speed,
                    "output_s": _s(segment.output_duration_us),
                    "transition_out": [
                        segment.transition_out.kind.value,
                        _s(segment.transition_out.duration_us),
                    ],
                    "stale": segment.stale,
                }
            )
        result.append(
            {
                "id": timeline.id,
                "aspect": timeline.aspect,
                "duration_s": _s(timeline.duration_us),
                "segments": segments,
                "texts": [
                    {
                        "id": t.id,
                        "text": t.text,
                        "start_s": _s(t.start_us),
                        "end_s": _s(t.end_us),
                        "style": t.style,
                        "by": t.origin.value,
                    }
                    for t in timeline.texts
                ],
                "end_card": {
                    "enabled": timeline.end_card.enabled,
                    "cta_text": timeline.end_card.cta_text,
                    "duration_s": _s(timeline.end_card.duration_us),
                },
            }
        )
    return _json(result)


# --- Candidate (blue) zones ----------------------------------------------------------------------

CANDIDATE_PHASES = frozenset({ToolPhase.ANALYZE, ToolPhase.FOLLOWUP})
HIGHLIGHT_PHASES = frozenset({ToolPhase.REFINE, ToolPhase.FOLLOWUP})
EDIT_PHASES = frozenset({ToolPhase.ASSEMBLE, ToolPhase.FOLLOWUP})


class CreateCandidateArgs(Args):
    clip_id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    label: str = Field(description="Short English description, e.g. 'Overtake in the hairpin'.")
    score: float | None = Field(None, ge=0, le=1, description="How interesting, 0..1.")


@tool(
    "create_candidate_zone",
    "Mark a rough interesting region of a clip (blue zone). Must not overlap exclude zones.",
    CreateCandidateArgs,
    CANDIDATE_PHASES,
    mutating=True,
)
def create_candidate_zone(ctx: ToolContext, args: CreateCandidateArgs) -> ToolResult:
    start, end = _snap(ctx, args.clip_id, args.start_s), _snap(ctx, args.clip_id, args.end_s)
    result = ctx.execute(
        CreateZone(
            kind=ZoneKind.CANDIDATE,
            clip_id=args.clip_id,
            start_us=start,
            end_us=end,
            label=args.label,
            score=args.score,
        )
    )
    return _done(ctx, f"Selected {span(start, end)}: {args.label}", result)


class UpdateZoneArgs(Args):
    zone_id: str
    start_s: float | None = Field(None, ge=0)
    end_s: float | None = Field(None, gt=0)
    label: str | None = None
    score: float | None = Field(None, ge=0, le=1)


def _update_zone(
    ctx: ToolContext, args: UpdateZoneArgs, kind: ZoneKind, priority: int | None = None
) -> ToolResult:
    zone = _zone_of_kind(ctx, args.zone_id, kind)
    start = _snap(ctx, zone.clip_id, args.start_s) if args.start_s is not None else None
    end = _snap(ctx, zone.clip_id, args.end_s) if args.end_s is not None else None
    result = ctx.execute(
        UpdateZone(
            zone_id=zone.id,
            start_us=start,
            end_us=end,
            label=args.label,
            score=args.score,
            priority=priority,
        )
    )
    updated = ctx.project.zone(zone.id)
    return _done(ctx, f"Updated {kind.value} zone {span(updated.start_us, updated.end_us)}", result)


@tool(
    "update_candidate_zone",
    "Change an unlocked candidate (blue) zone.",
    UpdateZoneArgs,
    CANDIDATE_PHASES,
    mutating=True,
)
def update_candidate_zone(ctx: ToolContext, args: UpdateZoneArgs) -> ToolResult:
    return _update_zone(ctx, args, ZoneKind.CANDIDATE)


class ZoneIdArgs(Args):
    zone_id: str


@tool(
    "delete_candidate_zone",
    "Delete an unlocked candidate (blue) zone.",
    ZoneIdArgs,
    CANDIDATE_PHASES,
    mutating=True,
)
def delete_candidate_zone(ctx: ToolContext, args: ZoneIdArgs) -> ToolResult:
    zone = _zone_of_kind(ctx, args.zone_id, ZoneKind.CANDIDATE)
    result = ctx.execute(DeleteZone(zone_id=zone.id))
    return _done(ctx, f"Removed candidate zone {span(zone.start_us, zone.end_us)}", result)


# --- Highlight (yellow) zones --------------------------------------------------------------------


class CreateHighlightArgs(Args):
    parent_id: str = Field(description="The candidate zone this highlight refines.")
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    label: str
    score: float | None = Field(None, ge=0, le=1)
    priority: int | None = Field(None, ge=1, le=3, description="1-3 stars; 3 = must use.")


@tool(
    "create_highlight_zone",
    "Mark a precisely trimmed best moment (yellow zone) inside a "
    "candidate zone (it may extend beyond it by the highlight tolerance).",
    CreateHighlightArgs,
    HIGHLIGHT_PHASES,
    mutating=True,
)
def create_highlight_zone(ctx: ToolContext, args: CreateHighlightArgs) -> ToolResult:
    parent = _zone_of_kind(ctx, args.parent_id, ZoneKind.CANDIDATE)
    start, end = _snap(ctx, parent.clip_id, args.start_s), _snap(ctx, parent.clip_id, args.end_s)
    result = ctx.execute(
        CreateZone(
            kind=ZoneKind.HIGHLIGHT,
            clip_id=parent.clip_id,
            start_us=start,
            end_us=end,
            label=args.label,
            score=args.score,
            priority=args.priority,
            parent_id=parent.id,
        )
    )
    return _done(ctx, f"Highlight {span(start, end)}: {args.label}", result)


class UpdateHighlightArgs(UpdateZoneArgs):
    priority: int | None = Field(None, ge=1, le=3)


@tool(
    "update_highlight_zone",
    "Change an unlocked highlight (yellow) zone.",
    UpdateHighlightArgs,
    HIGHLIGHT_PHASES,
    mutating=True,
)
def update_highlight_zone(ctx: ToolContext, args: UpdateHighlightArgs) -> ToolResult:
    return _update_zone(ctx, args, ZoneKind.HIGHLIGHT, args.priority)


@tool(
    "delete_highlight_zone",
    "Delete an unlocked highlight (yellow) zone.",
    ZoneIdArgs,
    HIGHLIGHT_PHASES,
    mutating=True,
)
def delete_highlight_zone(ctx: ToolContext, args: ZoneIdArgs) -> ToolResult:
    zone = _zone_of_kind(ctx, args.zone_id, ZoneKind.HIGHLIGHT)
    result = ctx.execute(DeleteZone(zone_id=zone.id))
    return _done(ctx, f"Removed highlight zone {span(zone.start_us, zone.end_us)}", result)


# --- Edit timeline -------------------------------------------------------------------------------


class CreateTimelineArgs(Args):
    aspect: str = Field(pattern=r"^[1-9][0-9]*:[1-9][0-9]*$", description="e.g. '16:9' or '9:16'.")


@tool(
    "create_timeline",
    "Create the edit timeline for an aspect ratio (one per aspect).",
    CreateTimelineArgs,
    EDIT_PHASES,
    mutating=True,
)
def create_timeline(ctx: ToolContext, args: CreateTimelineArgs) -> ToolResult:
    result = ctx.execute(CreateTimeline(aspect=args.aspect))
    return _done(ctx, f"Created the {args.aspect} timeline", result)


class TimelineIdArgs(Args):
    timeline_id: str


@tool(
    "clear_timeline",
    "Remove every segment, and the texts not written by the user, from a timeline (to rebuild it).",
    TimelineIdArgs,
    EDIT_PHASES,
    mutating=True,
)
def clear_timeline(ctx: ToolContext, args: TimelineIdArgs) -> ToolResult:
    timeline = ctx.project.timeline(args.timeline_id)
    for segment in list(timeline.segments):
        ctx.execute(RemoveSegment(timeline_id=timeline.id, segment_id=segment.id))
    for text in list(timeline.texts):
        if text.origin is not Origin.USER:
            ctx.execute(RemoveText(timeline_id=timeline.id, text_id=text.id))
    ctx.log(f"Cleared the {timeline.aspect} timeline")
    return ToolResult(f"Cleared timeline {timeline.id}.")


class AddSegmentArgs(Args):
    timeline_id: str
    zone_id: str = Field(description="The highlight zone to use.")
    start_s: float | None = Field(None, ge=0, description="Default: the zone start.")
    end_s: float | None = Field(None, gt=0, description="Default: the zone end.")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="<1 slow motion, >1 faster.")
    index: int | None = Field(None, ge=0, description="Position; default: at the end.")


@tool(
    "add_segment",
    "Add a shot to an edit timeline from a highlight zone. Output length is (end - start) / speed.",
    AddSegmentArgs,
    EDIT_PHASES,
    mutating=True,
)
def add_segment(ctx: ToolContext, args: AddSegmentArgs) -> ToolResult:
    zone = _zone_of_kind(ctx, args.zone_id, ZoneKind.HIGHLIGHT)
    start = _snap(ctx, zone.clip_id, args.start_s) if args.start_s is not None else zone.start_us
    end = _snap(ctx, zone.clip_id, args.end_s) if args.end_s is not None else zone.end_us
    result = ctx.execute(
        AddSegment(
            timeline_id=args.timeline_id,
            clip_id=zone.clip_id,
            in_us=start,
            out_us=end,
            speed=args.speed,
            zone_id=zone.id,
            index=args.index,
        )
    )
    return _done(ctx, f"Added shot {span(start, end)} ({zone.label or 'highlight'})", result)


class UpdateSegmentArgs(Args):
    timeline_id: str
    segment_id: str
    start_s: float | None = Field(None, ge=0)
    end_s: float | None = Field(None, gt=0)
    speed: float | None = Field(None, ge=0.25, le=4.0)


@tool(
    "update_segment",
    "Trim a shot or change its speed.",
    UpdateSegmentArgs,
    EDIT_PHASES,
    mutating=True,
)
def update_segment(ctx: ToolContext, args: UpdateSegmentArgs) -> ToolResult:
    segment = ctx.project.timeline(args.timeline_id).segment(args.segment_id)
    start = _snap(ctx, segment.clip_id, args.start_s) if args.start_s is not None else None
    end = _snap(ctx, segment.clip_id, args.end_s) if args.end_s is not None else None
    result = ctx.execute(
        UpdateSegment(
            timeline_id=args.timeline_id,
            segment_id=segment.id,
            in_us=start,
            out_us=end,
            speed=args.speed,
        )
    )
    return _done(ctx, "Adjusted a shot", result)


class MoveSegmentArgs(Args):
    timeline_id: str
    segment_id: str
    index: int = Field(ge=0)


@tool(
    "move_segment", "Move a shot to another position.", MoveSegmentArgs, EDIT_PHASES, mutating=True
)
def move_segment(ctx: ToolContext, args: MoveSegmentArgs) -> ToolResult:
    result = ctx.execute(
        MoveSegment(timeline_id=args.timeline_id, segment_id=args.segment_id, index=args.index)
    )
    return _done(ctx, f"Moved a shot to position {args.index + 1}", result)


class SegmentIdArgs(Args):
    timeline_id: str
    segment_id: str


@tool("remove_segment", "Remove a shot from a timeline.", SegmentIdArgs, EDIT_PHASES, mutating=True)
def remove_segment(ctx: ToolContext, args: SegmentIdArgs) -> ToolResult:
    result = ctx.execute(RemoveSegment(timeline_id=args.timeline_id, segment_id=args.segment_id))
    return _done(ctx, "Removed a shot", result)


class TransitionArgs(Args):
    timeline_id: str
    segment_id: str
    kind: Literal["cut", "crossfade", "dip_to_black", "dip_to_white", "flash"]
    duration_s: float = Field(0.0, ge=0, le=3, description="Ignored for cut.")


@tool(
    "set_transition",
    "Set the transition from a shot into the next one (or the end card). "
    "Crossfades use extra frames around the cut and are refused if those are excluded.",
    TransitionArgs,
    EDIT_PHASES,
    mutating=True,
)
def set_transition(ctx: ToolContext, args: TransitionArgs) -> ToolResult:
    kind = TransitionKind(args.kind)
    duration = 0 if kind is TransitionKind.CUT else max(_us(args.duration_s), 40_000)
    result = ctx.execute(
        SetTransition(
            timeline_id=args.timeline_id,
            segment_id=args.segment_id,
            transition=Transition(kind=kind, duration_us=duration),
        )
    )
    return _done(ctx, f"Transition: {kind.value.replace('_', ' ')}", result)


class AddTextArgs(Args):
    timeline_id: str
    text: str = Field(min_length=1, max_length=80)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    style: Literal["title", "caption", "cta"] = "title"
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


@tool(
    "add_text",
    "Add a text overlay (times on the edit timeline).",
    AddTextArgs,
    EDIT_PHASES,
    mutating=True,
)
def add_text(ctx: ToolContext, args: AddTextArgs) -> ToolResult:
    result = ctx.execute(
        AddText(
            timeline_id=args.timeline_id,
            text=args.text,
            start_us=_us(args.start_s),
            end_us=_us(args.end_s),
            style=args.style,
            x=args.x,
            y=args.y,
        )
    )
    return _done(ctx, f"Added text “{args.text}”", result)


class UpdateTextArgs(Args):
    timeline_id: str
    text_id: str
    text: str | None = Field(None, min_length=1, max_length=80)
    start_s: float | None = Field(None, ge=0)
    end_s: float | None = Field(None, gt=0)
    style: Literal["title", "caption", "cta"] | None = None


@tool("update_text", "Change a text overlay.", UpdateTextArgs, EDIT_PHASES, mutating=True)
def update_text(ctx: ToolContext, args: UpdateTextArgs) -> ToolResult:
    result = ctx.execute(
        UpdateText(
            timeline_id=args.timeline_id,
            text_id=args.text_id,
            text=args.text,
            start_us=_us(args.start_s) if args.start_s is not None else None,
            end_us=_us(args.end_s) if args.end_s is not None else None,
            style=args.style,
        )
    )
    return _done(ctx, "Changed a text", result)


class TextIdArgs(Args):
    timeline_id: str
    text_id: str


@tool("remove_text", "Remove a text overlay.", TextIdArgs, EDIT_PHASES, mutating=True)
def remove_text(ctx: ToolContext, args: TextIdArgs) -> ToolResult:
    result = ctx.execute(RemoveText(timeline_id=args.timeline_id, text_id=args.text_id))
    return _done(ctx, "Removed a text", result)


class EndCardArgs(Args):
    timeline_id: str
    enabled: bool | None = None
    cta_text: str | None = Field(None, max_length=60)
    duration_s: float | None = Field(None, ge=0.5, le=10)


@tool(
    "set_end_card",
    "Configure the end card (call to action at the end).",
    EndCardArgs,
    EDIT_PHASES,
    mutating=True,
)
def set_end_card(ctx: ToolContext, args: EndCardArgs) -> ToolResult:
    timeline = ctx.project.timeline(args.timeline_id)
    changes: dict[str, Any] = {}
    if args.enabled is not None:
        changes["enabled"] = args.enabled
    if args.cta_text is not None:
        changes["cta_text"] = args.cta_text
    if args.duration_s is not None:
        changes["duration_us"] = _us(args.duration_s)
    card = timeline.end_card.model_copy(update=changes)
    result = ctx.execute(SetEndCard(timeline_id=timeline.id, end_card=card))
    return _done(ctx, "Updated the end card", result)
