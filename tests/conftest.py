import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real AI calls in tests (CLAUDE.md rule 9): the runner never finds the user's Claude Code
    or API key. Tests inject `FakeProvider` or a stand-in `claude` instead."""
    from easy_video_editor.ai import runner

    monkeypatch.setattr(runner, "find_claude", lambda override=None: None)
    monkeypatch.setattr(runner, "get_api_key", lambda provider: None)
