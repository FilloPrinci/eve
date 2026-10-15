"""Conversions between integer microseconds, seconds and frames.

The model stores every time as integer microseconds. Convert only at boundaries.
"""

from __future__ import annotations

from fractions import Fraction

US_PER_SECOND = 1_000_000


def seconds_to_us(seconds: float) -> int:
    return round(seconds * US_PER_SECOND)


def us_to_seconds(us: int) -> float:
    return us / US_PER_SECOND


def us_to_frame(us: int, fps: Fraction) -> int:
    """Index of the frame displayed at ``us`` (frames start at 0)."""
    return int(Fraction(us) * fps // US_PER_SECOND)


def frame_to_us(frame: int, fps: Fraction) -> int:
    """Start time of ``frame``, rounded to the nearest microsecond."""
    return round(Fraction(frame * US_PER_SECOND) / fps)


def snap_to_frame(us: int, fps: Fraction) -> int:
    """Snap ``us`` to the nearest frame boundary."""
    return frame_to_us(round(Fraction(us) * fps / US_PER_SECOND), fps)


def format_us(us: int) -> str:
    """Format as ``MM:SS.mmm`` (or ``H:MM:SS.mmm`` above one hour) for messages and labels."""
    sign = "-" if us < 0 else ""
    total_ms = abs(us) // 1000
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{sign}{minutes:02d}:{seconds:02d}.{millis:03d}"
