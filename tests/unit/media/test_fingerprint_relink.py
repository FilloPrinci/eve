import os
from pathlib import Path

import pytest

from easy_video_editor.core.model import Clip
from easy_video_editor.media.fingerprint import SAMPLE_SIZE, Fingerprint, fingerprint
from easy_video_editor.media.relink import SourceState, check_source, find_candidates


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def clip_for(path: Path) -> Clip:
    return Clip(
        id="c", path=str(path), name=path.name, duration_us=1, fingerprint=str(fingerprint(path))
    )


def test_fingerprint_round_trip(tmp_path: Path) -> None:
    value = fingerprint(write(tmp_path / "a.bin", b"hello"))
    assert Fingerprint.parse(str(value)) == value
    assert str(value).startswith("v1:5:")
    with pytest.raises(ValueError):
        Fingerprint.parse("v0:1:2:abc")


def test_large_files_are_sampled(tmp_path: Path) -> None:
    data = bytearray(os.urandom(4 * SAMPLE_SIZE))
    a = fingerprint(write(tmp_path / "a.bin", bytes(data)))
    data[SAMPLE_SIZE + 10] ^= 0xFF  # outside every sampled chunk
    b = fingerprint(write(tmp_path / "b.bin", bytes(data)))
    data[5] ^= 0xFF  # inside the first sampled chunk
    c = fingerprint(write(tmp_path / "c.bin", bytes(data)))
    assert a.same_content(b)
    assert not a.same_content(c)
    assert a.content_key == b.content_key != c.content_key


def test_check_source_states(tmp_path: Path) -> None:
    path = write(tmp_path / "clip.mp4", b"original")
    clip = clip_for(path)
    assert check_source(clip) is SourceState.OK
    os.utime(path, ns=(1, 1))  # touched but identical: still OK
    assert check_source(clip) is SourceState.OK
    path.write_bytes(b"modified")  # same size, different content
    os.utime(path, ns=(2, 2))  # explicit: Windows file times may not advance within ~15 ms
    assert check_source(clip) is SourceState.CHANGED
    path.write_bytes(b"much longer content")
    assert check_source(clip) is SourceState.CHANGED
    path.unlink()
    assert check_source(clip) is SourceState.MISSING


def test_find_candidates_prefers_same_name(tmp_path: Path) -> None:
    original = write(tmp_path / "old" / "clip.mp4", b"content")
    clip = clip_for(original)
    original.unlink()
    renamed = write(tmp_path / "new" / "a" / "renamed.mp4", b"content")
    same_name = write(tmp_path / "new" / "z" / "clip.mp4", b"content")
    write(tmp_path / "new" / "other.mp4", b"CONTENT")
    write(tmp_path / "new" / "short.mp4", b"x")
    assert find_candidates(clip, [tmp_path / "new"]) == [same_name.resolve(), renamed.resolve()]
