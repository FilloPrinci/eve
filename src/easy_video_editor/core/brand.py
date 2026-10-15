"""Brand kits (SPEC §9): reusable logo, titles, colours and fonts."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from easy_video_editor.core.model import EveModel

Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


class BrandKit(EveModel):
    id: str
    name: str
    logo_path: str | None = None
    """PNG or JPEG. SVG logos are rasterised to PNG when added to a brand kit."""
    game_title: str = ""
    cta_text: str = ""
    """Default call-to-action text for end cards."""
    primary_color: Color = "#FFD400"
    text_color: Color = "#FFFFFF"
    background_color: Color = "#000000"
    font_path: str | None = None
    """None means: the bundled default font."""
    music_dir: str | None = None
