"""Text styles for overlays and end cards.

Sizes are fractions of the short side of the output, so styles look the same at every
resolution (including low-resolution drafts) and in vertical formats.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextStyle:
    size: float
    """Font size as a fraction of the short side of the output."""
    color: str | None = None
    """``#RRGGBB``; None means the brand kit's text colour."""
    border: float = 0.0
    """Outline width as a fraction of the font size."""
    box: str | None = None
    """``primary`` (brand primary colour), ``#RRGGBB[@alpha]`` or None for no box."""


TEXT_STYLES: dict[str, TextStyle] = {
    "title": TextStyle(size=0.10, border=0.06),
    "caption": TextStyle(size=0.05, box="#000000@0.55"),
    "cta": TextStyle(size=0.065, color="#000000", box="primary"),
}

DEFAULT_TEXT_COLOR = "#FFFFFF"
DEFAULT_PRIMARY_COLOR = "#FFD400"


def ffmpeg_color(color: str) -> str:
    """``#RRGGBB[@alpha]`` → ffmpeg colour syntax ``0xRRGGBB[@alpha]``."""
    return "0x" + color.removeprefix("#") if color.startswith("#") else color
