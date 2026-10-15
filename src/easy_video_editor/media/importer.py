"""Importing clips: probe, fingerprint and generate proxies, thumbnails and waveforms."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from easy_video_editor.core.commands import AddClip
from easy_video_editor.core.model import Clip, FrameRate
from easy_video_editor.core.progress import (
    CancellationToken,
    Progress,
    no_progress,
    sub_progress,
)
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import DEFAULT_PROXY_HEIGHT, MediaCache
from easy_video_editor.media.errors import MediaError
from easy_video_editor.media.fingerprint import fingerprint
from easy_video_editor.media.generate import make_proxy, make_thumbnail_strip, make_waveform
from easy_video_editor.media.probe import ProbeResult, probe


def add_clip_command(
    path: Path, toolchain: Toolchain, token: CancellationToken | None = None
) -> tuple[AddClip, ProbeResult]:
    """Probe and fingerprint ``path`` and build the command that adds it to a project."""
    path = path.resolve()
    info = probe(path, toolchain, token)
    if not info.has_video:
        raise MediaError(f"{path.name} has no video stream.")
    rate = info.frame_rate
    command = AddClip(
        path=str(path),
        duration_us=info.duration_us,
        frame_rate=FrameRate(num=rate.numerator, den=rate.denominator) if rate else None,
        width=info.width,
        height=info.height,
        has_audio=info.has_audio,
        fingerprint=str(fingerprint(path)),
    )
    return command, info


@dataclass(frozen=True)
class PreparedMedia:
    proxy: Path
    strip: Path
    waveform: Path | None


def prepare_clip_media(
    clip: Clip,
    cache: MediaCache,
    toolchain: Toolchain,
    progress: Progress = no_progress,
    token: CancellationToken | None = None,
    proxy_height: int = DEFAULT_PROXY_HEIGHT,
    force: bool = False,
) -> PreparedMedia:
    """Generate whatever is missing among proxy, thumbnail strip and waveform."""
    cache.proxies_dir.mkdir(parents=True, exist_ok=True)
    cache.thumbs_dir.mkdir(parents=True, exist_ok=True)
    proxy = cache.proxy_path(clip, proxy_height)
    if force or not proxy.is_file():
        make_proxy(clip, proxy, toolchain, proxy_height, sub_progress(progress, 0.0, 0.8), token)
    strip, info = cache.strip_path(clip), cache.strip_info_path(clip)
    if force or not (strip.is_file() and info.is_file()):
        make_thumbnail_strip(
            clip, proxy, strip, info, toolchain, sub_progress(progress, 0.8, 0.9), token
        )
    waveform = None
    if clip.has_audio:
        waveform = cache.waveform_path(clip)
        if force or not waveform.is_file():
            make_waveform(clip, proxy, waveform, toolchain, sub_progress(progress, 0.9, 1.0), token)
    progress(1.0, "Media ready")
    return PreparedMedia(proxy, strip, waveform)
