"""Instant preview (SPEC §6.2): an mpv EDL that plays the cut list without rendering.

The EDL plays segments of proxy files in sequence. It is an approximation: speed changes,
transitions and the end card are not represented (the draft render shows them).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from easy_video_editor.core.model import Clip, EditTimeline, Project

US = 1_000_000


@dataclass(frozen=True)
class EdlEntry:
    path: Path
    start_us: int
    length_us: int
    output_start_us: int
    """Where this entry starts on the edit timeline (output time)."""
    output_length_us: int
    segment_id: str


def _seconds(us: int) -> str:
    return f"{us / US:.6f}".rstrip("0").rstrip(".") or "0"


def edl_entries(
    project: Project, timeline: EditTimeline, media_path: Callable[[Clip], Path]
) -> list[EdlEntry]:
    entries = []
    position = 0
    for segment in timeline.segments:
        clip = project.clip(segment.clip_id)
        entries.append(
            EdlEntry(
                path=media_path(clip),
                start_us=segment.in_us,
                length_us=segment.out_us - segment.in_us,
                output_start_us=position,
                output_length_us=segment.output_duration_us,
                segment_id=segment.id,
            )
        )
        position += segment.output_duration_us
    return entries


def edl_url(entries: list[EdlEntry]) -> str:
    """``edl://`` URL. Paths are length-prefixed (``%bytes%path``), so commas and semicolons in
    file names are safe."""
    parts = []
    for entry in entries:
        path = str(entry.path)
        parts.append(
            f"%{len(path.encode('utf-8'))}%{path},{_seconds(entry.start_us)},"
            f"{_seconds(entry.length_us)}"
        )
    return "edl://" + ";".join(parts)


def edl_time_to_output_us(entries: list[EdlEntry], edl_seconds: float) -> int:
    """Map a playback position in the EDL (source-time lengths) to edit-timeline time."""
    position_us = round(edl_seconds * US)
    elapsed = 0
    for entry in entries:
        if position_us < elapsed + entry.length_us:
            fraction = (position_us - elapsed) / entry.length_us
            return entry.output_start_us + round(fraction * entry.output_length_us)
        elapsed += entry.length_us
    return entries[-1].output_start_us + entries[-1].output_length_us if entries else 0


def output_us_to_edl_time(entries: list[EdlEntry], output_us: int) -> float:
    elapsed = 0
    for entry in entries:
        if output_us < entry.output_start_us + entry.output_length_us:
            fraction = (output_us - entry.output_start_us) / max(entry.output_length_us, 1)
            return (elapsed + fraction * entry.length_us) / US
        elapsed += entry.length_us
    return elapsed / US
