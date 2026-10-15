import json
from pathlib import Path
from typing import Any

import pytest

from easy_video_editor.core import persistence
from easy_video_editor.core.errors import ProjectFormatError
from easy_video_editor.core.model import CURRENT_SCHEMA_VERSION, Project
from easy_video_editor.core.persistence import (
    BACKUP_FILE,
    PROJECT_FILE,
    SUBDIRECTORIES,
    create_project,
    load_project,
    project_dir_for,
    save_project,
)
from easy_video_editor.core.session import ProjectSession

from .conftest import BLUE, RED, zone


def test_round_trip(tmp_path: Path, session: ProjectSession) -> None:
    zone(session, BLUE, 0, 30)
    zone(session, RED, 10, 12)
    directory = project_dir_for(tmp_path, "My Trailer")
    create_project(directory, session.project)
    assert directory.name == "My Trailer.eve"
    assert all((directory / name).is_dir() for name in SUBDIRECTORIES)
    assert load_project(directory) == session.project


def test_save_is_atomic_and_keeps_a_backup(tmp_path: Path) -> None:
    create_project(tmp_path, Project(name="First"))
    save_project(Project(name="Second"), tmp_path)
    assert load_project(tmp_path).name == "Second"
    backup = json.loads((tmp_path / BACKUP_FILE).read_text(encoding="utf-8"))
    assert backup["name"] == "First"
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [
        PROJECT_FILE,
        BACKUP_FILE,
    ]


def test_create_refuses_existing_project(tmp_path: Path) -> None:
    create_project(tmp_path, Project(name="A"))
    with pytest.raises(ProjectFormatError, match="already exists"):
        create_project(tmp_path, Project(name="B"))


@pytest.mark.parametrize("name", ["", "a/b", "a\\b", "..", "what?"])
def test_invalid_project_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ProjectFormatError):
        project_dir_for(tmp_path, name)


def test_missing_and_corrupt_files(tmp_path: Path) -> None:
    with pytest.raises(ProjectFormatError, match="No project"):
        load_project(tmp_path)
    (tmp_path / PROJECT_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectFormatError, match="Cannot read"):
        load_project(tmp_path)
    (tmp_path / PROJECT_FILE).write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(ProjectFormatError, match="not a valid project"):
        load_project(tmp_path)


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    document = {"schema_version": CURRENT_SCHEMA_VERSION + 1, "name": "Future"}
    (tmp_path / PROJECT_FILE).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectFormatError, match="newer version"):
        load_project(tmp_path)


def test_migrations_are_applied_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    def rename_title(document: dict[str, Any]) -> dict[str, Any]:
        document["name"] = document.pop("title")
        return document

    monkeypatch.setattr(persistence, "CURRENT_SCHEMA_VERSION", 2)
    monkeypatch.setattr(persistence, "MIGRATIONS", {1: rename_title})
    migrated = persistence.migrate({"schema_version": 1, "title": "Old"})
    assert migrated == {"schema_version": 2, "name": "Old"}

    monkeypatch.setattr(persistence, "MIGRATIONS", {})
    with pytest.raises(ProjectFormatError, match="No migration"):
        persistence.migrate({"schema_version": 1})
