"""SPEC §4.4: exclusion, locks, tolerance and staleness rules."""

import pytest

from easy_video_editor.core.commands import (
    AddSegment,
    CreateZone,
    DeleteZone,
    SetTransition,
    SetZoneLock,
    UpdateSegment,
    UpdateZone,
)
from easy_video_editor.core.errors import CommandError, NotFoundError
from easy_video_editor.core.model import Origin, Transition, TransitionKind, ZoneKind
from easy_video_editor.core.session import ProjectSession

from .conftest import AI, AUTO, BLUE, RED, USER, YELLOW, S, zone

AUTOMATED = [AI, AUTO]


def ranges(session: ProjectSession, kind: ZoneKind) -> list[tuple[float, float]]:
    return sorted((z.start_us / S, z.end_us / S) for z in session.project.zones if z.kind is kind)


def segment_ranges(session: ProjectSession) -> list[tuple[float, float]]:
    return [(s.in_us / S, s.out_us / S) for s in session.project.timeline("tl").segments]


def add_segment(session: ProjectSession, start_s: float, end_s: float, **kwargs: object) -> str:
    command = AddSegment.model_validate(
        {
            "timeline_id": "tl",
            "clip_id": "clip",
            "in_us": int(start_s * S),
            "out_us": int(end_s * S),
            **kwargs,
        }
    )
    return session.execute(command, USER).created_ids[0]


CROSSFADE_1S = Transition(kind=TransitionKind.CROSSFADE, duration_us=S)


# --- Creation and locking ----------------------------------------------------------------------


def test_user_zones_are_locked_automated_zones_are_not(session: ProjectSession) -> None:
    user_id = zone(session, BLUE, 0, 5)
    ai_id = zone(session, BLUE, 10, 15, AI)
    user_zone, ai_zone = session.project.zone(user_id), session.project.zone(ai_id)
    assert (user_zone.origin, user_zone.locked) == (USER, True)
    assert (ai_zone.origin, ai_zone.locked) == (AI, False)


@pytest.mark.parametrize("origin", AUTOMATED)
def test_automation_cannot_create_red_zones(session: ProjectSession, origin: Origin) -> None:
    with pytest.raises(CommandError, match="Only the user"):
        zone(session, RED, 0, 5, origin)
    assert session.project.zones == []


@pytest.mark.parametrize("origin", AUTOMATED)
def test_automation_cannot_modify_or_delete_red_zones(
    session: ProjectSession, origin: Origin
) -> None:
    red = zone(session, RED, 0, 5)
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(UpdateZone(zone_id=red, end_us=6 * S), origin)
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(UpdateZone(zone_id=red, label="x"), origin)
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(DeleteZone(zone_id=red), origin)
    assert ranges(session, RED) == [(0, 5)]


@pytest.mark.parametrize("origin", AUTOMATED)
def test_automation_cannot_touch_locked_zones(session: ProjectSession, origin: Origin) -> None:
    locked = zone(session, BLUE, 0, 5)
    with pytest.raises(CommandError, match="locked"):
        session.execute(UpdateZone(zone_id=locked, end_us=6 * S), origin)
    with pytest.raises(CommandError, match="locked"):
        session.execute(DeleteZone(zone_id=locked), origin)
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(SetZoneLock(zone_id=locked, locked=False), origin)
    assert ranges(session, BLUE) == [(0, 5)]


def test_automation_can_change_unlocked_zones(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 0, 5, AI)
    session.execute(UpdateZone(zone_id=blue, end_us=7 * S, score=0.8), AUTO)
    assert session.project.zone(blue).end_us == 7 * S
    assert session.project.zone(blue).locked is False
    session.execute(DeleteZone(zone_id=blue), AI)
    assert session.project.zones == []


def test_user_modification_locks_and_explicit_unlock(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 0, 5, AI)
    session.execute(UpdateZone(zone_id=blue, label="Big jump", priority=2), USER)
    assert session.project.zone(blue).locked
    session.execute(SetZoneLock(zone_id=blue, locked=False), USER)
    assert not session.project.zone(blue).locked
    session.execute(UpdateZone(zone_id=blue, end_us=6 * S), AI)


def test_zone_must_fit_clip_and_reference_existing_clip(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="ends after the end of clip"):
        zone(session, BLUE, 55, 61)
    with pytest.raises(NotFoundError):
        session.execute(CreateZone(kind=BLUE, clip_id="nope", start_us=0, end_us=S), USER)


def test_update_rejects_inverted_range(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 0, 5)
    with pytest.raises(CommandError, match="after its start"):
        session.execute(UpdateZone(zone_id=blue, start_us=6 * S), USER)


# --- Red zones are absolute --------------------------------------------------------------------


@pytest.mark.parametrize("origin", [USER, AI, AUTO])
@pytest.mark.parametrize("kind", [BLUE, YELLOW])
def test_zones_can_never_overlap_red(
    session: ProjectSession, origin: Origin, kind: ZoneKind
) -> None:
    zone(session, RED, 10, 20)
    with pytest.raises(CommandError, match="overlaps the exclude zone"):
        zone(session, kind, 15, 25, origin)
    zone(session, kind, 20, 25, origin)  # touching is fine: ranges are half-open


@pytest.mark.parametrize("origin", AUTOMATED)
def test_automation_cannot_move_zone_into_red(session: ProjectSession, origin: Origin) -> None:
    zone(session, RED, 10, 20)
    blue = zone(session, BLUE, 0, 5, origin)
    with pytest.raises(CommandError, match="overlaps the exclude zone"):
        session.execute(UpdateZone(zone_id=blue, end_us=11 * S), origin)


def test_red_zone_trims_zones_at_both_ends(session: ProjectSession) -> None:
    zone(session, BLUE, 0, 10)
    zone(session, BLUE, 20, 30)
    result = session.execute(
        CreateZone(kind=RED, clip_id="clip", start_us=8 * S, end_us=22 * S), USER
    )
    assert ranges(session, BLUE) == [(0, 8), (22, 30)]
    assert len(result.notices) == 2
    assert all("Trimmed" in notice for notice in result.notices)


def test_red_zone_splits_locked_zone_in_the_middle(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 0, 30, label="Lap")
    result = session.execute(
        CreateZone(kind=RED, clip_id="clip", start_us=10 * S, end_us=12 * S), USER
    )
    assert ranges(session, BLUE) == [(0, 10), (12, 30)]
    pieces = session.project.zones_of("clip", BLUE)
    assert pieces[0].id == blue
    assert all(piece.locked and piece.label == "Lap" for piece in pieces)
    assert pieces[1].id != blue
    assert "Split" in result.notices[0]


def test_red_zone_removes_fully_covered_zones(session: ProjectSession) -> None:
    zone(session, YELLOW, 5, 8)
    result = session.execute(
        CreateZone(kind=RED, clip_id="clip", start_us=4 * S, end_us=9 * S), USER
    )
    assert ranges(session, YELLOW) == []
    assert "Removed" in result.notices[0]


def test_red_split_reassigns_yellow_children(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 0, 30, AI)
    early = zone(session, YELLOW, 2, 4, AI, parent_id=blue)
    late = zone(session, YELLOW, 25, 28, AI, parent_id=blue)
    zone(session, RED, 10, 12)
    second_blue = next(z for z in session.project.zones_of("clip", BLUE) if z.id != blue)
    assert session.project.zone(early).parent_id == blue
    assert session.project.zone(late).parent_id == second_blue.id
    assert session.project.zone(early).stale and session.project.zone(late).stale


def test_extending_red_zone_trims_other_zones(session: ProjectSession) -> None:
    red = zone(session, RED, 10, 12)
    zone(session, BLUE, 12, 20)
    result = session.execute(UpdateZone(zone_id=red, end_us=15 * S), USER)
    assert ranges(session, BLUE) == [(15, 20)]
    assert result.notices


def test_red_zone_only_affects_its_clip(session: ProjectSession) -> None:
    from easy_video_editor.core.commands import AddClip

    session.execute(AddClip(path="/media/other.mp4", duration_us=60 * S, clip_id="other"), USER)
    session.execute(CreateZone(kind=BLUE, clip_id="other", start_us=0, end_us=10 * S), USER)
    zone(session, RED, 0, 10)
    assert len(session.project.zones_of("other", BLUE)) == 1


# --- Red zones and the edit timeline -----------------------------------------------------------


@pytest.mark.parametrize("origin", [USER, AI, AUTO])
def test_segments_can_never_use_red_frames(session: ProjectSession, origin: Origin) -> None:
    zone(session, RED, 10, 20)
    with pytest.raises(CommandError, match="exclude zone"):
        session.execute(
            AddSegment(timeline_id="tl", clip_id="clip", in_us=5 * S, out_us=11 * S), origin
        )
    segment = add_segment(session, 5, 10)
    with pytest.raises(CommandError, match="exclude zone"):
        session.execute(UpdateSegment(timeline_id="tl", segment_id=segment, out_us=12 * S), origin)


def test_transition_handles_count_as_used_frames(session: ProjectSession) -> None:
    zone(session, RED, 10, 20)
    first = add_segment(session, 5, 10)
    add_segment(session, 30, 35)
    with pytest.raises(CommandError, match="transition handles included"):
        session.execute(
            SetTransition(timeline_id="tl", segment_id=first, transition=CROSSFADE_1S), AI
        )
    # A dip to black does not blend shots, so it needs no handles.
    session.execute(
        SetTransition(
            timeline_id="tl",
            segment_id=first,
            transition=Transition(kind=TransitionKind.DIP_TO_BLACK, duration_us=S),
        ),
        AI,
    )


def test_incoming_handle_counts_too(session: ProjectSession) -> None:
    zone(session, RED, 10, 20)
    first = add_segment(session, 30, 35)
    add_segment(session, 20, 25)
    with pytest.raises(CommandError, match="transition handles included"):
        session.execute(
            SetTransition(timeline_id="tl", segment_id=first, transition=CROSSFADE_1S), USER
        )


def test_handles_must_exist_in_the_clip(session: ProjectSession) -> None:
    add_segment(session, 0, 5)
    second = add_segment(session, 55, 60)
    with pytest.raises(CommandError, match="outside clip"):
        session.execute(
            SetTransition(timeline_id="tl", segment_id=second, transition=CROSSFADE_1S), USER
        )


def test_handles_scale_with_speed(session: ProjectSession) -> None:
    zone(session, RED, 11, 20)
    first = add_segment(session, 5, 10, speed=2.0)
    add_segment(session, 30, 35)
    # 0.5 s of output after the cut at 2x speed needs 1 s of source: 10-11 s is still free.
    session.execute(
        SetTransition(timeline_id="tl", segment_id=first, transition=CROSSFADE_1S), USER
    )
    with pytest.raises(CommandError, match="transition handles included"):
        session.execute(UpdateSegment(timeline_id="tl", segment_id=first, speed=3.0), USER)


def test_crossfade_cannot_exceed_shots(session: ProjectSession) -> None:
    first = add_segment(session, 5, 6)
    add_segment(session, 30, 35)
    with pytest.raises(CommandError, match="longer than"):
        session.execute(
            SetTransition(
                timeline_id="tl",
                segment_id=first,
                transition=Transition(kind=TransitionKind.CROSSFADE, duration_us=2 * S),
            ),
            USER,
        )


def test_red_zone_trims_splits_and_removes_segments(session: ProjectSession) -> None:
    add_segment(session, 0, 10)
    add_segment(session, 20, 40)
    add_segment(session, 50, 52)
    zone(session, RED, 8, 9)
    zone(session, RED, 25, 30)
    result = session.execute(
        CreateZone(kind=RED, clip_id="clip", start_us=49 * S, end_us=53 * S), USER
    )
    assert segment_ranges(session) == [(0, 8), (9, 10), (20, 25), (30, 40)]
    assert all(s.stale for s in session.project.timeline("tl").segments)
    assert "Removed" in result.notices[0]


def test_red_zone_turns_crossfade_into_cut(session: ProjectSession) -> None:
    first = add_segment(session, 5, 10)
    add_segment(session, 30, 35)
    session.execute(
        SetTransition(timeline_id="tl", segment_id=first, transition=CROSSFADE_1S), USER
    )
    result = session.execute(
        CreateZone(kind=RED, clip_id="clip", start_us=10 * S, end_us=12 * S), USER
    )
    segment = session.project.timeline("tl").segment(first)
    assert segment.transition_out.kind is TransitionKind.CUT
    assert segment.stale
    assert any("with a cut" in notice for notice in result.notices)


def test_split_segment_keeps_transition_on_last_piece(session: ProjectSession) -> None:
    first = add_segment(session, 0, 20)
    add_segment(session, 30, 35)
    session.execute(
        SetTransition(timeline_id="tl", segment_id=first, transition=CROSSFADE_1S), USER
    )
    zone(session, RED, 5, 6)
    first_piece, second_piece, _ = session.project.timeline("tl").segments
    assert first_piece.transition_out.kind is TransitionKind.CUT
    assert second_piece.transition_out.kind is TransitionKind.CROSSFADE


# --- Yellow tolerance --------------------------------------------------------------------------


def test_yellow_may_extend_beyond_parent_within_tolerance(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 10, 20, AI)
    zone(session, YELLOW, 8, 22, AI, parent_id=blue)
    with pytest.raises(CommandError, match="tolerance"):
        zone(session, YELLOW, 7.9, 12, AI, parent_id=blue)
    with pytest.raises(CommandError, match="tolerance"):
        zone(session, YELLOW, 18, 22.1, USER, parent_id=blue)


def test_tolerance_is_configurable_and_checked_on_update(session: ProjectSession) -> None:
    from easy_video_editor.core.commands import UpdateSettings

    session.execute(UpdateSettings(changes={"yellow_tolerance_us": 0}), USER)
    blue = zone(session, BLUE, 10, 20, AI)
    yellow = zone(session, YELLOW, 10, 20, AI, parent_id=blue)
    with pytest.raises(CommandError, match="tolerance"):
        session.execute(UpdateZone(zone_id=yellow, end_us=21 * S), AI)


def test_one_blue_may_yield_several_yellows(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 10, 30, AI)
    zone(session, YELLOW, 10, 15, AI, parent_id=blue)
    zone(session, YELLOW, 20, 25, AI, parent_id=blue)
    assert len(session.project.children_of(blue)) == 2


def test_yellow_parent_must_be_a_blue_zone(session: ProjectSession) -> None:
    other_yellow = zone(session, YELLOW, 0, 5, AI)
    with pytest.raises(CommandError, match="must be an existing candidate"):
        zone(session, YELLOW, 6, 8, AI, parent_id=other_yellow)
    with pytest.raises(NotFoundError):
        zone(session, YELLOW, 6, 8, AI, parent_id="missing")


# --- Staleness ---------------------------------------------------------------------------------


def test_editing_blue_range_marks_children_stale(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 10, 20, AI)
    yellow = zone(session, YELLOW, 12, 15, AI, parent_id=blue)
    session.execute(UpdateZone(zone_id=blue, label="renamed"), USER)
    assert not session.project.zone(yellow).stale
    session.execute(UpdateZone(zone_id=blue, end_us=25 * S), USER)
    assert session.project.zone(yellow).stale


def test_editing_yellow_range_marks_segments_stale(session: ProjectSession) -> None:
    yellow = zone(session, YELLOW, 10, 15, AI)
    segment = add_segment(session, 10, 15, zone_id=yellow)
    session.execute(UpdateZone(zone_id=yellow, start_us=11 * S), AI)
    assert session.project.timeline("tl").segment(segment).stale
    session.execute(UpdateSegment(timeline_id="tl", segment_id=segment, in_us=11 * S), AI)
    assert not session.project.timeline("tl").segment(segment).stale


def test_updating_a_stale_zone_refreshes_it(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 10, 20, AI)
    yellow = zone(session, YELLOW, 12, 15, AI, parent_id=blue)
    session.execute(UpdateZone(zone_id=blue, end_us=25 * S), AI)
    session.execute(UpdateZone(zone_id=yellow, end_us=16 * S), AI)
    assert not session.project.zone(yellow).stale


def test_deleting_zones_detaches_dependents(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 10, 20, AI)
    yellow = zone(session, YELLOW, 12, 15, AI, parent_id=blue)
    segment = add_segment(session, 12, 15, zone_id=yellow)
    session.execute(DeleteZone(zone_id=blue), AI)
    assert session.project.zone(yellow).parent_id is None
    assert session.project.zone(yellow).stale
    session.execute(DeleteZone(zone_id=yellow), AI)
    used = session.project.timeline("tl").segment(segment)
    assert used.zone_id is None and used.stale


def test_segment_source_zone_must_be_yellow(session: ProjectSession) -> None:
    blue = zone(session, BLUE, 10, 20, AI)
    with pytest.raises(CommandError, match="not an existing highlight"):
        add_segment(session, 10, 15, zone_id=blue)


# --- Atomicity ---------------------------------------------------------------------------------


def test_rejected_command_leaves_project_unchanged(session: ProjectSession) -> None:
    zone(session, RED, 10, 20)
    before = session.project.model_dump()
    with pytest.raises(CommandError):
        zone(session, BLUE, 0, 15, AI)
    assert session.project.model_dump() == before


# --- Invariants on corrupted projects (e.g. edited outside EVE) --------------------------------


def test_invariants_report_broken_references(session: ProjectSession) -> None:
    from easy_video_editor.core.commands import AddClip
    from easy_video_editor.core.model import Segment, Zone
    from easy_video_editor.core.rules import check_invariants

    session.execute(AddClip(path="/media/other.mp4", duration_us=60 * S, clip_id="other"), USER)
    blue = zone(session, BLUE, 0, 10)
    project = session.project.model_copy(deep=True)
    project.zones += [
        Zone(id=blue, kind=BLUE, clip_id="clip", start_us=20 * S, end_us=21 * S, origin=AI),
        Zone(id="orphan", kind=BLUE, clip_id="gone", start_us=0, end_us=S, origin=AI),
        Zone(id="red_ai", kind=RED, clip_id="clip", start_us=30 * S, end_us=31 * S, origin=AI),
        Zone(id="y", kind=YELLOW, clip_id="other", start_us=0, end_us=S, origin=AI, parent_id=blue),
    ]
    project.timeline("tl").segments.append(Segment(id="s", clip_id="gone", in_us=0, out_us=S))
    problems = " ".join(check_invariants(project))
    for expected in [
        f"Identifier {blue} is used more than once",
        "references missing clip gone",
        "was not created by the user",
        "belongs to a different clip",
        "segment s",
    ]:
        assert expected in problems
