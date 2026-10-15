"""File fingerprints (SPEC §7.2): size + mtime + hash of sampled chunks.

The content key (size + sampled hash) identifies a file independently of its location and
modification time, so it survives moves and copies: it keys caches and drives relinking.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

SAMPLE_SIZE = 1 << 20
"""Bytes hashed at the start, middle and end of the file."""
VERSION = "v1"


@dataclass(frozen=True)
class Fingerprint:
    size: int
    mtime_ns: int
    sample_hash: str

    @property
    def content_key(self) -> str:
        """Stable identifier of the file content, used for cache file names."""
        return hashlib.sha256(f"{self.size}:{self.sample_hash}".encode()).hexdigest()[:16]

    def same_content(self, other: Fingerprint) -> bool:
        return self.size == other.size and self.sample_hash == other.sample_hash

    def __str__(self) -> str:
        return f"{VERSION}:{self.size}:{self.mtime_ns}:{self.sample_hash}"

    @classmethod
    def parse(cls, text: str) -> Fingerprint:
        version, size, mtime_ns, sample_hash = text.split(":")
        if version != VERSION:
            raise ValueError(f"Unsupported fingerprint version: {version}")
        return cls(int(size), int(mtime_ns), sample_hash)


def sample_hash(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if size <= 3 * SAMPLE_SIZE:
            digest.update(handle.read())
        else:
            for offset in (0, size // 2 - SAMPLE_SIZE // 2, size - SAMPLE_SIZE):
                handle.seek(offset)
                digest.update(handle.read(SAMPLE_SIZE))
    return digest.hexdigest()


def fingerprint(path: Path) -> Fingerprint:
    stat = path.stat()
    return Fingerprint(stat.st_size, stat.st_mtime_ns, sample_hash(path, stat.st_size))
