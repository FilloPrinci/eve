from pytestqt.qtbot import QtBot

from easy_video_editor.ui.main_window import MainWindow


def test_main_window_opens(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()
    assert "EVE" in window.windowTitle()
