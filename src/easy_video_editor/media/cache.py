"""Locations and status of generated media inside a project folder (SPEC §7.2).

Generated files are keyed by the clip's content key, so they survive moving or relinking the
source file and are shared by clips that point to the same content.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from easy_video_editor.core.model import Clip
from easy_video_editor.media.fingerprint import Fingerprint

DEFAULT_PROXY_HEIGHT = 540


def content_key(clip: Clip) -> str:
    if clip.fingerprint:
        try:
            return Fingerprint.parse(clip.fingerprint).content_key
        except ValueError:
            pass
    return hashlib.sha256(clip.path.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class MediaStatus:
    proxy_ready: bool
    thumbnails_ready: bool
    waveform_ready: bool


class MediaCache:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    @property
    def proxies_dir(self) -> Path:
        return self.project_dir / "proxies"

    @property
    def thumbs_dir(self) -> Path:
        return self.project_dir / "thumbs"

    def proxy_path(self, clip: Clip, height: int = DEFAULT_PROXY_HEIGHT) -> Path:
        return self.proxies_dir / f"{content_key(clip)}_{height}p.mp4"

    def strip_path(self, clip: Clip) -> Path:
        return self.thumbs_dir / f"{content_key(clip)}_strip.jpg"

    def strip_info_path(self, clip: Clip) -> Path:
        return self.thumbs_dir / f"{content_key(clip)}_strip.json"

    def waveform_path(self, clip: Clip) -> Path:
        return self.thumbs_dir / f"{content_key(clip)}_wave.npy"

    def status(self, clip: Clip, proxy_height: int = DEFAULT_PROXY_HEIGHT) -> MediaStatus:
        return MediaStatus(
            proxy_ready=self.proxy_path(clip, proxy_height).is_file(),
            thumbnails_ready=self.strip_path(clip).is_file()
            and self.strip_info_path(clip).is_file(),
            waveform_ready=not clip.has_audio or self.waveform_path(clip).is_file(),
        )
