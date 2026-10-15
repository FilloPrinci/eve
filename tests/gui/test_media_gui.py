"""GUI tests with real media: import jobs, playback, drafts and export (need ffmpeg)."""

import shutil
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from easy_video_editor.core.commands import AddSegment, CreateTimeline
from easy_video_editor.core.model import Project
from easy_video_editor.core.persistence import create_project
from easy_video_editor.ui.main_window import MainWindow

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

S = 1_000_000
CODED_FPS = 25


@pytest.fixture(scope="module")
def coded_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from tests.integration.conftest import make_coded_clip

    return make_coded_clip(tmp_path_factory.mktemp("media") / "coded.mp4", seconds=6)


@pytest.fixture
def media_window(window: MainWindow, tmp_path: Path, coded_clip: Path, qtbot: QtBot) -> MainWindow:
    """A project with the coded clip imported through the media bin (background jobs)."""
    directory = tmp_path / "Media.eve"
    create_project(directory, Project(name="Media"))
    window.open_project(directory)
    window.media_bin.import_files([coded_clip])
    cache = window.controller.cache
    assert cache is not None

    def ready() -> None:
        project = window.controller.project
        assert project is not None and project.clips
        assert cache.status(project.clips[0]).proxy_ready
        assert not window.runner.busy

    qtbot.waitUntil(ready, timeout=60_000)
    return window


def decode_index(image: QImage) -> int | None:
    """Frame index encoded in grey bands (see tests/integration/conftest.make_coded_clip)."""

    def level(band: int) -> float:
        y = (2 * band + 1) * image.height() // 8
        grey = sum(image.pixelColor(x, y).value() for x in range(2, 6)) / 4
        return grey * 219 / 255 + 16

    if abs(level(0) - 208) > 8:
        return None
    number = 0
    for band in (1, 2, 3):
        digit = round((level(band) - 16) / 32)
        if not 0 <= digit <= 7:
            return None
        number = number * 8 + digit
    return number


def test_import_runs_in_background_and_fills_the_bin(media_window: MainWindow) -> None:
    assert media_window.media_bin.list.count() == 1
    assert "ready" in media_window.media_bin.list.item(0).text()
    assert media_window.source_view._strip is not None
    assert media_window.source_view._peaks is not None
    jobs = [
        media_window.jobs_panel.list.item(i) for i in range(media_window.jobs_panel.list.count())
    ]
    assert len(jobs) == 2  # analyse + prepare


def test_duplicate_import_is_skipped(
    media_window: MainWindow, coded_clip: Path, qtbot: QtBot
) -> None:
    with qtbot.waitSignal(media_window.controller.notice, timeout=30_000) as notice:
        media_window.media_bin.import_files([coded_clip])
    assert "already in the project" in notice.args[0]


def test_draft_render_from_the_ui(media_window: MainWindow, qtbot: QtBot) -> None:
    controller = media_window.controller
    project = controller.project
    assert project is not None
    timeline_id = controller.execute(CreateTimeline(aspect="16:9")).created_ids[0]  # type: ignore[union-attr]
    controller.set_current_timeline(timeline_id)
    clip_id = project.clips[0].id
    controller.execute(AddSegment(timeline_id=timeline_id, clip_id=clip_id, in_us=S, out_us=3 * S))
    with qtbot.waitSignal(media_window.playback.draft.draftReady, timeout=60_000) as ready:
        media_window.playback.draft.render_now()
    assert Path(ready.args[0]).is_file()


def test_export_dialog_renders(media_window: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    from easy_video_editor.ui.export_dialog import ExportDialog

    controller = media_window.controller
    project = controller.project
    assert project is not None
    timeline_id = controller.execute(CreateTimeline(aspect="16:9")).created_ids[0]  # type: ignore[union-attr]
    controller.execute(AddSegment(timeline_id=timeline_id, clip_id=project.clips[0].id, in_us=0,
                                  out_us=2 * S))  # fmt: skip
    dialog = ExportDialog(controller, media_window.runner, media_window)
    dialog.folder.setText(str(tmp_path / "out"))
    dialog.draft.setChecked(True)
    assert dialog.export_button.isEnabled()
    dialog.export()
    qtbot.waitUntil(
        lambda: (tmp_path / "out" / "Media-youtube-draft.mp4").is_file(), timeout=60_000
    )


def test_mpv_player_shows_the_requested_frame(coded_clip: Path, qtbot: QtBot) -> None:
    import os

    from easy_video_editor.ui.player import MpvView, load_mpv_module

    os.environ.pop("EVE_PLAYER", None)
    module, reason = load_mpv_module(None)
    if module is None:
        pytest.skip(reason)
    view = MpvView(module)
    qtbot.addWidget(view)
    view.resize(320, 180)
    view.show()
    view.load(str(coded_clip))
    qtbot.waitUntil(lambda: view.duration > 0, timeout=10_000)
    for seconds in (2.0, 4.6):
        view.seek(seconds)

        def arrived(target: float = seconds) -> bool:
            return abs(view.position - target) < 0.02

        qtbot.waitUntil(arrived, timeout=10_000)
        qtbot.wait(200)
        image = view.render_frame()
        assert image is not None
        decoded = decode_index(image)
        assert decoded is not None and abs(decoded - round(seconds * CODED_FPS)) <= 1
    view.shutdown()


def test_svg_logo_is_rasterised(tmp_path: Path, isolated_config: Path) -> None:
    from easy_video_editor.ui.settings_panel import rasterize_logo

    svg = tmp_path / "logo.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
        '<rect width="40" height="20" fill="#ff0000"/></svg>',
        encoding="utf-8",
    )
    png = rasterize_logo(svg, "kit")
    image = QImage(str(png))
    assert png.suffix == ".png" and image.width() == 1024 and image.height() == 512
    assert png.is_relative_to(isolated_config)


def test_system_check_dialog_lists_fixes() -> None:
    from easy_video_editor.media.doctor import Check, Status
    from easy_video_editor.ui.system_check import SystemCheckDialog

    dialog = SystemCheckDialog(
        [
            Check("ffmpeg", Status.OK, "found"),
            Check("libmpv", Status.WARNING, "missing", "apt install libmpv2"),
        ]
    )
    assert dialog.fix_text() == "apt install libmpv2"


def test_full_manual_trailer(
    media_window: MainWindow, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M4 acceptance: a trailer made entirely by hand through the UI."""
    from PySide6.QtWidgets import QInputDialog

    from easy_video_editor.core.model import ZoneKind
    from easy_video_editor.ui.controller import Selection, SelectionKind
    from easy_video_editor.ui.export_dialog import ExportDialog

    window = media_window
    controller = window.controller
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("16:9", True))
    window.new_timeline()
    view = window.source_view
    for start, end in ((1 * S, 2 * S), (4 * S, 5 * S)):
        view.set_playhead(start)
        view.set_mark_in()
        view.set_playhead(end)
        view.set_mark_out()
        view.zone_from_marks(ZoneKind.HIGHLIGHT)
        selection = controller.selection
        assert selection is not None
        controller.select(Selection(SelectionKind.ZONE, selection.item_id))
        window.inspector.zone_form.to_timeline.click()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("V-CORE", True))
    window.add_text()
    window.settings_panel.cta.setText("Wishlist now")
    window.settings_panel.cta.editingFinished.emit()

    timeline = controller.current_timeline
    assert timeline is not None and len(timeline.segments) == 2 and len(timeline.texts) == 1
    assert timeline.end_card.cta_text == "Wishlist now"

    dialog = ExportDialog(controller, window.runner, window)
    dialog.folder.setText(str(tmp_path / "out"))
    assert dialog.export_button.isEnabled()
    dialog.export()
    output = tmp_path / "out" / "Media-youtube.mp4"
    qtbot.waitUntil(output.is_file, timeout=120_000)


def test_run_all_phases_from_the_panel(media_window: MainWindow, qtbot: QtBot) -> None:
    from easy_video_editor.core.model import Origin, ZoneKind
    from easy_video_editor.ui.automation_panel import ALL_PHASES

    panel = media_window.automation
    assert not panel.mode.model().item(1).isEnabled()  # type: ignore[attr-defined]
    history = len(media_window.controller.session.history)  # type: ignore[union-attr]
    finished: list[bool] = []
    panel.run(list(ALL_PHASES), then=lambda: finished.append(True))
    qtbot.waitUntil(lambda: bool(finished), timeout=120_000)
    project = media_window.controller.project
    assert project is not None
    assert project.zones_of_kind(ZoneKind.CANDIDATE) and project.zones_of_kind(ZoneKind.HIGHLIGHT)
    assert project.timelines and project.timelines[0].segments
    entries = media_window.controller.session.history[history:]  # type: ignore[union-attr]
    assert [entry.origin for entry in entries] == [Origin.AUTO] * 3
    texts = [panel.activity.item(i).text() for i in range(panel.activity.count())]
    assert any("candidate zones" in text for text in texts) and texts[-1] == "Done."
    assert media_window.controller.current_timeline_id == project.timelines[0].id
