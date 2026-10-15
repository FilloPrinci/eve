from pathlib import Path

import pytest

from easy_video_editor.core.model import EditTimeline, Segment
from easy_video_editor.media.preview import (
    EdlEntry,
    edl_entries,
    edl_time_to_output_us,
    edl_url,
    output_us_to_edl_time,
)

from .render_fixtures import build_project

S = 1_000_000


def entries() -> list[EdlEntry]:
    project = build_project()
    return edl_entries(project, project.timelines[0], lambda clip: Path("/p/a,b;c.mp4"))


def test_edl_url_length_prefixes_paths() -> None:
    path = str(Path("/p/a,b;c.mp4"))
    prefix = f"%{len(path.encode())}%{path}"
    url = edl_url(entries())
    assert url.startswith(f"edl://{prefix},1,2.5;")
    assert url.count(prefix) == 3


def test_edl_maps_source_lengths_to_output_time() -> None:
    items = entries()
    # Second segment: 2.5 s of source at 2x = 1.25 s of output, starting at output 2.5 s.
    assert edl_time_to_output_us(items, 2.5 + 1.25) == 2_500_000 + 625_000
    assert output_us_to_edl_time(items, 2_500_000 + 625_000) == pytest.approx(3.75)
    assert (
        edl_time_to_output_us(items, 999) == items[-1].output_start_us + items[-1].output_length_us
    )
    assert edl_time_to_output_us([], 1.0) == 0


def test_empty_timeline_has_no_entries() -> None:
    project = build_project()
    empty = EditTimeline(id="e", name="e", aspect="16:9")
    assert edl_entries(project, empty, lambda clip: clip.file_path) == []
    one = EditTimeline(
        id="o",
        name="o",
        aspect="16:9",
        segments=[Segment(id="s", clip_id="clip", in_us=0, out_us=S)],
    )
    assert edl_entries(project, one, lambda clip: clip.file_path)[0].output_length_us == S
