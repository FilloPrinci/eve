"""Final validation before render (SPEC §4.4 rule 5)."""

from collections.abc import Callable
from pathlib import Path

from easy_video_editor.core.commands import AddSegment, UpdateSettings
from easy_video_editor.core.model import Segment
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.core.validation import Severity, validate_for_render

from .conftest import RED, USER, S, zone


def always(_: Path) -> bool:
    return True


def never(_: Path) -> bool:
    return False


def codes(
    session: ProjectSession,
    preset_id: str = "youtube",
    exists: Callable[[Path], bool] = always,
) -> set[str]:
    preset = session.project.settings.preset(preset_id)
    report = validate_for_render(session.project, "tl", preset, file_exists=exists)
    return {issue.code for issue in report.issues}


def add(session: ProjectSession, start_s: int, end_s: int) -> None:
    session.execute(
        AddSegment(timeline_id="tl", clip_id="clip", in_us=start_s * S, out_us=end_s * S), USER
    )


def test_valid_timeline(session: ProjectSession) -> None:
    add(session, 0, 5)
    report = validate_for_render(
        session.project, "tl", session.project.settings.preset("youtube"), file_exists=always
    )
    assert report.ok and report.issues == []


def test_empty_timeline_and_aspect_mismatch(session: ProjectSession) -> None:
    assert codes(session, "shorts") == {"empty_timeline", "aspect_mismatch"}


def test_missing_media(session: ProjectSession) -> None:
    add(session, 0, 5)
    add(session, 10, 15)
    preset = session.project.settings.preset("youtube")
    report = validate_for_render(session.project, "tl", preset, file_exists=never)
    assert [issue.code for issue in report.errors] == ["missing_media"]
    assert report.errors[0].item_ids == ("clip",)


def test_too_long_for_preset(session: ProjectSession) -> None:
    add(session, 0, 50)
    presets = [p.model_dump() for p in session.project.settings.output_presets]
    presets[0]["max_duration_us"] = 30 * S
    session.execute(UpdateSettings(changes={"output_presets": presets}), USER)
    assert codes(session) == {"too_long"}


def test_red_frames_are_caught_even_if_invariants_were_bypassed(session: ProjectSession) -> None:
    # Simulate a project edited outside EVE: validation must not trust the invariants.
    zone(session, RED, 10, 20)
    project = session.project.model_copy(deep=True)
    project.timeline("tl").segments.append(
        Segment(id="bad", clip_id="clip", in_us=5 * S, out_us=12 * S)
    )
    preset = project.settings.preset("youtube")
    report = validate_for_render(project, "tl", preset, file_exists=always)
    assert [(i.code, i.item_ids[0]) for i in report.errors] == [("excluded_frames", "bad")]


def test_stale_segments_only_warn(session: ProjectSession) -> None:
    add(session, 0, 15)
    zone(session, RED, 5, 6)
    preset = session.project.settings.preset("youtube")
    report = validate_for_render(session.project, "tl", preset, file_exists=always)
    assert report.ok
    assert {issue.severity for issue in report.issues} == {Severity.WARNING}
