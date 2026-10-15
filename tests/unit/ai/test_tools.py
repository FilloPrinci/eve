"""The AI tool registry (SPEC §7.5): phases, red zones, locks and argument checking."""

import json

import pytest

from easy_video_editor.ai.tools import (
    REGISTRY,
    ToolContext,
    ToolError,
    ToolPhase,
    call_tool,
    tools_for,
)
from easy_video_editor.core.model import Origin, ZoneKind

from .conftest import S


def names(phase: ToolPhase) -> set[str]:
    return {spec.name for spec in tools_for(phase)}


def test_no_tool_can_touch_red_zones() -> None:
    for spec in REGISTRY.values():
        assert "exclude" not in spec.name and "red" not in spec.name.split("_")
        schema = json.dumps(spec.input_schema())
        if spec.mutating:
            assert '"exclude"' not in schema, spec.name


def test_each_phase_only_exposes_its_own_editing_tools() -> None:
    analyze, refine, assemble = (
        names(ToolPhase.ANALYZE),
        names(ToolPhase.REFINE),
        names(ToolPhase.ASSEMBLE),
    )
    assert "create_candidate_zone" in analyze and "create_highlight_zone" not in analyze
    assert "create_highlight_zone" in refine and "create_candidate_zone" not in refine
    assert "add_segment" in assemble and "create_highlight_zone" not in assemble
    assert analyze | refine | assemble == names(ToolPhase.FOLLOWUP)
    assert {"get_project_state", "get_frames"} <= analyze & refine & assemble


def test_schemas_forbid_unknown_arguments(ctx: ToolContext) -> None:
    for spec in REGISTRY.values():
        assert spec.input_schema()["additionalProperties"] is False
    with pytest.raises(ToolError, match="Invalid arguments"):
        call_tool(ctx, ToolPhase.FOLLOWUP, "get_zones", {"colour": "red"})


def test_tools_outside_the_phase_are_refused(ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="not available in the analyze phase"):
        call_tool(ctx, ToolPhase.ANALYZE, "create_timeline", {"aspect": "9:16"})
    with pytest.raises(ToolError, match="not available"):
        call_tool(ctx, ToolPhase.FOLLOWUP, "create_exclude_zone", {})


def test_candidate_zones_never_cover_red(ctx: ToolContext) -> None:
    before = ctx.project.model_copy(deep=True)
    with pytest.raises(ToolError):
        call_tool(
            ctx,
            ToolPhase.ANALYZE,
            "create_candidate_zone",
            {"clip_id": "clip", "start_s": 18, "end_s": 25, "label": "Over the red zone"},
        )
    assert ctx.project.zones == before.zones

    result = call_tool(
        ctx,
        ToolPhase.ANALYZE,
        "create_candidate_zone",
        {"clip_id": "clip", "start_s": 5, "end_s": 10.01, "label": "Start", "score": 0.7},
    )
    created = [z for z in ctx.project.zones if z.origin is Origin.AI]
    assert len(created) == 1 and created[0].id in result.text
    assert created[0].kind is ZoneKind.CANDIDATE and created[0].label == "Start"
    assert created[0].end_us == 10 * S  # snapped to a frame of the 30 fps clip


def test_user_zones_are_locked_for_the_ai(ctx: ToolContext) -> None:
    for name, args in (
        ("update_candidate_zone", {"zone_id": "user_blue", "end_s": 55}),
        ("delete_candidate_zone", {"zone_id": "user_blue"}),
    ):
        with pytest.raises(ToolError):
            call_tool(ctx, ToolPhase.ANALYZE, name, args)
    assert ctx.project.zone("user_blue").end_us == 50 * S


def test_read_tools_describe_the_project(ctx: ToolContext) -> None:
    state = json.loads(call_tool(ctx, ToolPhase.ANALYZE, "get_project_state", {}).text)
    assert state["clips"][0]["id"] == "clip"
    zones = json.loads(call_tool(ctx, ToolPhase.ANALYZE, "get_zones", {"kind": "exclude"}).text)
    assert [(z["start_s"], z["end_s"]) for z in zones] == [(20.0, 22.0)]


def test_frames_need_ffmpeg(ctx: ToolContext) -> None:
    with pytest.raises(ToolError, match="not available"):
        call_tool(ctx, ToolPhase.ANALYZE, "get_frames", {"clip_id": "clip", "times_s": [1.0]})
    with pytest.raises(ToolError, match="Invalid arguments"):
        call_tool(ctx, ToolPhase.ANALYZE, "get_frames", {"clip_id": "clip", "times_s": [1.0] * 7})
