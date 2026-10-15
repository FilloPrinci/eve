"""Clip, timeline and settings commands."""

import pytest

from easy_video_editor.core.commands import (
    AddClip,
    AddSegment,
    AddText,
    CreateTimeline,
    DeleteTimeline,
    MoveSegment,
    RemoveClip,
    RemoveSegment,
    RemoveText,
    SetEndCard,
    SetMusic,
    SetOutputPresets,
    UpdateSettings,
    UpdateText,
)
from easy_video_editor.core.errors import CommandError, NotFoundError
from easy_video_editor.core.model import EndCard, MusicCue, OutputPreset
from easy_video_editor.core.session import ProjectSession

from .conftest import AI, BLUE, USER, S, zone


def segment(session: ProjectSession, start_s: int, end_s: int) -> str:
    command = AddSegment(timeline_id="tl", clip_id="clip", in_us=start_s * S, out_us=end_s * S)
    return session.execute(command, AI).created_ids[0]


def order(session: ProjectSession) -> list[int]:
    return [s.in_us // S for s in session.project.timeline("tl").segments]


def test_add_clip_requires_user_and_absolute_path(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(AddClip(path="/media/b.mp4", duration_us=S), AI)
    with pytest.raises(CommandError, match="absolute"):
        session.execute(AddClip(path="relative.mp4", duration_us=S), USER)
    clip_id = session.execute(AddClip(path="/media/b.mp4", duration_us=S), USER).created_ids[0]
    assert session.project.clip(clip_id).name == "b.mp4"


def test_remove_clip_removes_zones_and_segments(session: ProjectSession) -> None:
    zone(session, BLUE, 0, 5)
    segment(session, 0, 5)
    result = session.execute(RemoveClip(clip_id="clip"), USER)
    assert session.project.clips == []
    assert session.project.zones == []
    assert session.project.timeline("tl").segments == []
    assert "1 zone(s) and 1 timeline segment(s)" in result.notices[0]


def test_segment_order_operations(session: ProjectSession) -> None:
    a = segment(session, 0, 1)
    segment(session, 10, 11)
    session.execute(
        AddSegment(timeline_id="tl", clip_id="clip", in_us=20 * S, out_us=21 * S, index=0), AI
    )
    assert order(session) == [20, 0, 10]
    session.execute(MoveSegment(timeline_id="tl", segment_id=a, index=99), AI)
    assert order(session) == [20, 10, 0]
    session.execute(RemoveSegment(timeline_id="tl", segment_id=a), AI)
    assert order(session) == [20, 10]
    with pytest.raises(NotFoundError):
        session.execute(RemoveSegment(timeline_id="tl", segment_id=a), AI)


def test_segment_must_fit_clip(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="outside clip"):
        segment(session, 55, 61)


def test_timelines_are_unique_per_aspect(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="already exists"):
        session.execute(CreateTimeline(aspect="16:9"), AI)
    vertical = session.execute(CreateTimeline(aspect="9:16"), AI).created_ids[0]
    session.execute(DeleteTimeline(timeline_id=vertical), USER)
    assert [t.aspect for t in session.project.timelines] == ["16:9"]


def test_text_operations(session: ProjectSession) -> None:
    text = session.execute(
        AddText(timeline_id="tl", text="V-Core", start_us=0, end_us=2 * S), AI
    ).created_ids[0]
    session.execute(UpdateText(timeline_id="tl", text_id=text, text="Race now", y=0.8), USER)
    overlay = session.project.timeline("tl").text(text)
    assert (overlay.text, overlay.y, overlay.end_us) == ("Race now", 0.8, 2 * S)
    with pytest.raises(CommandError, match="after its start"):
        session.execute(UpdateText(timeline_id="tl", text_id=text, start_us=3 * S), USER)
    session.execute(RemoveText(timeline_id="tl", text_id=text), USER)
    assert session.project.timeline("tl").texts == []


def test_music_and_end_card(session: ProjectSession) -> None:
    result = session.execute(
        SetMusic(timeline_id="tl", cues=[MusicCue(id="", path="/music/a.mp3", fade_out_us=S)]),
        USER,
    )
    assert session.project.timeline("tl").music[0].id == result.created_ids[0] != ""
    session.execute(SetEndCard(timeline_id="tl", end_card=EndCard(cta_text="Wishlist now")), AI)
    assert session.project.timeline("tl").end_card.cta_text == "Wishlist now"


def test_settings_are_user_only_and_validated(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="Only the user"):
        session.execute(UpdateSettings(changes={"mood": "cinematic"}), AI)
    session.execute(UpdateSettings(changes={"mood": "cinematic"}), USER)
    assert session.project.settings.mood == "cinematic"
    with pytest.raises(CommandError, match="Invalid settings"):
        session.execute(UpdateSettings(changes={"no_such_field": 1}), USER)
    with pytest.raises(CommandError, match="Invalid settings"):
        session.execute(UpdateSettings(changes={"enabled_preset_ids": ["missing"]}), USER)


def test_set_output_presets(session: ProjectSession) -> None:
    preset = OutputPreset(id="square", name="Square", width=1080, height=1080)
    session.execute(SetOutputPresets(presets=[preset], enabled_preset_ids=["square"]), USER)
    assert session.project.settings.preset("square").aspect == "1:1"


def test_commands_describe_themselves() -> None:
    assert RemoveClip(clip_id="c").describe() == "Remove clip"
    assert CreateTimeline(aspect="16:9").describe() == "Create timeline"


def test_command_arguments_have_json_schema() -> None:
    schema = AddSegment.model_json_schema()
    assert {"timeline_id", "clip_id", "in_us", "out_us"} <= set(schema["required"])
