"""Real renders of synthetic projects (SPEC §15): durations, streams, frame provenance."""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from easy_video_editor.core.persistence import create_project
from easy_video_editor.core.progress import CancellationToken, CancelledError
from easy_video_editor.core.validation import Severity
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.render.build import RenderOptions
from easy_video_editor.media.render.renderer import ValidationFailed, render_timeline
from tests.unit.media.render_fixtures import brand, build_project

from .conftest import CODED_FPS, decode_frame_indexes

RED_FRAMES = range(4 * CODED_FPS, 6 * CODED_FPS)


def stream_info(path: Path) -> dict[str, dict[str, Any]]:
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,nb_frames,r_frame_rate", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return {s["codec_type"]: s for s in json.loads(output)["streams"]}


def integrated_loudness(path: Path) -> float:
    stderr = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null",
         "-"],
        check=True, capture_output=True, text=True,
    ).stderr  # fmt: skip
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", stderr)[-1])


@pytest.fixture
def project_dir(tmp_path: Path, media_dir: Path) -> Path:
    directory = tmp_path / "Golden.eve"
    create_project(directory, build_project(media=media_dir))
    return directory


def render(project_dir: Path, media_dir: Path, toolchain: Toolchain, preset_id: str,
           **kwargs: object) -> Path:  # fmt: skip
    project = build_project(media=media_dir)
    preset = project.settings.preset(preset_id)
    output = project_dir / "exports" / f"{preset_id}.mp4"
    return render_timeline(project, project_dir, preset, output, toolchain, **kwargs)  # type: ignore[arg-type]


def expected_source_frame(output_frame: int) -> int | None:
    """Source frame shown at an output frame of the 16:9 timeline, outside transitions."""
    fps = 25
    # (first core output frame, last core output frame (exclusive), source in (s), speed)
    segments = [(0, 62, 1.0, 1.0), (62, 94, 6.5, 2.0), (94, 119, 10.0, 1.0)]
    for start, end, source_in, speed in segments:
        if start <= output_frame < end:
            seconds = source_in + (output_frame - start) / fps * speed
            return int(seconds * CODED_FPS + 1e-6)
    return None


def test_final_render_wide(project_dir: Path, media_dir: Path, toolchain: Toolchain) -> None:
    output = render(project_dir, media_dir, toolchain, "wide")
    info = stream_info(output)
    assert (info["video"]["width"], info["video"]["height"]) == (320, 180)
    assert info["video"]["r_frame_rate"] == "25/1"
    assert int(info["video"]["nb_frames"]) == 169
    assert "audio" in info

    indexes = decode_frame_indexes(output)
    assert not [n for n in indexes if n is not None and n in RED_FRAMES]
    # Transitions: crossfade 57-66, dip 89-98, crossfade into the end card 114-123.
    transitions = set(range(57, 67)) | set(range(89, 99)) | set(range(114, 169))
    checked = 0
    for frame, decoded in enumerate(indexes):
        expected = expected_source_frame(frame)
        if frame in transitions or expected is None:
            continue
        assert decoded is not None and abs(decoded - expected) <= 1, (frame, decoded, expected)
        checked += 1
    assert checked > 80

    assert integrated_loudness(output) == pytest.approx(-14.0, abs=1.5)
    assert list((project_dir / "cache" / "render").iterdir()) == []


@pytest.mark.parametrize(("preset_id", "size"), [("tall", (180, 320)), ("feed", (144, 180))])
def test_reframed_renders(
    project_dir: Path, media_dir: Path, toolchain: Toolchain, preset_id: str,
    size: tuple[int, int],
) -> None:  # fmt: skip
    output = render(project_dir, media_dir, toolchain, preset_id)
    info = stream_info(output)
    assert (info["video"]["width"], info["video"]["height"]) == size
    assert int(info["video"]["nb_frames"]) == 169
    if preset_id == "tall":  # a centre crop keeps the full height, so bands stay readable
        indexes = decode_frame_indexes(output)
        assert sum(n is not None for n in indexes) > 60
        assert not [n for n in indexes if n is not None and n in RED_FRAMES]


def test_draft_render_with_brand(project_dir: Path, media_dir: Path, toolchain: Toolchain) -> None:
    output = render(
        project_dir, media_dir, toolchain, "wide", draft=True,
        options=RenderOptions(brand=brand(media_dir)),
    )  # fmt: skip
    info = stream_info(output)
    assert (info["video"]["width"], info["video"]["height"]) == (106, 60)
    assert int(info["video"]["nb_frames"]) == 169


def test_validation_refuses_export(
    project_dir: Path, media_dir: Path, toolchain: Toolchain
) -> None:
    with pytest.raises(ValidationFailed) as error:
        render(project_dir, media_dir, toolchain, "wide", file_exists=lambda path: False)
    assert {issue.code for issue in error.value.report.errors} >= {"missing_media"}
    assert all(issue.severity is Severity.ERROR for issue in error.value.report.errors)
    assert not (project_dir / "exports" / "wide.mp4").exists()


def test_cancelled_render_leaves_nothing(
    project_dir: Path, media_dir: Path, toolchain: Toolchain
) -> None:
    token = CancellationToken()

    def cancel_on_progress(fraction: float | None, message: str = "") -> None:
        if fraction:
            token.cancel()

    with pytest.raises(CancelledError):
        render(project_dir, media_dir, toolchain, "wide", progress=cancel_on_progress, token=token)
    assert list((project_dir / "exports").iterdir()) == []
    assert list((project_dir / "cache" / "render").iterdir()) == []


def test_cli_validate_and_render(project_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from easy_video_editor.cli.main import EXIT_ERROR, EXIT_OK, main

    assert main(["validate", str(project_dir)]) == EXIT_OK
    assert "Wide: OK" in capsys.readouterr().out
    assert main(["render", str(project_dir), "--preset", "wide", "--draft"]) == EXIT_OK
    assert (project_dir / "exports" / "Golden-wide-draft.mp4").is_file()
    assert main(["render", str(project_dir), "--preset", "nope"]) == EXIT_ERROR


def test_cli_render_reports_validation_errors(
    project_dir: Path, media_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from easy_video_editor.cli.main import EXIT_ERROR, main
    from easy_video_editor.core.persistence import save_project

    project = build_project(media=tmp_path / "offline")
    save_project(project, project_dir)
    assert main(["validate", str(project_dir), "--preset", "wide"]) == EXIT_ERROR
    assert main(["render", str(project_dir), "--preset", "wide"]) == EXIT_ERROR
    assert "missing" in capsys.readouterr().err


@pytest.mark.parametrize("look", ["classic", "band", "bold"])
def test_every_text_look_renders(
    project_dir: Path, media_dir: Path, toolchain: Toolchain, look: str
) -> None:
    from easy_video_editor.core.model import TextLook

    project = build_project(media=media_dir)
    project.settings.text_look = TextLook(look)
    project.timelines[0].texts[0].style = "caption"
    output = project_dir / "exports" / f"{look}.mp4"
    render_timeline(
        project, project_dir, project.settings.preset("wide"), output, toolchain, draft=True,
        options=RenderOptions(brand=brand(media_dir)),
    )  # fmt: skip
    assert int(stream_info(output)["video"]["nb_frames"]) == 169
