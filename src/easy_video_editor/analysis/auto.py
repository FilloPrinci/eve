"""Auto mode (SPEC §5.4): deterministic heuristics for the three phases and "Refresh stale".

Every function returns a `Plan` computed from a project snapshot; nothing here mutates the
project. Red zones and locked zones are respected by construction (and enforced again by the
commands themselves when the plan is applied).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from easy_video_editor.analysis.beats import BeatGrid
from easy_video_editor.analysis.features import ClipFeatures, Series
from easy_video_editor.analysis.plan import Plan
from easy_video_editor.analysis.scoring import dominant_feature, mask, score
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.commands import (
    AddSegment,
    AddText,
    CreateTimeline,
    CreateZone,
    DeleteZone,
    RemoveSegment,
    RemoveText,
    SetEndCard,
    SetMusic,
    SetTransition,
    UpdateSegment,
    UpdateZone,
)
from easy_video_editor.core.ids import new_id
from easy_video_editor.core.model import (
    Clip,
    EditTimeline,
    EndCard,
    Origin,
    Project,
    ProjectSettings,
    Transition,
    TransitionKind,
    Zone,
    ZoneKind,
)
from easy_video_editor.core.ranges import subtract
from easy_video_editor.core.rules import span
from easy_video_editor.core.timecode import snap_to_frame

IdFactory = Callable[[str], str]
Features = Mapping[str, ClipFeatures]

CANDIDATE_MIN_US = 1_500_000
CANDIDATE_MAX_US = 12_000_000
MERGE_GAP_US = 1_000_000
MAX_CANDIDATES = 30
CANDIDATE_BUDGET = 4
"""Total candidate time is at most this many times the target duration."""
SCORE_PERCENTILE = 65
MIN_THRESHOLD = 0.15
MAX_HIGHLIGHTS_PER_CANDIDATE = 3
EXTRA_HIGHLIGHT_RATIO = 0.8
"""Extra highlights in a candidate must score at least this fraction of its best one."""
VERTICAL_SHOT_FACTOR = 0.8
"""Vertical/social formats use shorter shots."""
TITLE_US = 1_500_000


# --- Helpers -------------------------------------------------------------------------------------


def _fps(clip: Clip) -> Fraction:
    return clip.frame_rate.as_fraction() if clip.frame_rate else Fraction(30)


def _snap(clip: Clip, us: int) -> int:
    return min(max(snap_to_frame(us, _fps(clip)), 0), clip.duration_us)


def _ranges(
    project: Project, clip_id: str, kind: ZoneKind, locked_only: bool = False
) -> list[tuple[int, int]]:
    return [
        (zone.start_us, zone.end_us)
        for zone in project.zones_of(clip_id, kind)
        if zone.locked or not locked_only
    ]


def _longest_piece(start: int, end: int, blocked: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Longest part of ``[start, end)`` that overlaps none of ``blocked``."""
    pieces = [(start, end)]
    for cut_start, cut_end in blocked:
        pieces = [p for piece in pieces for p in subtract(*piece, cut_start, cut_end)]
    return max(pieces, key=lambda p: p[1] - p[0]) if pieces else None


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges ``[a, b)`` where ``flags`` is true."""
    padded = np.concatenate([[False], flags, [False]]).astype(np.int8)
    changes = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b)) for a, b in zip(changes[::2], changes[1::2], strict=True)]


def best_window(values: Series, steps: int, first: int, last: int) -> tuple[int, float] | None:
    """Start index and mean of the best window of ``steps`` values inside ``[first, last)``,
    using only windows without NaN."""
    first, last = max(first, 0), min(last, len(values))
    if steps <= 0 or last - first < steps:
        return None
    region = values[first:last]
    valid = np.isfinite(region)
    filled = np.where(valid, region, 0.0)
    sums = np.convolve(filled, np.ones(steps), mode="valid")
    invalid = np.convolve((~valid).astype(np.float32), np.ones(steps), mode="valid")
    sums[invalid > 0] = -np.inf
    if not np.isfinite(sums).any():
        return None
    index = int(np.argmax(sums))
    return first + index, float(sums[index] / steps)


def shot_length(settings: ProjectSettings, beats: BeatGrid | None, factor: float = 1.0) -> int:
    """Preferred shot length: the middle of the range, rounded to whole beats when music is set."""
    low = round(settings.shot_length_min_us * factor)
    high = round(settings.shot_length_max_us * factor)
    preferred = (low + high) // 2
    if beats is None or beats.period_us <= 0:
        return preferred
    count = max(1, round(preferred / beats.period_us))
    while count > 1 and count * beats.period_us > high:
        count -= 1
    while count * beats.period_us < low and (count + 1) * beats.period_us <= high:
        count += 1
    return count * beats.period_us


@dataclass(frozen=True)
class _Window:
    clip: Clip
    start_us: int
    end_us: int
    score: float
    label: str


# --- Phase 1 -------------------------------------------------------------------------------------


def plan_phase1(project: Project, features: Features, id_factory: IdFactory = new_id) -> Plan:
    """Candidate (blue) zones from the numeric features."""
    plan = Plan("Phase 1 (auto): find candidate zones")
    for zone in project.zones:
        if zone.kind is ZoneKind.CANDIDATE and not zone.locked:
            plan.add(DeleteZone(zone_id=zone.id))
    found: list[_Window] = []
    for number, clip in enumerate(project.clips, start=1):
        clip_features = features.get(clip.id)
        if clip_features is None:
            plan.messages.append(f"Skipped {clip.name}: not analysed.")
            continue
        plan.messages.append(f"Analysing clip {number} of {len(project.clips)}: {clip.name}")
        found += _candidates(project, clip, clip_features)
    found.sort(key=lambda w: w.score, reverse=True)
    budget = project.settings.target_duration_us * CANDIDATE_BUDGET
    chosen: list[_Window] = []
    total = 0
    for window in found:
        if len(chosen) >= MAX_CANDIDATES or total >= budget:
            break
        chosen.append(window)
        total += window.end_us - window.start_us
    for window in sorted(chosen, key=lambda w: (w.clip.id, w.start_us)):
        plan.add(
            CreateZone(
                kind=ZoneKind.CANDIDATE,
                clip_id=window.clip.id,
                start_us=window.start_us,
                end_us=window.end_us,
                score=round(window.score, 3),
                label=window.label,
                zone_id=id_factory("zone"),
            )
        )
        plan.messages.append(
            f"Selected {window.clip.name} {span(window.start_us, window.end_us)}: {window.label}"
        )
    plan.messages.append(f"Created {len(chosen)} candidate zones.")
    return plan


def _candidates(project: Project, clip: Clip, features: ClipFeatures) -> list[_Window]:
    hop = features.hop_us
    blocked = _ranges(project, clip.id, ZoneKind.EXCLUDE) + _ranges(
        project, clip.id, ZoneKind.CANDIDATE, locked_only=True
    )
    values = mask(score(features, project.settings.mood), hop, blocked)
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return []
    threshold = max(float(np.percentile(valid, SCORE_PERCENTILE)), MIN_THRESHOLD)
    runs = _runs(np.isfinite(values) & (values >= threshold))
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        gap_ok = merged and start - merged[-1][1] <= MERGE_GAP_US // hop
        if gap_ok and np.isfinite(values[merged[-1][1] : start]).all():
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    windows = []
    max_steps = CANDIDATE_MAX_US // hop
    for start, end in merged:
        pieces = max(1, -(-(end - start) // max_steps))
        size = -(-(end - start) // pieces)
        for piece_start in range(start, end, size):
            piece_end = min(piece_start + size, end)
            if (piece_end - piece_start) * hop < CANDIDATE_MIN_US:
                continue
            exact = _longest_piece(
                _snap(clip, piece_start * hop),
                _snap(clip, min(piece_end * hop, clip.duration_us)),
                blocked,
            )
            if exact is None or exact[1] - exact[0] < CANDIDATE_MIN_US:
                continue
            windows.append(
                _Window(
                    clip,
                    exact[0],
                    exact[1],
                    float(np.nanmean(values[piece_start:piece_end])),
                    dominant_feature(features, piece_start, piece_end),
                )
            )
    return windows


# --- Phase 2 -------------------------------------------------------------------------------------


def _highlight_windows(
    project: Project, candidate: Zone, features: ClipFeatures, length_us: int
) -> list[_Window]:
    clip = project.clip(candidate.clip_id)
    hop = features.hop_us
    tolerance = project.settings.yellow_tolerance_us
    blocked = _ranges(project, clip.id, ZoneKind.EXCLUDE) + _ranges(
        project, clip.id, ZoneKind.HIGHLIGHT, locked_only=True
    )
    values = mask(score(features, project.settings.mood), hop, blocked)
    low = max(0, candidate.start_us - tolerance)
    high = min(clip.duration_us, candidate.end_us + tolerance)
    first, last = low // hop, -(-high // hop)
    steps = max(1, length_us // hop)
    if last - first < steps:
        steps = last - first
    windows: list[_Window] = []
    for _ in range(MAX_HIGHLIGHTS_PER_CANDIDATE):
        best = best_window(values, steps, first, last)
        if best is None or (windows and best[1] < windows[0].score * EXTRA_HIGHLIGHT_RATIO):
            break
        index, mean = best
        start = _snap(clip, max(index * hop, low))
        end = _snap(clip, min(start + length_us, high))
        exact = _longest_piece(start, end, blocked)
        values[index : index + steps] = np.nan
        if exact is None or exact[1] - exact[0] < project.settings.shot_length_min_us // 2:
            continue
        windows.append(
            _Window(
                clip,
                exact[0],
                exact[1],
                mean,
                candidate.label or dominant_feature(features, index, index + steps),
            )
        )
    return windows


def plan_phase2(
    project: Project,
    features: Features,
    beats: BeatGrid | None = None,
    id_factory: IdFactory = new_id,
) -> Plan:
    """Highlight (yellow) zones: the best shot-length windows inside each candidate."""
    plan = Plan("Phase 2 (auto): refine highlights")
    for zone in project.zones:
        if zone.kind is ZoneKind.HIGHLIGHT and not zone.locked:
            plan.add(DeleteZone(zone_id=zone.id))
    length = shot_length(project.settings, beats)
    if beats:
        plan.messages.append(f"Music at {beats.tempo_bpm:.0f} BPM: shots of {length / 1e6:.2f} s.")
    created: list[tuple[_Window, str, str]] = []
    candidates = sorted(
        project.zones_of_kind(ZoneKind.CANDIDATE), key=lambda z: (z.clip_id, z.start_us)
    )
    for candidate in candidates:
        clip_features = features.get(candidate.clip_id)
        if clip_features is None:
            continue
        for window in _highlight_windows(project, candidate, clip_features, length):
            created.append((window, candidate.id, id_factory("zone")))
    ranked = sorted(range(len(created)), key=lambda i: created[i][0].score, reverse=True)
    priority = {
        index: 3 - min(2, rank * 3 // max(len(created), 1)) for rank, index in enumerate(ranked)
    }
    for index, (window, parent_id, zone_id) in enumerate(created):
        plan.add(
            CreateZone(
                kind=ZoneKind.HIGHLIGHT,
                clip_id=window.clip.id,
                start_us=window.start_us,
                end_us=window.end_us,
                score=round(window.score, 3),
                label=window.label,
                priority=priority[index],
                parent_id=parent_id,
                zone_id=zone_id,
            )
        )
        plan.messages.append(
            f"Highlight {window.clip.name} {span(window.start_us, window.end_us)}: {window.label}"
        )
    plan.messages.append(f"Created {len(created)} highlight zones.")
    return plan


# --- Phase 3 -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Shot:
    zone: Zone
    length_us: int
    speed: float = 1.0


TRANSITIONS = {
    "cinematic": (Transition(kind=TransitionKind.CROSSFADE, duration_us=500_000), None),
    "epic": (Transition(), Transition(kind=TransitionKind.DIP_TO_BLACK, duration_us=400_000)),
    "adrenaline": (Transition(), Transition(kind=TransitionKind.FLASH, duration_us=150_000)),
}
"""Per mood: (transition between shots, transition into the climax)."""
SPEEDS = {"adrenaline": (1.1, 0.6), "epic": (1.0, 0.6)}
"""Per mood: (build-up speed, climax speed)."""


def music_path(project: Project) -> str | None:
    for timeline in project.timelines:
        for cue in timeline.music:
            return cue.path
    return None


def output_aspects(project: Project) -> list[str]:
    settings = project.settings
    aspects = [p.aspect for p in settings.output_presets if p.id in settings.enabled_preset_ids]
    return list(dict.fromkeys(aspects)) or ["16:9"]


def _is_vertical(aspect: str) -> bool:
    width, _, height = aspect.partition(":")
    return int(width) < int(height)


def _source_window(zone: Zone, output_us: int, speed: float, clip: Clip) -> tuple[int, int]:
    length = min(round(output_us * speed), zone.end_us - zone.start_us)
    center = (zone.start_us + zone.end_us) // 2
    start = min(max(center - length // 2, zone.start_us), zone.end_us - length)
    return _snap(clip, start), _snap(clip, start + length)


def plan_phase3(
    project: Project,
    beats: BeatGrid | None = None,
    brand: BrandKit | None = None,
    id_factory: IdFactory = new_id,
) -> Plan:
    """Build one edit timeline per aspect-ratio family from the highlights."""
    plan = Plan("Phase 3 (auto): assemble the edit")
    highlights = project.zones_of_kind(ZoneKind.HIGHLIGHT)
    fresh = [zone for zone in highlights if not zone.stale]
    if len(fresh) < len(highlights):
        plan.messages.append(f"{len(highlights) - len(fresh)} stale highlight(s) left out.")
    if not fresh:
        plan.messages.append("No highlights to assemble. Run phase 2 or mark yellow zones first.")
        return plan
    ranked = sorted(fresh, key=lambda z: (-(z.priority or 0), -(z.score or 0.0), z.start_us))
    mood = project.settings.mood
    between, into_climax = TRANSITIONS.get(mood, (Transition(), None))
    buildup_speed, climax_speed = SPEEDS.get(mood, (1.0, 1.0))
    for aspect in output_aspects(project):
        timeline = next((t for t in project.timelines if t.aspect == aspect), None)
        if timeline is None:
            timeline_id = id_factory("tl")
            plan.add(CreateTimeline(aspect=aspect, timeline_id=timeline_id))
            end_card_us = 3_000_000
        else:
            timeline_id = timeline.id
            for segment in timeline.segments:
                plan.add(RemoveSegment(timeline_id=timeline_id, segment_id=segment.id))
            for text in timeline.texts:
                if text.origin is not Origin.USER:
                    plan.add(RemoveText(timeline_id=timeline_id, text_id=text.id))
            end_card_us = timeline.end_card.duration_us if timeline.end_card.enabled else 0
        factor = VERTICAL_SHOT_FACTOR if _is_vertical(aspect) else 1.0
        shots = _pick_shots(
            project, ranked, beats, factor, project.settings.target_duration_us - end_card_us
        )
        shots = _order(shots, buildup_speed, climax_speed)
        segment_ids = []
        for shot in shots:
            clip = project.clip(shot.zone.clip_id)
            start, end = _source_window(shot.zone, shot.length_us, shot.speed, clip)
            segment_id = id_factory("seg")
            segment_ids.append(segment_id)
            plan.add(
                AddSegment(
                    timeline_id=timeline_id,
                    clip_id=clip.id,
                    in_us=start,
                    out_us=end,
                    speed=shot.speed,
                    zone_id=shot.zone.id,
                    segment_id=segment_id,
                )
            )
        for index, segment_id in enumerate(segment_ids[:-1]):
            climax_next = index == len(segment_ids) - 2 and into_climax is not None
            transition = into_climax if climax_next else between
            if transition is not None and transition.kind is not TransitionKind.CUT:
                plan.add(
                    SetTransition(
                        timeline_id=timeline_id, segment_id=segment_id, transition=transition
                    ),
                    optional=True,
                )
        if brand and brand.game_title and shots:
            plan.add(
                AddText(
                    timeline_id=timeline_id,
                    text=brand.game_title,
                    start_us=0,
                    end_us=min(TITLE_US, shots[0].length_us),
                    style="title",
                    text_id=id_factory("text"),
                )
            )
        _end_card_and_music(plan, timeline, timeline_id, brand, beats)
        total = sum(shot.length_us for shot in shots)
        plan.messages.append(f"Timeline {aspect}: {len(shots)} shots, {total / 1e6:.1f} s.")
    return plan


def _pick_shots(
    project: Project, ranked: list[Zone], beats: BeatGrid | None, factor: float, budget_us: int
) -> list[_Shot]:
    settings = project.settings
    longest = round(settings.shot_length_max_us * factor)
    shortest = round(settings.shot_length_min_us * factor)
    shots: list[_Shot] = []
    total = 0
    for zone in ranked:
        remaining = budget_us - total
        if remaining <= 0:
            break
        length = min(zone.end_us - zone.start_us, longest, max(remaining, shortest))
        if beats and beats.period_us and length >= beats.period_us:
            length = (length // beats.period_us) * beats.period_us
        if length < shortest // 2:
            continue
        shots.append(_Shot(zone, length))
        total += length
    return shots


def _order(shots: list[_Shot], buildup_speed: float, climax_speed: float) -> list[_Shot]:
    """Hook (strongest) first, build-up by rising score, climax (second strongest) last."""
    if len(shots) < 3:
        return shots
    hook, climax, *middle = shots
    middle.sort(key=lambda s: s.zone.score or 0.0)
    return [
        hook,
        *(_Shot(s.zone, s.length_us, buildup_speed) for s in middle),
        _Shot(climax.zone, climax.length_us, climax_speed),
    ]


def _end_card_and_music(
    plan: Plan,
    timeline: EditTimeline | None,
    timeline_id: str,
    brand: BrandKit | None,
    beats: BeatGrid | None,
) -> None:
    if brand and brand.cta_text and (timeline is None or not timeline.end_card.cta_text):
        base = timeline.end_card if timeline else EndCard()
        card = base.model_copy(update={"cta_text": brand.cta_text, "enabled": True})
        plan.add(SetEndCard(timeline_id=timeline_id, end_card=card))
    if timeline and timeline.music and beats:
        first = timeline.music[0]
        if first.source_in_us != beats.first_beat_us():
            cues = [
                first.model_copy(update={"source_in_us": beats.first_beat_us()}),
                *timeline.music[1:],
            ]
            plan.add(SetMusic(timeline_id=timeline_id, cues=cues))
            plan.messages.append("Music starts on its first beat so cuts land on the beat.")


# --- Refresh stale -------------------------------------------------------------------------------


def plan_refresh_stale(project: Project, features: Features, beats: BeatGrid | None = None) -> Plan:
    """Regenerate only stale items: highlights whose candidate changed, segments whose highlight
    changed (SPEC §4.4 rule 4)."""
    plan = Plan("Refresh stale items (auto)")
    length = shot_length(project.settings, beats)
    new_ranges: dict[str, tuple[int, int]] = {}
    for zone in project.zones_of_kind(ZoneKind.HIGHLIGHT):
        if not zone.stale:
            continue
        if zone.locked:
            plan.messages.append(
                f"Kept stale highlight {span(zone.start_us, zone.end_us)}: it is locked."
            )
            continue
        if zone.parent_id is None or zone.clip_id not in features:
            plan.messages.append(
                f"Left stale highlight {span(zone.start_us, zone.end_us)}: no candidate."
            )
            continue
        parent = project.zone(zone.parent_id)
        windows = _highlight_windows(project, parent, features[zone.clip_id], length)
        if not windows:
            continue
        window = windows[0]
        new_ranges[zone.id] = (window.start_us, window.end_us)
        plan.add(
            UpdateZone(
                zone_id=zone.id,
                start_us=window.start_us,
                end_us=window.end_us,
                score=round(window.score, 3),
            )
        )
        plan.messages.append(f"Refreshed highlight {span(window.start_us, window.end_us)}.")
    for timeline in project.timelines:
        for segment in timeline.segments:
            if not segment.stale and segment.zone_id not in new_ranges:
                continue
            if segment.zone_id is None:
                plan.add(RemoveSegment(timeline_id=timeline.id, segment_id=segment.id))
                plan.messages.append("Removed a segment whose highlight no longer exists.")
                continue
            zone = project.zone(segment.zone_id)
            start, end = new_ranges.get(zone.id, (zone.start_us, zone.end_us))
            moved = zone.model_copy(update={"start_us": start, "end_us": end})
            output = round((segment.out_us - segment.in_us) / segment.speed)
            new_in, new_out = _source_window(
                moved, output, segment.speed, project.clip(zone.clip_id)
            )
            plan.add(
                UpdateSegment(
                    timeline_id=timeline.id, segment_id=segment.id, in_us=new_in, out_us=new_out
                )
            )
    if not plan.commands:
        plan.messages.append("Nothing stale to refresh.")
    return plan
