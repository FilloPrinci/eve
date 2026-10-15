"""Project folders and `project.json` persistence (SPEC §7.2).

`project.json` is written atomically (temporary file in the same folder, fsync, rename) and the
previous version is kept as `project.json.bak`. Older schema versions are migrated on load.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from easy_video_editor.core.errors import ProjectFormatError
from easy_video_editor.core.model import CURRENT_SCHEMA_VERSION, Project

PROJECT_SUFFIX = ".eve"
PROJECT_FILE = "project.json"
BACKUP_FILE = "project.json.bak"
SUBDIRECTORIES = ("cache", "proxies", "thumbs", "exports")

Migration = Callable[[dict[str, Any]], dict[str, Any]]

MIGRATIONS: dict[int, Migration] = {}
"""``MIGRATIONS[n]`` upgrades a project document from schema version ``n`` to ``n + 1``."""

_FORBIDDEN_NAME_CHARACTERS = set('<>:"/\\|?*')


def project_dir_for(parent: Path, name: str) -> Path:
    """Folder of a project called ``name`` inside ``parent`` (``<parent>/<name>.eve``)."""
    if not name.strip() or name in {".", ".."} or _FORBIDDEN_NAME_CHARACTERS & set(name):
        raise ProjectFormatError(f"Invalid project name: {name!r}")
    return parent / f"{name}{PROJECT_SUFFIX}"


def create_project(directory: Path, project: Project) -> None:
    """Create a new project folder with its subfolders and save ``project`` into it."""
    if (directory / PROJECT_FILE).exists():
        raise ProjectFormatError(f"A project already exists in {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    for name in SUBDIRECTORIES:
        (directory / name).mkdir(exist_ok=True)
    save_project(project, directory)


def save_project(project: Project, directory: Path) -> None:
    target = directory / PROJECT_FILE
    data = project.model_dump_json(indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=".project-", suffix=".tmp", dir=directory)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            shutil.copy2(target, directory / BACKUP_FILE)
        os.replace(temp_path, target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def load_project(directory: Path) -> Project:
    path = directory / PROJECT_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProjectFormatError(f"No project found in {directory}") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectFormatError(f"Cannot read {path}: {error}") from error
    if not isinstance(document, dict):
        raise ProjectFormatError(f"{path} does not contain a project.")
    document = migrate(document)
    try:
        return Project.model_validate(document)
    except ValidationError as error:
        raise ProjectFormatError(f"{path} is not a valid project: {error}") from error


def migrate(document: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a project document to `CURRENT_SCHEMA_VERSION`."""
    version = document.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise ProjectFormatError("The project file has no valid schema_version.")
    if version > CURRENT_SCHEMA_VERSION:
        raise ProjectFormatError(
            f"The project was saved by a newer version of EVE (schema {version}); "
            f"this version supports schema {CURRENT_SCHEMA_VERSION}. Please update EVE."
        )
    while version < CURRENT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ProjectFormatError(f"No migration from schema version {version}.")
        document = migration(document)
        version += 1
        document["schema_version"] = version
    return document
