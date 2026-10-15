"""Project rules (SPEC §4.4): exclusion, transition handles, staleness and invariants.

Invariants are checked after every command; a command that would break one is rejected.
They hold regardless of who issued the command:

- exclude (red) zones are created only by the user;
- candidate/highlight zones never overlap an exclude zone of the same clip;
- edit-timeline segments never use frames of an exclude zone, transition handles included;
- every reference (clip, parent zone, source zone) points to an existing item of the right kind;
- every range lies inside its clip.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator

from easy_video_editor.core.model import (
    EditTimeline,
    Origin,
    Project,
    Segment,
    Transition,
    TransitionKind,
    Zone,
    ZoneKind,
)
from easy_video_editor.core.ranges import overlaps, subtract
from easy_video_editor.core.timecode import format_us

HANDLE_TRANSITIONS = frozenset({TransitionKind.CROSSFADE})
"""Transitions that blend two shots and therefore use source frames beyond the cut points."""


def span(start_us: int, end_us: int) -> str:
    return f"{format_us(start_us)}-{format_us(end_us)}"


def handle_us(transition: Transition, speed: float) -> int:
    """Source frames a transition needs on each side of the cut.

    Handles are centred on the cut: a crossfade of duration d uses d/2 of extra output time
    after the outgoing segment and d/2 before the incoming one, converted to source time.
    """
    if transition.kind not in HANDLE_TRANSITIONS:
        return 0
    return round(transition.duration_us / 2 * speed)


def used_source_range(timeline: EditTimeline, index: int) -> tuple[int, int]:
    """Source range a segment actually uses, including transition handles."""
    segment = timeline.segments[index]
    start = segment.in_us
    end = segment.out_us + handle_us(segment.transition_out, segment.speed)
    if index > 0:
        start -= handle_us(timeline.segments[index - 1].transition_out, segment.speed)
    return start, end


def exclusion_ranges(project: Project, clip_id: str) -> list[tuple[int, int]]:
    return [(zone.start_us, zone.end_us) for zone in project.zones_of(clip_id, ZoneKind.EXCLUDE)]


def first_exclusion_hit(project: Project, clip_id: str, start: int, end: int) -> Zone | None:
    for zone in project.zones_of(clip_id, ZoneKind.EXCLUDE):
        if overlaps(start, end, zone.start_us, zone.end_us):
            return zone
    return None


# --- Staleness ---------------------------------------------------------------------------------


def mark_dependents_stale(project: Project, zone: Zone) -> None:
    """A zone's range changed: mark what was derived from it as stale (SPEC §4.4 rule 4)."""
    if zone.kind is ZoneKind.CANDIDATE:
        for child in project.children_of(zone.id):
            child.stale = True
    elif zone.kind is ZoneKind.HIGHLIGHT:
        for segment in project.segments_using(zone.id):
            segment.stale = True


def detach_dependents(project: Project, zone: Zone) -> None:
    """A zone is about to be deleted: detach and mark stale what was derived from it."""
    for child in project.children_of(zone.id):
        child.parent_id = None
        child.stale = True
    for segment in project.segments_using(zone.id):
        segment.zone_id = None
        segment.stale = True


# --- Exclusion (red zones) ---------------------------------------------------------------------


def apply_exclusion(
    project: Project, clip_id: str, start: int, end: int, new_id: Callable[[str], str]
) -> list[str]:
    """Remove ``[start, end)`` of ``clip_id`` from every zone and segment, locked or not.

    Overlapping zones and segments are trimmed, split in two, or removed. Crossfades that would
    take handle frames from an exclude zone are turned into cuts. Returns user-facing notices.
    """
    notices: list[str] = []
    excluded = span(start, end)

    for zone in list(project.zones):
        if zone.clip_id != clip_id or zone.kind is ZoneKind.EXCLUDE:
            continue
        pieces = subtract(zone.start_us, zone.end_us, start, end)
        if pieces == [(zone.start_us, zone.end_us)]:
            continue
        name = f"{zone.kind.value} zone {span(zone.start_us, zone.end_us)}"
        if not pieces:
            detach_dependents(project, zone)
            project.zones.remove(zone)
            notices.append(f"Removed {name}: it lies inside the excluded range {excluded}.")
            continue
        zone.start_us, zone.end_us = pieces[0]
        mark_dependents_stale(project, zone)
        if len(pieces) == 1:
            notices.append(
                f"Trimmed {name} to {span(*pieces[0])} around excluded range {excluded}."
            )
            continue
        second = zone.model_copy(
            update={"id": new_id("zone"), "start_us": pieces[1][0], "end_us": pieces[1][1]}
        )
        project.zones.insert(project.zones.index(zone) + 1, second)
        if zone.kind is ZoneKind.CANDIDATE:
            for child in project.children_of(zone.id):
                if (child.start_us + child.end_us) // 2 >= second.start_us:
                    child.parent_id = second.id
        notices.append(
            f"Split {name} into {span(*pieces[0])} and {span(*pieces[1])} "
            f"around excluded range {excluded}."
        )

    for timeline in project.timelines:
        notices.extend(_exclude_from_segments(timeline, clip_id, start, end, new_id))
        notices.extend(_drop_handles_into_exclusions(project, timeline))
    return notices


def _exclude_from_segments(
    timeline: EditTimeline, clip_id: str, start: int, end: int, new_id: Callable[[str], str]
) -> list[str]:
    notices: list[str] = []
    rebuilt: list[Segment] = []
    for segment in timeline.segments:
        pieces = (
            subtract(segment.in_us, segment.out_us, start, end)
            if segment.clip_id == clip_id
            else [(segment.in_us, segment.out_us)]
        )
        if pieces == [(segment.in_us, segment.out_us)]:
            rebuilt.append(segment)
            continue
        name = f"segment {span(segment.in_us, segment.out_us)} in timeline {timeline.name}"
        if not pieces:
            notices.append(f"Removed {name}: it uses only excluded footage.")
            continue
        for number, (piece_in, piece_out) in enumerate(pieces):
            last = number == len(pieces) - 1
            rebuilt.append(
                segment.model_copy(
                    update={
                        "id": segment.id if number == 0 else new_id("seg"),
                        "in_us": piece_in,
                        "out_us": piece_out,
                        "stale": True,
                        "transition_out": segment.transition_out if last else Transition(),
                    },
                    deep=True,
                )
            )
        verb = "Split" if len(pieces) > 1 else "Trimmed"
        notices.append(f"{verb} {name} to avoid the excluded range {span(start, end)}.")
    timeline.segments = rebuilt
    return notices


def _drop_handles_into_exclusions(project: Project, timeline: EditTimeline) -> list[str]:
    notices: list[str] = []
    for index, segment in enumerate(timeline.segments):
        if segment.transition_out.kind not in HANDLE_TRANSITIONS:
            continue
        affected = [index] + ([index + 1] if index + 1 < len(timeline.segments) else [])
        for position in affected:
            used = used_source_range(timeline, position)
            clip_id = timeline.segments[position].clip_id
            if first_exclusion_hit(project, clip_id, *used) is not None:
                segment.transition_out = Transition()
                segment.stale = True
                notices.append(
                    f"Replaced the {TransitionKind.CROSSFADE.value} after segment "
                    f"{span(segment.in_us, segment.out_us)} in timeline {timeline.name} "
                    "with a cut: it needed excluded frames."
                )
                break
    return notices


# --- Invariants --------------------------------------------------------------------------------


def _all_ids(project: Project) -> Iterator[str]:
    yield from (clip.id for clip in project.clips)
    yield from (zone.id for zone in project.zones)
    for timeline in project.timelines:
        yield timeline.id
        yield from (segment.id for segment in timeline.segments)
        yield from (text.id for text in timeline.texts)
        yield from (cue.id for cue in timeline.music)


def check_invariants(project: Project) -> list[str]:
    """Return a user-facing message for every broken invariant (empty when the project is valid)."""
    problems: list[str] = []
    duplicates = [item for item, count in Counter(_all_ids(project)).items() if count > 1]
    problems.extend(f"Identifier {item} is used more than once." for item in duplicates)

    clips = {clip.id: clip for clip in project.clips}
    zones = {zone.id: zone for zone in project.zones}

    for zone in project.zones:
        name = f"{zone.kind.value} zone {zone.id} ({span(zone.start_us, zone.end_us)})"
        clip = clips.get(zone.clip_id)
        if clip is None:
            problems.append(f"The {name} references missing clip {zone.clip_id}.")
            continue
        if zone.end_us > clip.duration_us:
            problems.append(f"The {name} ends after the end of clip {clip.name}.")
        if zone.kind is ZoneKind.EXCLUDE:
            if zone.origin is not Origin.USER:
                problems.append(f"The {name} was not created by the user.")
            continue
        hit = first_exclusion_hit(project, zone.clip_id, zone.start_us, zone.end_us)
        if hit is not None:
            problems.append(
                f"The {name} overlaps the exclude zone {span(hit.start_us, hit.end_us)}."
            )
        if zone.parent_id is not None:
            parent = zones.get(zone.parent_id)
            if parent is None or parent.kind is not ZoneKind.CANDIDATE:
                problems.append(f"The parent of {name} is not an existing candidate zone.")
            elif parent.clip_id != zone.clip_id:
                problems.append(f"The parent of {name} belongs to a different clip.")

    for timeline in project.timelines:
        for index, segment in enumerate(timeline.segments):
            name = (
                f"segment {segment.id} ({span(segment.in_us, segment.out_us)}) "
                f"in timeline {timeline.name}"
            )
            clip = clips.get(segment.clip_id)
            if clip is None:
                problems.append(f"The {name} references missing clip {segment.clip_id}.")
                continue
            used_start, used_end = used_source_range(timeline, index)
            if used_start < 0 or used_end > clip.duration_us:
                problems.append(
                    f"The {name} needs frames outside clip {clip.name} "
                    "(check its range and transition handles)."
                )
            hit = first_exclusion_hit(project, segment.clip_id, used_start, used_end)
            if hit is not None:
                problems.append(
                    f"The {name} uses frames of the exclude zone {span(hit.start_us, hit.end_us)} "
                    "(transition handles included)."
                )
            if segment.zone_id is not None:
                source = zones.get(segment.zone_id)
                if source is None or source.kind is not ZoneKind.HIGHLIGHT:
                    problems.append(f"The source zone of {name} is not an existing highlight zone.")
            transition = segment.transition_out
            if transition.kind in HANDLE_TRANSITIONS:
                following = timeline.segments[index + 1 : index + 2]
                shortest = min(s.output_duration_us for s in [segment, *following])
                if transition.duration_us > shortest:
                    problems.append(
                        f"The {transition.kind.value} after {name} is longer than "
                        "the shots it joins."
                    )
    return problems
