"""Identifier generation for project items."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Return a new random identifier such as ``zone_1a2b3c4d5e6f``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
