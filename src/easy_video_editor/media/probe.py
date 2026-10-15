"""Probing media files with ffprobe."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from easy_video_editor.core.progress import CancellationToken
from easy_video_editor.core.timecode import seconds_to_us
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.errors import MediaError
from easy_video_editor.media.process import run


@dataclass(frozen=True)
class ProbeResult:
    path: Path
    duration_us: int
    has_video: bool
    has_audio: bool
    width: int | None = None
    """Display width (rotation applied)."""
    height: int | None = None
    frame_rate: Fraction | None = None
    """Average frame rate."""
    variable_frame_rate: bool = False
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    rotation: int = 0


def parse_rate(value: object) -> Fraction | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        rate = Fraction(int(numerator), int(denominator))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _rotation(stream: dict[str, Any]) -> int:
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return int(float(side_data["rotation"])) % 360
    rotate = stream.get("tags", {}).get("rotate")
    return int(rotate) % 360 if isinstance(rotate, str) and rotate.lstrip("-").isdigit() else 0


def parse_probe(path: Path, document: dict[str, Any]) -> ProbeResult:
    streams: list[dict[str, Any]] = document.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic", 0)
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration_text = document.get("format", {}).get("duration") or (video or {}).get("duration")
    try:
        duration_us = seconds_to_us(float(str(duration_text)))
    except ValueError:
        duration_us = 0
    if duration_us <= 0:
        raise MediaError(f"Cannot determine the duration of {path.name}.")
    if video is None and audio is None:
        raise MediaError(f"{path.name} contains no audio or video.")

    width = height = None
    frame_rate = None
    variable = False
    rotation = 0
    if video is not None:
        width, height = video.get("width"), video.get("height")
        rotation = _rotation(video)
        if rotation in (90, 270) and width and height:
            width, height = height, width
        frame_rate = parse_rate(video.get("avg_frame_rate")) or parse_rate(
            video.get("r_frame_rate")
        )
        real_rate = parse_rate(video.get("r_frame_rate"))
        variable = frame_rate is not None and real_rate is not None and real_rate != frame_rate
    sample_rate = (audio or {}).get("sample_rate")
    return ProbeResult(
        path=path,
        duration_us=duration_us,
        has_video=video is not None,
        has_audio=audio is not None,
        width=width,
        height=height,
        frame_rate=frame_rate,
        variable_frame_rate=variable,
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        audio_sample_rate=int(sample_rate) if isinstance(sample_rate, str) else None,
        rotation=rotation,
    )


def probe(path: Path, toolchain: Toolchain, token: CancellationToken | None = None) -> ProbeResult:
    if not path.is_file():
        raise MediaError(f"File not found: {path}")
    result = run(
        [
            toolchain.ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ],
        token=token,
    )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MediaError(f"ffprobe returned invalid output for {path.name}.") from error
    return parse_probe(path, document)
