"""Auto-mode planners on synthetic features (SPEC §5.4)."""

import pytest

from easy_video_editor.analysis.auto import (
    best_window,
    plan_phase1,
    plan_phase2,
    plan_phase3,
    plan_refresh_stale,
    shot_length,
)
from easy_video_editor.analysis.beats import BeatGrid
from easy_video_editor.analysis.plan import Plan, apply_plan
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.commands import (
    AddText,
    CreateTimeline,
    CreateZone,
    SetMusic,
    SetOutputPresets,
    SetTransition,
    UpdateSettings,
    UpdateZone,
)
from easy_video_editor.core.model import (
    MusicCue,
    Origin,
    OutputPreset,
    Transition,
    TransitionKind,
    ZoneKind,
)
from easy_video_editor.core.session import ProjectSession

from .conftest import S, bursts

AUTO, USER = Origin.AUTO, Origin.USER
FEATURES = {"clip": bursts([(10, 16), (30, 34), (45, 47)])}


def zones(session: ProjectSession, kind: ZoneKind) -> list[tuple[float, float]]:
    return sorted((z.start_us / S, z.end_us / S) for z in session.project.zones_of_kind(kind))


def run(session: ProjectSession, plan: Plan) -> None:
    outcome = apply_plan(session, plan, AUTO)
    assert outcome.rejected == []


def test_best_window_skips_nan() -> None:
    import numpy as np

    values = np.array([0.1, 0.9, np.nan, 0.8, 0.8, 0.2], dtype=np.float32)
    best = best_window(values, 2, 0, 6)
    assert best is not None and best[0] == 3 and best[1] == pytest.approx(0.8)
    assert best_window(values, 5, 0, 6) is None


def test_phase1_finds_the_bursts(session: ProjectSession) -> None:
    run(session, plan_phase1(session.project, FEATURES))
    found = zones(session, ZoneKind.CANDIDATE)
    assert len(found) == 3
    for (start, end), (burst_start, burst_end) in zip(
        found, [(10, 16), (30, 34), (45, 47)], strict=True
    ):
        assert start <= burst_start + 0.5 and end >= burst_end - 0.5 and end - start < 10
    zone = session.project.zones_of_kind(ZoneKind.CANDIDATE)[0]
    assert zone.origin is AUTO and not zone.locked and zone.label and zone.score


def test_phase1_respects_red_and_locked_zones(session: ProjectSession) -> None:
    session.execute(
        CreateZone(kind=ZoneKind.EXCLUDE, clip_id="clip", start_us=30 * S, end_us=35 * S), USER
    )
    session.execute(
        CreateZone(
            kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=11 * S, end_us=13 * S, label="mine"
        ),
        USER,
    )
    session.execute(
        CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=50 * S, end_us=55 * S), AUTO
    )
    run(session, plan_phase1(session.project, FEATURES))
    found = session.project.zones_of_kind(ZoneKind.CANDIDATE)
    assert any(z.label == "mine" and z.locked for z in found)  # kept
    assert not any(z.start_us == 50 * S for z in found)  # unlocked auto zone replaced
    for zone in found:
        assert zone.end_us <= 30 * S or zone.start_us >= 35 * S  # nothing on red
    others = [z for z in found if z.label != "mine"]
    assert all(z.end_us <= 11 * S or z.start_us >= 13 * S for z in others)


def test_phase2_highlights_inside_candidates(session: ProjectSession) -> None:
    run(session, plan_phase1(session.project, FEATURES))
    run(session, plan_phase2(session.project, FEATURES, None))
    tolerance = session.project.settings.yellow_tolerance_us
    highlights = session.project.zones_of_kind(ZoneKind.HIGHLIGHT)
    assert len(highlights) >= 3
    for zone in highlights:
        parent = session.project.zone(zone.parent_id)  # type: ignore[arg-type]
        assert (
            parent.start_us - tolerance <= zone.start_us < zone.end_us <= parent.end_us + tolerance
        )
        assert zone.priority in (1, 2, 3)
    assert {z.priority for z in highlights} >= {1, 3}


def test_phase2_lengths_follow_the_beat(session: ProjectSession) -> None:
    beats = BeatGrid(tuple(range(0, 60 * S, 600_000)), 600_000)
    assert shot_length(session.project.settings, beats) == 2_400_000  # 4 beats, nearest to 2.5 s
    run(session, plan_phase1(session.project, FEATURES))
    run(session, plan_phase2(session.project, FEATURES, beats))
    lengths = {z.end_us - z.start_us for z in session.project.zones_of_kind(ZoneKind.HIGHLIGHT)}
    assert all(abs(length - 2_400_000) < 40_000 for length in lengths if length > 2_000_000)


def test_phase2_keeps_locked_highlights(session: ProjectSession) -> None:
    run(session, plan_phase1(session.project, FEATURES))
    parent = session.project.zones_of_kind(ZoneKind.CANDIDATE)[0]
    session.execute(
        CreateZone(
            kind=ZoneKind.HIGHLIGHT,
            clip_id="clip",
            start_us=parent.start_us,
            end_us=parent.start_us + S,
            parent_id=parent.id,
        ),
        USER,
    )
    run(session, plan_phase2(session.project, FEATURES, None))
    locked = [z for z in session.project.zones_of_kind(ZoneKind.HIGHLIGHT) if z.locked]
    assert len(locked) == 1
    others = [z for z in session.project.zones_of_kind(ZoneKind.HIGHLIGHT) if not z.locked]
    assert all(z.end_us <= locked[0].start_us or z.start_us >= locked[0].end_us for z in others)


def prepared(session: ProjectSession) -> ProjectSession:
    run(session, plan_phase1(session.project, FEATURES))
    run(session, plan_phase2(session.project, FEATURES, None))
    return session


def test_phase3_builds_a_timeline_per_aspect(session: ProjectSession) -> None:
    prepared(session)
    session.execute(
        UpdateSettings(changes={"enabled_preset_ids": ["youtube", "shorts", "instagram_feed"]}),
        USER,
    )
    brand = BrandKit(id="b", name="b", game_title="V-CORE", cta_text="Wishlist now")
    run(session, plan_phase3(session.project, None, brand))
    project = session.project
    assert sorted(t.aspect for t in project.timelines) == ["16:9", "4:5", "9:16"]
    wide = next(t for t in project.timelines if t.aspect == "16:9")
    target = project.settings.target_duration_us
    assert target * 0.7 <= wide.duration_us <= target * 1.3
    assert wide.texts[0].text == "V-CORE" and wide.end_card.cta_text == "Wishlist now"
    scores = [project.zone(s.zone_id).score or 0.0 for s in wide.segments]  # type: ignore[arg-type]
    assert scores[0] == max(scores)  # the hook is the strongest shot
    tall = next(t for t in project.timelines if t.aspect == "9:16")
    assert (
        max(s.output_duration_us for s in tall.segments)
        <= project.settings.shot_length_max_us * 0.8 + 1
    )


def test_phase3_moods(session: ProjectSession) -> None:
    prepared(session)
    session.execute(
        UpdateSettings(changes={"mood": "cinematic", "target_duration_us": 10 * S}), USER
    )
    run(session, plan_phase3(session.project, None, None))
    segments = session.project.timelines[0].segments
    assert any(s.transition_out.kind is TransitionKind.CROSSFADE for s in segments[:-1])
    session.execute(UpdateSettings(changes={"mood": "adrenaline"}), USER)
    run(session, plan_phase3(session.project, None, None))
    segments = session.project.timelines[0].segments
    if len(segments) >= 3:
        assert (
            segments[-1].speed == 0.6 and segments[-2].transition_out.kind is TransitionKind.FLASH
        )


def test_phase3_rebuild_keeps_user_texts_and_aligns_music(session: ProjectSession) -> None:
    prepared(session)
    session.execute(CreateTimeline(aspect="16:9", timeline_id="tl"), USER)
    session.execute(AddText(timeline_id="tl", text="Mine", start_us=0, end_us=S), USER)
    session.execute(SetMusic(timeline_id="tl", cues=[MusicCue(id="m", path="/music.mp3")]), USER)
    beats = BeatGrid((250_000, 750_000, 1_250_000, 1_750_000), 500_000)
    run(session, plan_phase3(session.project, beats, None))
    timeline = session.project.timeline("tl")
    assert [t.text for t in timeline.texts] == ["Mine"]
    assert timeline.music[0].source_in_us == 250_000
    assert all(s.output_duration_us % 500_000 < 2 or s.speed != 1 for s in timeline.segments)


def test_phase3_without_highlights_does_nothing(session: ProjectSession) -> None:
    plan = plan_phase3(session.project, None, None)
    assert plan.commands == [] and "No highlights" in plan.messages[-1]


def test_transitions_that_need_red_frames_are_dropped(session: ProjectSession) -> None:
    prepared(session)
    session.execute(UpdateSettings(changes={"mood": "cinematic"}), USER)
    plan = plan_phase3(session.project, None, None)
    assert any(isinstance(c, SetTransition) for c in plan.commands)
    assert plan.optional  # transitions are optional: a rejection leaves a cut


def test_refresh_stale(session: ProjectSession) -> None:
    prepared(session)
    run(session, plan_phase3(session.project, None, None))
    parent = session.project.zones_of_kind(ZoneKind.CANDIDATE)[0]
    session.execute(UpdateZone(zone_id=parent.id, end_us=parent.end_us + S), USER)
    stale = [z for z in session.project.zones_of_kind(ZoneKind.HIGHLIGHT) if z.stale]
    assert stale
    run(session, plan_refresh_stale(session.project, FEATURES, None))
    assert not any(z.stale for z in session.project.zones_of_kind(ZoneKind.HIGHLIGHT))
    assert not any(s.stale for t in session.project.timelines for s in t.segments)
    plan = plan_refresh_stale(session.project, FEATURES, None)
    assert plan.commands == [] and "Nothing stale" in plan.messages[-1]


def test_a_phase_is_one_undo_step(session: ProjectSession) -> None:
    entries = len(session.history)
    run(session, plan_phase1(session.project, FEATURES))
    assert len(session.history) == entries + 1
    assert session.history[-1].origin is AUTO and len(session.history[-1].steps) == 3
    session.undo()
    assert session.project.zones_of_kind(ZoneKind.CANDIDATE) == []


def test_presets_without_enabled_aspects(session: ProjectSession) -> None:
    from easy_video_editor.analysis.auto import output_aspects

    session.execute(
        SetOutputPresets(
            presets=[OutputPreset(id="x", name="X", width=10, height=10)], enabled_preset_ids=[]
        ),
        USER,
    )
    assert output_aspects(session.project) == ["16:9"]
    transition = Transition(kind=TransitionKind.CUT)
    assert transition.duration_us == 0
