"""Render graph compilation and ffmpeg translation (no ffmpeg needed)."""

import json
import os
from pathlib import Path

import pytest

from easy_video_editor.core.model import Segment, Transition, TransitionKind
from easy_video_editor.media.binaries import parse_ffmpeg_major
from easy_video_editor.media.render.build import (
    RenderError,
    RenderOptions,
    build_graph,
    fitted_font_size,
    plan_reframe,
)
from easy_video_editor.media.render.ffmpeg import (
    EncodeSettings,
    atempo_chain,
    filter_script,
    filter_script_option,
    render_args,
)
from easy_video_editor.media.render.graph import ClipNode, EndCardNode, ReframeKind
from easy_video_editor.media.render.renderer import parse_loudnorm

from .render_fixtures import BRAND, MEDIA, S, build_project

GOLDEN = Path(__file__).parent / "golden"
UPDATE_GOLDEN = os.environ.get("EVE_UPDATE_GOLDEN") == "1"


def graph_for(preset_id: str = "wide", **kwargs: object):  # type: ignore[no-untyped-def]
    project = build_project(**kwargs)  # type: ignore[arg-type]
    preset = project.settings.preset(preset_id)
    timeline = next(t for t in project.timelines if t.aspect == preset.aspect)
    return project, build_graph(project, timeline, preset, RenderOptions())


def check_golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if UPDATE_GOLDEN:
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert actual == path.read_text(encoding="utf-8"), f"golden mismatch: {name}"


# --- Exact lengths -----------------------------------------------------------------------------


def test_total_length_matches_timeline_duration() -> None:
    project, graph = graph_for()
    timeline = project.timelines[0]
    assert graph.total_frames == round(timeline.duration_us * 25 / S) == 169
    assert graph.total_samples == round(timeline.duration_us * 48_000 / S)


def test_crossfade_handles_and_joins() -> None:
    _, graph = graph_for()
    first, second, _, card = graph.items
    assert isinstance(first, ClipNode) and isinstance(card, EndCardNode)
    # 0.4 s crossfade = 5 frames on each side of the cut.
    assert [join.crossfade_frames for join in graph.joins] == [10, 0, 10]
    # Core lengths come from cumulative boundaries: 0-62.5 → 62 frames, 62.5-93.75 → 32 frames.
    assert first.frames == 62 + 5
    assert second.frames == 32 + 5
    assert card.frames == 50 + 5
    assert graph.inputs[0].start_s == 1.0 and graph.inputs[0].duration_s == pytest.approx(2.7)
    # The 2x segment needs 0.2 s of output = 0.4 s of source before its in point.
    assert graph.inputs[1].start_s == pytest.approx(6.1)


def test_dips_and_flashes_are_fades_without_handles() -> None:
    dip = Transition(kind=TransitionKind.DIP_TO_WHITE, duration_us=400_000)
    flash = Transition(kind=TransitionKind.FLASH, duration_us=200_000)
    _, graph = graph_for(transitions=(dip, flash, Transition()))
    first, second, third, _ = graph.items
    assert isinstance(first, ClipNode) and isinstance(second, ClipNode)
    assert isinstance(third, ClipNode)
    assert first.fade_out is not None and first.fade_out.color == "white"
    assert second.fade_in is not None and second.fade_in.frames == 5
    assert third.fade_in is not None and not third.fade_in.audio
    assert all(join.crossfade_frames == 0 for join in graph.joins)
    assert graph.inputs[0].duration_s == pytest.approx(2.5)


def test_without_end_card_last_crossfade_is_ignored() -> None:
    _, graph = graph_for(end_card=False)
    assert len(graph.items) == 3
    assert graph.inputs[2].duration_s == pytest.approx(1.0)


# --- Reframing ---------------------------------------------------------------------------------


def test_plan_reframe() -> None:
    project, _ = graph_for()
    clip = project.clip("clip")
    segment = Segment(id="s", clip_id="clip", in_us=0, out_us=S)
    wide, tall, feed = (project.settings.preset(i) for i in ("wide", "tall", "feed"))
    assert plan_reframe(clip, segment, wide, 0, None).kind is ReframeKind.SCALE
    crop = plan_reframe(clip, segment, tall, 0, None)
    assert (crop.kind, crop.crop_axis, crop.position) == (ReframeKind.CROP, "x", 0.5)
    left = segment.model_copy(
        update={"reframe": segment.reframe.model_copy(update={"center_x": 0})}
    )
    assert plan_reframe(clip, left, tall, 0, None).position == 0.0
    assert plan_reframe(clip, segment, feed, 0, None).kind is ReframeKind.BLUR


def test_follow_crop_keyframes_are_in_segment_output_time() -> None:
    from easy_video_editor.core.model import ReframeMode

    project, _ = graph_for()
    clip = project.clip("clip")
    segment = Segment(id="s", clip_id="clip", in_us=2 * S, out_us=4 * S, speed=2.0)
    tall = project.settings.preset("tall").model_copy(
        update={"reframe_mode": ReframeMode.FOLLOW_CROP}
    )
    plan = plan_reframe(clip, segment, tall, 2 * S, [(2 * S, 0.5), (4 * S, 1.0)])
    assert plan.keyframes == ((0.0, 0.5), (1.0, 1.0))


def test_follow_crop_expression() -> None:
    from easy_video_editor.media.render.ffmpeg import _position_expr
    from easy_video_editor.media.render.graph import Reframe

    reframe = Reframe(ReframeKind.CROP, keyframes=((0.0, 0.2), (1.0, 0.6)))
    assert _position_expr(reframe) == "if(lt(t,0),0.2,if(lt(t,1),0.2+(0.4)*(t-0)/1,0.6))"


# --- Text, logo, music -------------------------------------------------------------------------


def test_text_size_fits_width() -> None:
    assert fitted_font_size("V-Core", 0.10, 1920, 1080) == 108
    assert fitted_font_size("V-Core", 0.10, 1080, 1920) == 108
    assert fitted_font_size("A much longer title for a vertical video", 0.10, 1080, 1920) < 60


def test_texts_are_written_to_files_not_the_filter() -> None:
    _, graph = graph_for()
    assert graph.files["text0.txt"] == "Wishlist now"
    assert graph.files["text1.txt"] == "V-Core"
    script = filter_script(graph)
    assert "Wishlist" not in script and "textfile=text1.txt" in script


def test_unknown_text_style() -> None:
    from easy_video_editor.core.model import TextOverlay

    project, _ = graph_for()
    timeline = project.timelines[0]
    timeline.texts.append(TextOverlay(id="x", text="Hi", start_us=0, end_us=S, style="fancy"))
    with pytest.raises(RenderError, match="Unknown text style"):
        build_graph(project, timeline, project.settings.preset("wide"), RenderOptions())


def test_brand_kit_adds_logo_and_end_card_assets() -> None:
    project, _ = graph_for()
    graph = build_graph(
        project, project.timelines[0], project.settings.preset("wide"), RenderOptions(brand=BRAND)
    )
    card = graph.items[-1]
    assert isinstance(card, EndCardNode) and card.logo_input is not None
    assert graph.logo is not None and graph.logo.end_s == pytest.approx(4.76)
    assert "overlay=W-w-5:5:enable='lt(t,4.76)'" in filter_script(graph)


def test_music_is_trimmed_to_the_timeline() -> None:
    _, graph = graph_for()
    (cue,) = graph.music
    assert cue.length_samples == graph.total_samples
    assert cue.fade_out_samples == 48_000
    project = build_project()
    timeline = project.timelines[0]
    graph = build_graph(
        project, timeline, project.settings.preset("wide"),
        RenderOptions(music_durations_us={str(MEDIA / "music.mp3"): 3 * S}),
    )  # fmt: skip
    assert graph.music[0].length_samples == 3 * 48_000


def test_atempo_chain() -> None:
    assert atempo_chain(1.0) == []
    assert atempo_chain(1.5) == ["atempo=1.5"]
    assert atempo_chain(5.0) == ["atempo=2", "atempo=2", "atempo=1.25"]
    assert atempo_chain(0.2) == ["atempo=0.5", "atempo=0.5", "atempo=0.8"]


# --- ffmpeg command ----------------------------------------------------------------------------


def test_golden_filter_and_arguments() -> None:
    _, graph = graph_for()
    check_golden("wide_filter.txt", filter_script(graph))
    args = render_args(graph, Path("ffmpeg"), "graph.txt", Path("out.mp4"), EncodeSettings(), 7)
    normalised = [arg.replace(str(MEDIA), "<MEDIA>").replace("\\", "/") for arg in args]
    check_golden("wide_args.json", json.dumps(normalised, indent=1) + "\n")
    check_golden("wide_audio_filter.txt", filter_script(graph, video=False))


def test_golden_vertical_and_blurred() -> None:
    check_golden("tall_filter.txt", filter_script(graph_for("tall")[1]))
    check_golden("feed_filter.txt", filter_script(graph_for("feed")[1]))


def test_filter_script_option_depends_on_version() -> None:
    assert filter_script_option(6) == "-filter_complex_script"
    assert filter_script_option(7) == "-/filter_complex"
    assert parse_ffmpeg_major("ffmpeg version 6.1.1-3ubuntu5 Copyright") == 6
    assert parse_ffmpeg_major("ffmpeg version n7.1 Copyright") == 7
    assert parse_ffmpeg_major("ffmpeg version 8.0-full_build-www.gyan.dev") == 8
    assert parse_ffmpeg_major("ffmpeg version 2025-01-01-git-abc") is None


def test_parse_loudnorm() -> None:
    stderr = """[Parsed_loudnorm_0 @ 0x1]
{
	"input_i" : "-20.31",
	"input_tp" : "-3.10",
	"input_lra" : "1.20",
	"input_thresh" : "-30.40",
	"output_i" : "-14.00",
	"target_offset" : "0.12"
}"""
    assert parse_loudnorm(stderr) == {
        "measured_I": "-20.31", "measured_TP": "-3.10", "measured_LRA": "1.20",
        "measured_thresh": "-30.40", "offset": "0.12",
    }  # fmt: skip
    assert parse_loudnorm(stderr.replace('"-20.31"', '"-inf"')) is None
    assert parse_loudnorm("no json here") is None


# --- Text looks --------------------------------------------------------------------------------


@pytest.mark.parametrize("look", ["band", "bold"])
def test_golden_text_looks(look: str) -> None:
    from easy_video_editor.core.model import TextLook

    project = build_project()
    project.settings.text_look = TextLook(look)
    project.timelines[0].texts[0].style = "caption"
    graph = build_graph(
        project, project.timelines[0], project.settings.preset("wide"), RenderOptions(brand=BRAND)
    )
    check_golden(f"wide_{look}_filter.txt", filter_script(graph))


def test_every_look_defines_every_style() -> None:
    from easy_video_editor.core.model import TextLook
    from easy_video_editor.media.render.styles import STYLE_NAMES, TEXT_LOOKS

    assert set(TEXT_LOOKS) == set(TextLook)
    assert all(set(styles) == set(STYLE_NAMES) for styles in TEXT_LOOKS.values())


def test_resolve_color() -> None:
    from easy_video_editor.media.render.styles import resolve_color

    assert resolve_color("primary", "#112233") == "#112233"
    assert resolve_color("primary@0.5", "#112233") == "#112233@0.5"
    assert resolve_color("#000000@0.6", "#112233") == "#000000@0.6"


def test_text_look_setting_is_validated() -> None:
    from easy_video_editor.core.commands import UpdateSettings
    from easy_video_editor.core.errors import CommandError
    from easy_video_editor.core.model import Origin, Project
    from easy_video_editor.core.session import ProjectSession

    session = ProjectSession(Project(name="P"))
    assert session.project.settings.text_look == "classic"
    session.execute(UpdateSettings(changes={"text_look": "bold"}), Origin.USER)
    with pytest.raises(CommandError):
        session.execute(UpdateSettings(changes={"text_look": "fancy"}), Origin.USER)
