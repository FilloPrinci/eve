"""Text looks and styles.

A *look* is a coordinated set of the three text styles (``title``, ``caption``, ``cta``); the
project setting ``text_look`` selects one. Colours may be ``#RRGGBB[@alpha]`` or
``primary[@alpha]`` (the brand kit's primary colour). Sizes are fractions of the short side of the
output, so styles look the same at every resolution (including low-resolution drafts) and in
vertical formats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from easy_video_editor.core.model import TextLook


@dataclass(frozen=True)
class TextStyle:
    size: float
    """Font size as a fraction of the short side of the output."""
    color: str | None = None
    """None means the brand kit's text colour."""
    border: float = 0.0
    """Outline width as a fraction of the font size."""
    border_color: str = "black"
    box: str | None = None
    """Box drawn tightly around the text, or None."""
    shadow: str | None = None
    shadow_offset: float = 0.0
    """Shadow offset as a fraction of the font size."""
    band: str | None = None
    """Full-width band drawn behind the text, or None."""
    align: Literal["center", "left"] = "center"


STYLE_NAMES = ("title", "caption", "cta")

TEXT_LOOKS: dict[TextLook, dict[str, TextStyle]] = {
    TextLook.CLASSIC: {
        "title": TextStyle(size=0.10, border=0.06),
        "caption": TextStyle(size=0.05, box="#000000@0.55"),
        "cta": TextStyle(size=0.065, color="#000000", box="primary"),
    },
    TextLook.BAND: {
        "title": TextStyle(size=0.11, color="primary", band="#000000@0.6"),
        "caption": TextStyle(size=0.05, shadow="#000000@0.8", shadow_offset=0.1, align="left"),
        "cta": TextStyle(size=0.065, color="primary", border=0.08),
    },
    TextLook.BOLD: {
        "title": TextStyle(size=0.15, color="#FFFFFF", shadow="primary", shadow_offset=0.07),
        "caption": TextStyle(size=0.05, box="primary@0.9", align="left"),
        "cta": TextStyle(size=0.065, color="#FFFFFF", box="primary"),
    },
}

DEFAULT_TEXT_COLOR = "#FFFFFF"
DEFAULT_PRIMARY_COLOR = "#FFD400"
LEFT_MARGIN = 0.05
"""Left-aligned texts start at this fraction of the width."""


def resolve_color(color: str, primary: str) -> str:
    """Replace the ``primary`` keyword (optionally with ``@alpha``) by the brand colour."""
    name, separator, alpha = color.partition("@")
    if name == "primary":
        return primary + (f"@{alpha}" if separator else "")
    return color


def ffmpeg_color(color: str) -> str:
    """``#RRGGBB[@alpha]`` → ffmpeg colour syntax ``0xRRGGBB[@alpha]``."""
    return "0x" + color.removeprefix("#") if color.startswith("#") else color
