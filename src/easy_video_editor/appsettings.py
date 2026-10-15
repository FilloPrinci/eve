"""Application settings (SPEC §9), stored as JSON in the user config folder.

Holds binary paths, proxy resolution, cache limit, recent projects and the brand kits shared by
all projects. Never stores secrets: API keys live in the OS keyring.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import platformdirs
from pydantic import Field, ValidationError

from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.errors import NotFoundError
from easy_video_editor.core.model import EveModel

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
MAX_RECENT_PROJECTS = 10


def config_dir() -> Path:
    return Path(platformdirs.user_config_dir("EVE", appauthor=False))


class AppSettings(EveModel):
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    libmpv_path: str | None = None
    proxy_height: int = Field(default=540, ge=144, le=1080)
    cache_limit_mb: int = Field(default=20_000, ge=0)
    recent_projects: list[str] = Field(default_factory=list)
    system_check_done: bool = False
    brand_kits: list[BrandKit] = Field(default_factory=list)

    def brand_kit(self, kit_id: str) -> BrandKit:
        for kit in self.brand_kits:
            if kit.id == kit_id:
                return kit
        raise NotFoundError(f"Brand kit {kit_id} does not exist.")

    def add_recent(self, project_dir: Path) -> None:
        path = str(project_dir.resolve())
        self.recent_projects = [path] + [p for p in self.recent_projects if p != path]
        del self.recent_projects[MAX_RECENT_PROJECTS:]


def load_app_settings(path: Path | None = None) -> AppSettings:
    path = path or config_dir() / SETTINGS_FILE
    try:
        return AppSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppSettings()
    except (OSError, ValidationError, ValueError) as error:
        log.warning("Ignoring unreadable settings file %s: %s", path, error)
        return AppSettings()


def save_app_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or config_dir() / SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(settings.model_dump_json(indent=2) + "\n")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
