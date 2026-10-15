"""Detecting missing or changed source files and finding them again (SPEC §7.2)."""

from __future__ import annotations

import os
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from easy_video_editor.core.model import Clip
from easy_video_editor.core.progress import CancellationToken
from easy_video_editor.media.fingerprint import Fingerprint, fingerprint, sample_hash


class SourceState(StrEnum):
    OK = "ok"
    MISSING = "missing"
    CHANGED = "changed"
    """A file exists at the path but its content differs from the imported one."""


def check_source(clip: Clip) -> SourceState:
    path = clip.file_path
    if not path.is_file():
        return SourceState.MISSING
    if clip.fingerprint is None:
        return SourceState.OK
    expected = Fingerprint.parse(clip.fingerprint)
    stat = path.stat()
    if stat.st_size != expected.size:
        return SourceState.CHANGED
    if stat.st_mtime_ns == expected.mtime_ns:
        return SourceState.OK
    same = sample_hash(path, stat.st_size) == expected.sample_hash
    return SourceState.OK if same else SourceState.CHANGED


def find_candidates(
    clip: Clip, search_dirs: Iterable[Path], token: CancellationToken | None = None
) -> list[Path]:
    """Files under ``search_dirs`` with the same content as ``clip``; same file name first."""
    if clip.fingerprint is None:
        return []
    expected = Fingerprint.parse(clip.fingerprint)
    matches: list[Path] = []
    for directory in search_dirs:
        for root, _, files in os.walk(directory):
            if token is not None:
                token.raise_if_cancelled()
            for name in files:
                path = Path(root) / name
                try:
                    if path.stat().st_size != expected.size:
                        continue
                    if fingerprint(path).same_content(expected):
                        matches.append(path.resolve())
                except OSError:
                    continue
    original_name = clip.file_path.name
    return sorted(set(matches), key=lambda p: (p.name != original_name, str(p)))
