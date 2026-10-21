"""Render an edit timeline to a video file (final or draft)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.model import (
    Clip,
    EditTimeline,
    EndCard,
    OutputPreset,
    Project,
    ProjectSettings,
    TextLook,
)
from easy_video_editor.core.progress import (
    CancellationToken,
    Progress,
    no_progress,
    sub_progress,
)
from easy_video_editor.core.validation import ValidationReport, validate_for_render
from easy_video_editor.media.binaries import Toolchain, ffmpeg_major_version
from easy_video_editor.media.cache import MediaCache
from easy_video_editor.media.probe import probe
from easy_video_editor.media.process import FfmpegProgress, run
from easy_video_editor.media.render.build import RenderError, RenderOptions, build_graph
from easy_video_editor.media.render.ffmpeg import (
    AUDIO_OUT,
    VIDEO_OUT,
    EncodeSettings,
    filter_script,
    filter_script_option,
    input_args,
    measure_args,
    num,
    render_args,
)
from easy_video_editor.media.render.graph import Loudness, RenderGraph

log = logging.getLogger(__name__)

DRAFT_SCALE = 1 / 3
SCRIPT_NAME = "graph.txt"
MEASURED_KEYS = {
    "input_i": "measured_I",
    "input_tp": "measured_TP",
    "input_lra": "measured_LRA",
    "input_thresh": "measured_thresh",
    "target_offset": "offset",
}


class ValidationFailed(RenderError):
    """Final validation refused the export (SPEC §4.4 rule 5)."""

    def __init__(self, report: ValidationReport) -> None:
        details = " ".join(issue.message for issue in report.errors)
        super().__init__(f"Export refused: {details}")
        self.report = report


def timeline_for(project: Project, preset: OutputPreset) -> str:
    for timeline in project.timelines:
        if timeline.aspect == preset.aspect:
            return timeline.id
    raise RenderError(f"No edit timeline for aspect ratio {preset.aspect} (preset {preset.name}).")


def parse_loudnorm(stderr: str) -> dict[str, str] | None:
    """Measured values from loudnorm's JSON output, or None for silence/unparseable output."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not blocks:
        return None
    values = json.loads(blocks[-1])
    if not all(key in values for key in MEASURED_KEYS):
        return None
    if any("inf" in str(values[key]) for key in MEASURED_KEYS):
        return None
    return {target: str(values[source]) for source, target in MEASURED_KEYS.items()}


def _prepare_workdir(graph: RenderGraph, workdir: Path, video: bool = True) -> None:
    workdir.mkdir(parents=True)
    for name, content in graph.files.items():
        if isinstance(content, Path):
            shutil.copyfile(content, workdir / name)
        else:
            (workdir / name).write_text(content, encoding="utf-8")
    (workdir / SCRIPT_NAME).write_text(filter_script(graph, video=video), encoding="utf-8")


def render_timeline(
    project: Project,
    project_dir: Path,
    preset: OutputPreset,
    output: Path,
    toolchain: Toolchain,
    *,
    options: RenderOptions | None = None,
    draft: bool = False,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
    file_exists: Callable[[Path], bool] = Path.is_file,
) -> Path:
    """Validate, then render the timeline matching ``preset`` to ``output``.

    Final renders use two-pass loudness normalisation; drafts are rendered from proxies at
    reduced size with single-pass normalisation. The output is written atomically.
    """
    # ffmpeg runs in a work folder, so every path it is given must be absolute.
    project_dir, output = project_dir.resolve(), output.resolve()
    timeline_id = timeline_for(project, preset)
    report = validate_for_render(project, timeline_id, preset, file_exists=file_exists)
    if not report.ok:
        raise ValidationFailed(report)
    for warning in report.warnings:
        log.warning("Render warning: %s", warning.message)

    options = options or RenderOptions()
    timeline = project.timeline(timeline_id)
    unknown = {
        cue.path
        for cue in timeline.music
        if cue.source_out_us is None and cue.path not in options.music_durations_us
    }
    if unknown:
        durations = dict(options.music_durations_us)
        durations.update(
            {path: probe(Path(path), toolchain, token).duration_us for path in unknown}
        )
        options = replace(options, music_durations_us=durations)
    if draft:
        cache = MediaCache(project_dir)

        def proxy_or_source(clip: Clip) -> Path:
            proxy = cache.proxy_path(clip)
            return proxy if proxy.is_file() else clip.file_path

        options = replace(options, media_path=proxy_or_source, scale=DRAFT_SCALE)
    graph = build_graph(project, timeline, preset, options)
    major = ffmpeg_major_version(toolchain.ffmpeg)
    encode = (
        EncodeSettings.draft()
        if draft
        else EncodeSettings(video_codec=preset.video_codec, audio_codec=preset.audio_codec)
    )
    workdir = project_dir / "cache" / "render" / uuid.uuid4().hex
    partial = output.with_name(f".{output.stem}.partial{output.suffix}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        encode_progress = progress
        if graph.loudness is not None and not draft:
            measure_dir = workdir / "measure"
            _prepare_workdir(graph, measure_dir, video=False)
            tracker = FfmpegProgress(
                round(graph.duration_s * 1_000_000),
                lambda f: progress(f * 0.15, "Measuring loudness"),
            )
            result = run(
                measure_args(graph, toolchain.ffmpeg, SCRIPT_NAME, major),
                token=token,
                on_line=tracker.feed,
                cwd=measure_dir,
            )
            measured = parse_loudnorm(result.stderr)
            graph.loudness = (
                Loudness(graph.loudness.integrated, measured=measured) if measured else None
            )
            encode_progress = sub_progress(progress, 0.15, 1.0)

        final_dir = workdir / "final"
        _prepare_workdir(graph, final_dir)
        message = "Rendering draft" if draft else "Rendering"
        tracker = FfmpegProgress(
            round(graph.duration_s * 1_000_000), lambda f: encode_progress(f, message)
        )
        run(
            render_args(graph, toolchain.ffmpeg, SCRIPT_NAME, partial.resolve(), encode, major),
            token=token,
            on_line=tracker.feed,
            cwd=final_dir,
        )
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
    progress(1.0, "Done")
    return output


def render_end_card_still(
    brand: BrandKit | None,
    preset: OutputPreset,
    output: Path,
    toolchain: Toolchain,
    *,
    end_card: EndCard | None = None,
    text_look: TextLook = TextLook.CLASSIC,
    scale: float = 1.0,
    token: CancellationToken | None = None,
) -> Path:
    """Render one frame from the middle of an end card to a PNG ``output``.

    Uses the same graph as a real render, so the preview matches the exported video.
    """
    card = (end_card or EndCard()).model_copy(update={"enabled": True})
    timeline = EditTimeline(id="end_card", name="End card", aspect=preset.aspect, end_card=card)
    project = Project(
        name="End card preview",
        timelines=[timeline],
        settings=ProjectSettings(text_look=text_look),
    )
    options = RenderOptions(brand=brand, scale=scale, loudness=False, duck_game_audio=False)
    graph = build_graph(project, timeline, preset, options)
    output = output.resolve()
    # Texts fade in at the start of the card; the middle shows the steady state.
    middle = num(graph.duration_s / 2)
    with tempfile.TemporaryDirectory(prefix="eve-end-card-") as folder:
        workdir = Path(folder) / "still"
        _prepare_workdir(graph, workdir)
        run(
            [
                str(toolchain.ffmpeg), "-hide_banner", "-nostdin", "-y",
                *input_args(graph),
                filter_script_option(ffmpeg_major_version(toolchain.ffmpeg)), SCRIPT_NAME,
                "-map", f"[{VIDEO_OUT}]", "-ss", middle, "-frames:v", "1", "-update", "1",
                str(output),
                "-map", f"[{AUDIO_OUT}]", "-f", "null", "-",
            ],
            token=token,
            cwd=workdir,
        )  # fmt: skip
    return output
