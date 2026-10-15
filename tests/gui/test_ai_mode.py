"""AI mode in the Agent panel, with a scripted model (no real AI calls)."""

from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from easy_video_editor.ai import runner
from easy_video_editor.ai.agent import AgentError, AgentResult, FakeProvider
from easy_video_editor.ai.tools import ToolPhase
from easy_video_editor.analysis.pipeline import Phase
from easy_video_editor.core.model import Origin
from easy_video_editor.ui import automation_panel
from easy_video_editor.ui.main_window import MainWindow


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace the configured provider with a scripted one; returns the recorded calls."""
    calls: list[Any] = []

    def fake_run(phase: Any, project_dir: Path, settings: Any, on_event: Any, token: Any,
                 request: str | None = None, toolchain: Any = None) -> AgentResult:  # fmt: skip
        calls.append((phase, request))
        provider = FakeProvider(
            [
                [("tool", "create_candidate_zone",
                  {"clip_id": "clip", "start_s": 1, "end_s": 3, "label": "Start"})],
                [("tool", "create_candidate_zone",
                  {"clip_id": "clip", "start_s": 10, "end_s": 12, "label": "Middle"})],
                [("text", "Marked two moments.")],
            ]
        )  # fmt: skip
        return runner.run_ai_phase(phase, project_dir, settings, on_event, token, request,
                                   toolchain, provider)  # fmt: skip

    monkeypatch.setattr(automation_panel, "run_ai_phase", fake_run)
    return calls


def test_ai_phase_is_followed_live_and_undone_in_one_step(
    opened: MainWindow, qtbot: QtBot, scripted: list[Any]
) -> None:
    panel = opened.automation
    panel.set_mode("ai")
    assert panel.ask.isEnabled()
    panel.run([Phase.ANALYZE])
    assert opened.controller.external_active and not panel.run_all.isEnabled()
    qtbot.waitUntil(lambda: not panel._running, timeout=20_000)

    project = opened.controller.project
    assert project is not None
    assert sorted(z.label for z in project.zones if z.origin is Origin.AI) == ["Middle", "Start"]
    assert not opened.controller.external_active
    texts = [panel.activity.item(i).text() for i in range(panel.activity.count())]
    assert "Marked two moments." in texts and any(t.startswith("→ ") for t in texts)
    assert "tokens" in panel.usage.text()
    session = opened.controller.session
    assert session is not None and session.history[-1].description.startswith("AI: ")

    opened.controller.undo()
    project = opened.controller.project
    assert project is not None and not [z for z in project.zones if z.origin is Origin.AI]


def test_ask_sends_the_request(opened: MainWindow, qtbot: QtBot, scripted: list[Any]) -> None:
    panel = opened.automation
    panel.set_mode("ai")
    panel.request.setPlainText("Find the start")
    panel.ask_ai()
    qtbot.waitUntil(lambda: not panel._running, timeout=20_000)
    assert scripted == [(ToolPhase.FOLLOWUP, "Find the start")]
    assert panel.request.toPlainText() == ""


def test_provider_errors_are_shown(
    opened: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*args: Any, **kwargs: Any) -> AgentResult:
        raise AgentError("Claude Code was not found.")

    monkeypatch.setattr(automation_panel, "run_ai_phase", failing)
    panel = opened.automation
    panel.set_mode("ai")
    with qtbot.waitSignal(opened.controller.error, timeout=20_000) as signal:
        panel.run([Phase.REFINE])
    assert "not found" in signal.args[0]
    qtbot.waitUntil(lambda: not panel._running)
    assert not opened.controller.external_active and opened.controller.session is not None
    assert opened.controller.session.can_undo or not opened.controller.session.history
