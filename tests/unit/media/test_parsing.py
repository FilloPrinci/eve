"""Pure functions of the media layer (no ffmpeg needed)."""

import struct
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from easy_video_editor.core.model import Clip, FrameRate
from easy_video_editor.media.binaries import Toolchain, find_executable
from easy_video_editor.media.cache import MediaCache, content_key
from easy_video_editor.media.doctor import fix_for
from easy_video_editor.media.errors import MediaError, MissingBinaryError
from easy_video_editor.media.generate import (
    PeakAccumulator,
    proxy_command,
    proxy_frame_rate,
    strip_layout,
)
from easy_video_editor.media.probe import parse_probe, parse_rate
from easy_video_editor.media.process import FfmpegProgress

S = 1_000_000


def video_stream(**extra: object) -> dict[str, object]:
    return {
        "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
        "avg_frame_rate": "60/1", "r_frame_rate": "60/1", **extra,
    }  # fmt: skip


AUDIO = {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"}


def test_parse_rate() -> None:
    assert parse_rate("30000/1001") == Fraction(30000, 1001)
    assert parse_rate("0/0") is None
    assert parse_rate("abc") is None
    assert parse_rate(None) is None


def test_parse_probe_basic() -> None:
    info = parse_probe(
        Path("a.mp4"), {"format": {"duration": "12.5"}, "streams": [video_stream(), AUDIO]}
    )
    assert (info.duration_us, info.width, info.height) == (12_500_000, 1920, 1080)
    assert info.frame_rate == 60 and not info.variable_frame_rate
    assert info.has_audio and info.audio_sample_rate == 48000


def test_parse_probe_rotation_vfr_and_cover_art() -> None:
    cover = {"codec_type": "video", "disposition": {"attached_pic": 1}, "width": 1, "height": 1}
    stream = video_stream(avg_frame_rate="5994/100", side_data_list=[{"rotation": -90}])
    info = parse_probe(Path("a.mp4"), {"format": {"duration": "1"}, "streams": [cover, stream]})
    assert (info.width, info.height, info.rotation) == (1080, 1920, 270)
    assert info.variable_frame_rate
    assert not info.has_audio


def test_parse_probe_errors() -> None:
    with pytest.raises(MediaError, match="duration"):
        parse_probe(Path("a.mp4"), {"format": {}, "streams": [video_stream()]})
    with pytest.raises(MediaError, match="no audio or video"):
        parse_probe(Path("a.mp4"), {"format": {"duration": "1"}, "streams": []})


def test_ffmpeg_progress() -> None:
    seen: list[float] = []
    tracker = FfmpegProgress(10 * S, seen.append)
    for line in [
        "frame=10",
        "out_time_us=N/A",
        "out_time_us=2500000",
        "out_time_us=99000000",
        "progress=end",
    ]:
        tracker.feed(line)
    assert seen == [0.25, 1.0, 1.0]


@pytest.mark.parametrize("chunk_size", [1, 3, 7, 4096])
def test_peak_accumulator_handles_any_chunking(chunk_size: int) -> None:
    samples = [0.1, -0.5, 0.3, 0.9, -0.2, 0.0, 0.4]
    data = struct.pack(f"<{len(samples)}f", *samples)
    accumulator = PeakAccumulator(samples_per_bucket=3)
    for start in range(0, len(data), chunk_size):
        accumulator.feed(data[start : start + chunk_size])
    peaks = accumulator.result()
    expected = np.array([[-0.5, 0.3], [-0.2, 0.9], [0.4, 0.4]], dtype=np.float32)
    np.testing.assert_allclose(peaks, expected, rtol=1e-6)
    assert accumulator.samples == len(samples)


def test_peak_accumulator_empty() -> None:
    assert PeakAccumulator(80).result().shape == (0, 2)


def clip(**extra: object) -> Clip:
    data: dict[str, object] = {
        "id": "c", "path": "/media/a.mp4", "name": "a.mp4", "duration_us": 60 * S,
        "frame_rate": FrameRate(num=60), "width": 1920, "height": 1080,
        "fingerprint": "v1:10:20:abcdef", **extra,
    }  # fmt: skip
    return Clip.model_validate(data)


def test_proxy_command() -> None:
    toolchain = Toolchain(ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"))
    args = [str(arg) for arg in proxy_command(clip(), Path("out.mp4"), toolchain, 540)]
    assert args[args.index("-vf") + 1] == "scale=-2:540,fps=60/1"
    assert args[args.index("-g") + 1] == "60"
    assert "-c:a" in args and args[-1] == "out.mp4"
    silent = [
        str(a) for a in proxy_command(clip(has_audio=False, height=360), Path("o"), toolchain, 540)
    ]
    assert "-an" in silent and "scale=-2:360,fps=60/1" in silent


def test_proxy_frame_rate_fallback() -> None:
    assert proxy_frame_rate(clip(frame_rate=None)) == 30
    assert proxy_frame_rate(clip(frame_rate=FrameRate(num=1000))) == 30
    assert proxy_frame_rate(clip(frame_rate=FrameRate(num=30000, den=1001))) == Fraction(
        30000, 1001
    )


def test_strip_layout() -> None:
    info = strip_layout(clip())
    assert (info.count, info.thumb_width, info.thumb_height, info.interval_us) == (
        30,
        160,
        90,
        2 * S,
    )
    assert strip_layout(clip(duration_us=S)).count == 8
    assert strip_layout(clip(duration_us=3600 * S)).count == 120
    assert strip_layout(clip(width=1080, height=1920)).thumb_width == 50


def test_cache_paths_use_content_key(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path)
    a, moved = clip(), clip(path="/elsewhere/b.mp4", fingerprint="v1:10:99:abcdef")
    assert content_key(a) == content_key(moved)
    assert cache.proxy_path(a) == cache.proxy_path(moved)
    assert content_key(clip(fingerprint=None)) != content_key(clip(fingerprint=None, path="/x"))
    status = cache.status(a)
    assert not (status.proxy_ready or status.thumbnails_ready or status.waveform_ready)
    assert cache.status(clip(has_audio=False)).waveform_ready


def test_find_executable_override(tmp_path: Path) -> None:
    fake = tmp_path / "ffmpeg"
    assert find_executable("ffmpeg", fake) is None
    fake.write_text("")
    assert find_executable("ffmpeg", fake) == fake
    with pytest.raises(MissingBinaryError, match="eve doctor"):
        Toolchain.discover(ffmpeg=tmp_path / "missing", ffprobe=tmp_path / "missing")


def test_fixes_cover_all_platforms() -> None:
    assert "apt" in fix_for("ffmpeg", "debian")
    everything = fix_for("libmpv")
    assert all(name in everything for name in ("fedora", "debian", "arch", "windows"))
