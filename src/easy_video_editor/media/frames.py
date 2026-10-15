"""Single still frames as JPEG for AI vision (SPEC §5.1: only downscaled frames leave the PC)."""

from __future__ import annotations

from pathlib import Path

from easy_video_editor.core.progress import CancellationToken
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.process import run


def extract_jpeg(
    source: Path,
    time_s: float,
    width: int,
    toolchain: Toolchain,
    token: CancellationToken | None = None,
) -> bytes:
    """The frame shown at ``time_s``, scaled to ``width`` pixels, as JPEG bytes."""
    chunks: list[bytes] = []
    run(
        [
            toolchain.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{max(time_s, 0.0):.3f}",
            "-i",
            source,
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "5",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ],
        token=token,
        on_chunk=chunks.append,
    )
    return b"".join(chunks)
