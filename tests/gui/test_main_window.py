"""GUI smoke tests (SPEC §15): the window, project loading, lanes, inspector, timeline."""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from easy_video_editor.core.commands import AddSegment, CreateZone
from easy_video_editor.core.model import TextLook, ZoneKind
from easy_video_editor.ui.controller import Selection, SelectionKind
from easy_video_editor.ui.main_window import MainWindow
from easy_video_editor.ui.player import PlaceholderView
from easy_video_editor.ui.source_view import LANE_H, LANES, lane_top
from easy_video_editor.ui.timeline_view import VIDEO_H, segment_spans

S = 1_000_000


def lane_point(window: MainWindow, kind: ZoneKind, us: int) -> QPoint:
    view = window.source_view
    return QPoint(round(view.scale.x(us)), lane_top(LANES.index(kind)) + LANE_H // 2)


def drag(qtbot: QtBot, widget: QWidget, start: QPoint, end: QPoint) -> None:
    left, none = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    QTest.mousePress(widget, left, none, start)
    QTest.mouseMove(widget, QPoint((start.x() + end.x()) // 2, start.y()))
    QTest.mouseMove(widget, end)
    QTest.mouseRelease(widget, left, none, end)


def test_main_window_opens(window: MainWindow) -> None:
    assert window.isVisible()
    assert "EVE" in window.windowTitle()
    assert isinstance(window.player_host.view, PlaceholderView)


def test_project_loads(opened: MainWindow) -> None:
    assert "Test" in opened.windowTitle()
    assert opened.media_bin.list.count() == 1
    assert opened.source_view.clip is not None
    assert opened.timeline_choice.count() == 1


def test_zone_commands_update_the_lanes(opened: MainWindow) -> None:
    controller = opened.controller
    controller.execute(
        CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=S, end_us=5 * S)
    )
    assert [z.kind for z in opened.source_view.zones()] == [ZoneKind.CANDIDATE]
    opened.source_view.grab()  # paints without errors


def test_drag_creates_moves_and_resizes_zones(opened: MainWindow, qtbot: QtBot) -> None:
    view = opened.source_view
    drag(qtbot, view, lane_point(opened, ZoneKind.CANDIDATE, 10 * S),
         lane_point(opened, ZoneKind.CANDIDATE, 20 * S))  # fmt: skip
    (zone,) = view.zones(ZoneKind.CANDIDATE)
    assert zone.locked and zone.origin == "user"
    assert abs(zone.start_us - 10 * S) < S and abs(zone.end_us - 20 * S) < S
    for edge in (zone.start_us, zone.end_us):  # snapped to the 30 fps frame grid
        assert edge == round(round(edge * 30 / S) * S / 30)

    length = zone.end_us - zone.start_us
    middle = (zone.start_us + zone.end_us) // 2
    drag(qtbot, view, lane_point(opened, ZoneKind.CANDIDATE, middle),
         lane_point(opened, ZoneKind.CANDIDATE, middle + 10 * S))  # fmt: skip
    moved = opened.controller.project.zone(zone.id)  # type: ignore[union-attr]
    assert moved.end_us - moved.start_us == length and moved.start_us > zone.start_us + 5 * S

    drag(qtbot, view, lane_point(opened, ZoneKind.CANDIDATE, moved.end_us),
         lane_point(opened, ZoneKind.CANDIDATE, moved.end_us + 5 * S))  # fmt: skip
    resized = opened.controller.project.zone(zone.id)  # type: ignore[union-attr]
    assert resized.start_us == moved.start_us and resized.end_us > moved.end_us + 3 * S


def test_marks_and_red_zone_trim_with_notice(opened: MainWindow, qtbot: QtBot) -> None:
    controller = opened.controller
    controller.execute(
        CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=0, end_us=30 * S)
    )
    view = opened.source_view
    view.set_playhead(10 * S)
    view.set_mark_in()
    view.set_playhead(12 * S)
    view.set_mark_out()
    with qtbot.waitSignal(controller.notice, timeout=1000) as notice:
        view.zone_from_marks(ZoneKind.EXCLUDE)
    assert "Split" in notice.args[0]
    assert len(view.zones(ZoneKind.CANDIDATE)) == 2


def test_rejected_command_is_reported(opened: MainWindow, qtbot: QtBot) -> None:
    controller = opened.controller
    controller.execute(CreateZone(kind=ZoneKind.EXCLUDE, clip_id="clip", start_us=0, end_us=5 * S))
    with qtbot.waitSignal(controller.error, timeout=1000) as error:
        controller.execute(
            CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=S, end_us=2 * S)
        )
    assert "exclude zone" in error.args[0]
    assert "exclude zone" in opened.statusBar().currentMessage()


def test_inspector_edits_zone_and_adds_it_to_timeline(opened: MainWindow) -> None:
    controller = opened.controller
    result = controller.execute(
        CreateZone(kind=ZoneKind.HIGHLIGHT, clip_id="clip", start_us=2 * S, end_us=4 * S)
    )
    assert result is not None
    zone_id = result.created_ids[0]
    controller.select(Selection(SelectionKind.ZONE, zone_id))
    form = opened.inspector.zone_form
    assert opened.inspector.stack.currentWidget() is form
    form.label.setText("Big jump")
    form.label.editingFinished.emit()
    assert controller.project.zone(zone_id).label == "Big jump"  # type: ignore[union-attr]
    form.to_timeline.click()
    timeline = controller.current_timeline
    assert timeline is not None and timeline.segments[0].zone_id == zone_id


def test_timeline_drag_reorders_and_trims(opened: MainWindow, qtbot: QtBot) -> None:
    controller = opened.controller
    for second in (0, 10, 20):
        controller.execute(
            AddSegment(timeline_id="tl", clip_id="clip", in_us=second * S, out_us=(second + 2) * S)
        )  # fmt: skip
    view = opened.timeline_view
    view.refresh()
    y = view.video_top() + VIDEO_H // 2
    timeline = controller.current_timeline
    assert timeline is not None
    spans = segment_spans(timeline)
    first_id = timeline.segments[0].id
    start = QPoint(round(view.scale.x((spans[0][0] + spans[0][1]) / 2)), y)
    end = QPoint(round(view.scale.x(spans[2][1] - S // 4)), y)
    drag(qtbot, view, start, end)
    timeline = controller.current_timeline
    assert timeline is not None and timeline.segments[-1].id == first_id

    spans = segment_spans(timeline)
    edge = QPoint(round(view.scale.x(spans[0][1])), y)
    drag(qtbot, view, edge, QPoint(edge.x() - round(S / view.scale.us_per_px), y))
    timeline = controller.current_timeline
    assert timeline is not None
    assert timeline.segments[0].out_us - timeline.segments[0].in_us < 2 * S - S // 2


def test_undo_redo_and_history(opened: MainWindow) -> None:
    controller = opened.controller
    controller.execute(CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=0, end_us=S))
    assert opened.history.tree.topLevelItemCount() >= 1
    controller.undo()
    assert opened.source_view.zones() == []
    controller.redo()
    assert len(opened.source_view.zones()) == 1


def test_settings_panel_changes_text_look(opened: MainWindow) -> None:
    panel = opened.settings_panel
    panel.text_look.setCurrentIndex(2)
    assert opened.controller.project.settings.text_look is TextLook.BOLD  # type: ignore[union-attr]


def test_autosave_writes_the_project(opened: MainWindow, project_dir: Path, qtbot: QtBot) -> None:
    opened.controller.execute(
        CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=0, end_us=S)
    )

    def saved() -> None:
        data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        assert len(data["zones"]) == 1

    qtbot.waitUntil(saved, timeout=3000)


def test_delete_selection(opened: MainWindow) -> None:
    controller = opened.controller
    result = controller.execute(
        CreateZone(kind=ZoneKind.CANDIDATE, clip_id="clip", start_us=0, end_us=S)
    )
    assert result is not None
    controller.select(Selection(SelectionKind.ZONE, result.created_ids[0]))
    opened.delete_selection()
    assert opened.source_view.zones() == []


def test_log_console_and_f12(opened: MainWindow, qtbot: QtBot) -> None:
    logger = logging.getLogger("easy_video_editor.test")
    logger.addHandler(opened.log_handler)
    logger.warning("hello from the test")
    qtbot.waitUntil(lambda: "hello from the test" in opened.log_console.view.toPlainText())
    assert not opened.log_dock.isVisible()
    QTest.keyClick(opened, Qt.Key.Key_F12)
    assert opened.log_dock.isVisible()
    logger.removeHandler(opened.log_handler)


def test_export_dialog_blocks_invalid_projects(opened: MainWindow) -> None:
    from easy_video_editor.ui.export_dialog import ExportDialog

    dialog = ExportDialog(opened.controller, opened.runner, opened)
    assert not dialog.export_button.isEnabled()  # empty timeline
    opened.controller.execute(AddSegment(timeline_id="tl", clip_id="clip", in_us=0, out_us=S))
    dialog.validate()
    messages = [dialog.issues.item(i).text() for i in range(dialog.issues.count())]
    assert any("missing" in message for message in messages)  # the source file does not exist
    assert not dialog.export_button.isEnabled()


def test_import_without_a_project_explains_what_to_do(window: MainWindow, qtbot: QtBot) -> None:
    with qtbot.waitSignal(window.controller.error) as signal:
        window.media_bin.choose_files()
    assert "project first" in signal.args[0]


def test_video_filter_accepts_upper_case_extensions() -> None:
    from easy_video_editor.ui.media_bin import VIDEO_FILTER

    assert "*.mp4" in VIDEO_FILTER and "*.MP4" in VIDEO_FILTER and "All files (*)" in VIDEO_FILTER


def test_the_last_project_is_reopened(window: MainWindow, project_dir: Path) -> None:
    window.controller.settings.recent_projects = [str(project_dir)]
    window.reopen_last_project()
    assert window.controller.project_dir == project_dir


def test_a_missing_last_project_is_skipped(window: MainWindow, tmp_path: Path) -> None:
    window.controller.settings.recent_projects = [str(tmp_path / "Gone.eve")]
    window.reopen_last_project()
    assert window.controller.project is None
