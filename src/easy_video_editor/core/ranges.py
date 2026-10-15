"""Half-open integer time ranges ``[start, end)``."""

from __future__ import annotations


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def subtract(start: int, end: int, cut_start: int, cut_end: int) -> list[tuple[int, int]]:
    """Return the parts of ``[start, end)`` that remain after removing ``[cut_start, cut_end)``."""
    if not overlaps(start, end, cut_start, cut_end):
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    if start < cut_start:
        pieces.append((start, cut_start))
    if cut_end < end:
        pieces.append((cut_end, end))
    return pieces
