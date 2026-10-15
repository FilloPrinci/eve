from fractions import Fraction

import pytest
from pydantic import ValidationError

from easy_video_editor.core.model import (
    EditTimeline,
    EndCard,
    OutputPreset,
    Project,
    ProjectSettings,
    Segment,
    Transition,
    TransitionKind,
    Zone,
    default_output_presets,
)
from easy_video_editor.core.ranges import overlaps, subtract
from easy_video_editor.core.timecode import (
    format_us,
    frame_to_us,
    seconds_to_us,
    snap_to_frame,
    us_to_frame,
)

from .conftest import AI, BLUE, RED, YELLOW, S


def test_zone_requires_end_after_start() -> None:
    with pytest.raises(ValidationError):
        Zone(id="z", kind=BLUE, clip_id="c", start_us=5, end_us=5, origin=AI)


def test_only_highlights_have_parents() -> None:
    with pytest.raises(ValidationError):
        Zone(id="z", kind=RED, clip_id="c", start_us=0, end_us=5, origin=AI, parent_id="p")
    Zone(id="z", kind=YELLOW, clip_id="c", start_us=0, end_us=5, origin=AI, parent_id="p")


def test_zone_rejects_float_times() -> None:
    with pytest.raises(ValidationError):
        Zone.model_validate(
            {
                "id": "z",
                "kind": "candidate",
                "clip_id": "c",
                "start_us": 0.5,
                "end_us": 5,
                "origin": "ai",
            }
        )


def test_transition_duration_rules() -> None:
    Transition()
    Transition(kind=TransitionKind.CROSSFADE, duration_us=S)
    with pytest.raises(ValidationError):
        Transition(kind=TransitionKind.CUT, duration_us=S)
    with pytest.raises(ValidationError):
        Transition(kind=TransitionKind.FLASH)


def test_default_presets_cover_three_aspects() -> None:
    assert [preset.aspect for preset in default_output_presets()] == ["16:9", "9:16", "4:5"]


def test_settings_reject_unknown_enabled_preset() -> None:
    with pytest.raises(ValidationError):
        ProjectSettings(enabled_preset_ids=["nope"])
    with pytest.raises(ValidationError):
        ProjectSettings(
            output_presets=[
                OutputPreset(id="a", name="A", width=10, height=10),
                OutputPreset(id="a", name="B", width=10, height=10),
            ],
            enabled_preset_ids=[],
        )


def test_timeline_duration_includes_speed_and_end_card() -> None:
    timeline = EditTimeline(
        id="tl",
        name="tl",
        aspect="16:9",
        segments=[
            Segment(id="a", clip_id="c", in_us=0, out_us=2 * S),
            Segment(id="b", clip_id="c", in_us=0, out_us=2 * S, speed=2.0),
        ],
        end_card=EndCard(duration_us=3 * S),
    )
    assert timeline.duration_us == 6 * S
    timeline.end_card.enabled = False
    assert timeline.duration_us == 3 * S


def test_project_json_schema_export() -> None:
    schema = Project.model_json_schema()
    assert "schema_version" in schema["properties"]


def test_ranges() -> None:
    assert overlaps(0, 10, 9, 20)
    assert not overlaps(0, 10, 10, 20)
    assert subtract(0, 10, 3, 5) == [(0, 3), (5, 10)]
    assert subtract(0, 10, 0, 5) == [(5, 10)]
    assert subtract(0, 10, 5, 15) == [(0, 5)]
    assert subtract(0, 10, -1, 11) == []
    assert subtract(0, 10, 10, 20) == [(0, 10)]


def test_timecode() -> None:
    ntsc = Fraction(30000, 1001)
    assert seconds_to_us(1.5) == 1_500_000
    assert us_to_frame(frame_to_us(1234, ntsc), ntsc) == 1234
    assert snap_to_frame(1_010_000, Fraction(25)) == 1_000_000
    assert snap_to_frame(1_030_000, Fraction(25)) == 1_040_000
    assert format_us(83_456_789) == "01:23.456"
    assert format_us(3_723_000_000) == "1:02:03.000"
