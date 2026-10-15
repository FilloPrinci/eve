from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from easy_video_editor.core.model import Clip, Origin, Project
from easy_video_editor.core.progress import CancellationToken, CancelledError
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache
from easy_video_editor.media.errors import MediaError
from easy_video_editor.media.generate import StripInfo, make_proxy
from easy_video_editor.media.importer import add_clip_command, prepare_clip_media
from easy_video_editor.media.probe import probe


def import_clip(path: Path, toolchain: Toolchain) -> Clip:
    session = ProjectSession(Project(name="Test"))
    command, _ = add_clip_command(path, toolchain)
    clip_id = session.execute(command, Origin.USER).created_ids[0]
    return session.project.clip(clip_id)


def test_probe(media_dir: Path, toolchain: Toolchain) -> None:
    info = probe(media_dir / "clip.mp4", toolchain)
    assert abs(info.duration_us - 4_000_000) < 100_000
    assert (info.width, info.height, info.frame_rate) == (320, 240, Fraction(25))
    assert info.has_video and info.has_audio
    assert not probe(media_dir / "silent.mp4", toolchain).has_audio


def test_probe_rejects_non_media(tmp_path: Path, toolchain: Toolchain) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(MediaError):
        probe(junk, toolchain)
    with pytest.raises(MediaError, match="not found"):
        probe(tmp_path / "missing.mp4", toolchain)


def test_prepare_media(media_dir: Path, toolchain: Toolchain, tmp_path: Path) -> None:
    clip = import_clip(media_dir / "clip.mp4", toolchain)
    cache = MediaCache(tmp_path)
    reports: list[float | None] = []
    prepared = prepare_clip_media(clip, cache, toolchain, lambda f, m="": reports.append(f))

    proxy = probe(prepared.proxy, toolchain)
    assert (proxy.height, proxy.frame_rate, proxy.variable_frame_rate) == (240, Fraction(25), False)
    assert abs(proxy.duration_us - clip.duration_us) < 100_000

    info = StripInfo.load(cache.strip_info_path(clip))
    assert (info.count, info.thumb_width, info.thumb_height) == (8, 120, 90)

    assert prepared.waveform is not None
    peaks = np.load(prepared.waveform)
    assert peaks.shape[1] == 2 and abs(peaks.shape[0] - 400) <= 5
    assert 0.05 < float(peaks[:, 1].max()) < 0.5  # default sine amplitude is 1/8

    assert reports[-1] == 1.0
    assert all(r is not None and 0.0 <= r <= 1.0 for r in reports)
    assert cache.status(clip).proxy_ready and cache.status(clip).thumbnails_ready

    before = prepared.proxy.stat().st_mtime_ns
    prepare_clip_media(clip, cache, toolchain)
    assert prepared.proxy.stat().st_mtime_ns == before  # nothing regenerated


def test_silent_clip_has_no_waveform(media_dir: Path, toolchain: Toolchain, tmp_path: Path) -> None:
    clip = import_clip(media_dir / "silent.mp4", toolchain)
    prepared = prepare_clip_media(clip, MediaCache(tmp_path), toolchain)
    assert prepared.waveform is None
    assert MediaCache(tmp_path).status(clip).waveform_ready


def test_cancelled_proxy_leaves_no_file(
    media_dir: Path, toolchain: Toolchain, tmp_path: Path
) -> None:
    clip = import_clip(media_dir / "long.mp4", toolchain)
    token = CancellationToken()
    output = tmp_path / "proxy.mp4"
    with pytest.raises(CancelledError):
        make_proxy(clip, output, toolchain, 540, lambda f, m="": token.cancel(), token)
    assert list(tmp_path.iterdir()) == []
