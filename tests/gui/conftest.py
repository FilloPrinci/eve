from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from easy_video_editor import appsettings
from easy_video_editor.appsettings import AppSettings
from easy_video_editor.core.commands import AddClip, CreateTimeline
from easy_video_editor.core.model import FrameRate, Origin, Project
from easy_video_editor.core.persistence import create_project, load_project, save_project
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.ui.main_window import MainWindow

S = 1_000_000


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep tests away from the real user configuration and from libmpv by default."""
    config = tmp_path / "config"
    monkeypatch.setattr(appsettings, "config_dir", lambda: config)
    monkeypatch.setenv("EVE_PLAYER", "none")
    return config


@pytest.fixture
def window(qtbot: QtBot) -> Iterator[MainWindow]:
    main = MainWindow(AppSettings(system_check_done=True))
    qtbot.addWidget(main)
    main.resize(1400, 900)
    main.show()
    qtbot.waitExposed(main)
    yield main
    main.jobs.shutdown(cancel=True)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A project with one 60 s, 1920x1080, 30 fps clip (no media on disk) and a 16:9 timeline."""
    directory = tmp_path / "Test.eve"
    create_project(directory, Project(name="Test"))
    session = ProjectSession(load_project(directory))
    session.execute(
        AddClip(
            path=str(Path(tmp_path.anchor) / "media" / "lap.mp4"), duration_us=60 * S,
            clip_id="clip", frame_rate=FrameRate(num=30), width=1920, height=1080,
        ),
        Origin.USER,
    )  # fmt: skip
    session.execute(CreateTimeline(aspect="16:9", timeline_id="tl"), Origin.USER)
    save_project(session.project, directory)
    return directory


@pytest.fixture
def opened(window: MainWindow, project_dir: Path) -> MainWindow:
    window.open_project(project_dir)
    return window
