"""Bundled resources (fonts, icons, translations)."""

from __future__ import annotations

from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent
DEFAULT_FONT = RESOURCES_DIR / "fonts" / "Anton-Regular.ttf"
