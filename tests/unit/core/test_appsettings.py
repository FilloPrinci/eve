from pathlib import Path

import pytest

from easy_video_editor.appsettings import (
    MAX_RECENT_PROJECTS,
    AppSettings,
    load_app_settings,
    save_app_settings,
)
from easy_video_editor.core.brand import BrandKit
from easy_video_editor.core.errors import NotFoundError


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(proxy_height=360, brand_kits=[BrandKit(id="b", name="V-Core")])
    save_app_settings(settings, path)
    assert load_app_settings(path) == settings
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_missing_or_broken_files_give_defaults(tmp_path: Path) -> None:
    assert load_app_settings(tmp_path / "none.json") == AppSettings()
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert load_app_settings(broken) == AppSettings()


def test_recent_projects_are_unique_and_bounded(tmp_path: Path) -> None:
    settings = AppSettings()
    for index in range(MAX_RECENT_PROJECTS + 3):
        settings.add_recent(tmp_path / f"p{index}")
    settings.add_recent(tmp_path / "p5")
    assert len(settings.recent_projects) == MAX_RECENT_PROJECTS
    assert settings.recent_projects[0].endswith("p5")
    assert len(set(settings.recent_projects)) == MAX_RECENT_PROJECTS


def test_brand_kit_lookup() -> None:
    settings = AppSettings(brand_kits=[BrandKit(id="b", name="B")])
    assert settings.brand_kit("b").name == "B"
    with pytest.raises(NotFoundError):
        settings.brand_kit("x")
