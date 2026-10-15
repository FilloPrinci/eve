import json
import shutil
from pathlib import Path

import pytest

from easy_video_editor.cli.main import EXIT_ERROR, EXIT_OK, main
from easy_video_editor.core.persistence import load_project
from easy_video_editor.media.cache import MediaCache


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert main(["new", str(tmp_path), "--name", "Demo"]) == EXIT_OK
    return tmp_path / "Demo.eve"


def test_new_creates_project(project: Path) -> None:
    assert json.loads((project / "project.json").read_text(encoding="utf-8"))["name"] == "Demo"
    assert main(["new", str(project.parent), "--name", "Demo"]) == EXIT_ERROR


def test_import_generates_media_and_skips_duplicates(
    project: Path, media_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clip_path = media_dir / "clip.mp4"
    assert main(["import", str(project), str(clip_path)]) == EXIT_OK
    clips = load_project(project).clips
    assert [clip.name for clip in clips] == ["clip.mp4"]
    status = MediaCache(project).status(clips[0])
    assert status.proxy_ready and status.thumbnails_ready and status.waveform_ready

    assert main(["import", str(project), str(clip_path), "--no-media"]) == EXIT_OK
    assert "already in the project" in capsys.readouterr().err
    assert len(load_project(project).clips) == 1


def test_import_reports_errors(project: Path, tmp_path: Path) -> None:
    assert main(["import", str(project), str(tmp_path / "missing.mp4")]) == EXIT_ERROR


def test_relink_finds_moved_file(project: Path, media_dir: Path, tmp_path: Path) -> None:
    original = shutil.copy(media_dir / "clip.mp4", tmp_path / "clip.mp4")
    assert main(["import", str(project), str(original), "--no-media"]) == EXIT_OK
    moved = tmp_path / "archive" / "clip.mp4"
    moved.parent.mkdir()
    Path(original).rename(moved)

    assert main(["relink", str(project), "--search", str(tmp_path / "nowhere")]) == EXIT_ERROR
    assert main(["relink", str(project), "--search", str(tmp_path / "archive")]) == EXIT_OK
    assert load_project(project).clips[0].file_path == moved.resolve()


def test_doctor_finds_ffmpeg(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "[OK  ] ffmpeg" in output and "[OK  ] ffprobe" in output


def test_ai_mode_and_ask_use_the_configured_provider(
    project: Path,
    media_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from typing import Any

    from easy_video_editor import appsettings
    from easy_video_editor.ai import runner
    from easy_video_editor.ai.agent import AgentEvent, AgentResult, Usage
    from easy_video_editor.ai.tools import ToolPhase

    monkeypatch.setattr(appsettings, "config_dir", lambda: project.parent / "config")
    calls: list[tuple[Any, ...]] = []

    def fake_run(phase: ToolPhase, project_dir: Path, settings: Any, on_event: Any,
                 request: str | None = None, toolchain: Any = None) -> AgentResult:  # fmt: skip
        calls.append((phase, request, settings.provider))
        on_event(AgentEvent("tool", "get_project_state()"))
        return AgentResult("All done.", Usage(120, 30), 2)

    monkeypatch.setattr(runner, "run_ai_phase", fake_run)
    assert main(["import", str(project), str(media_dir / "clip.mp4"), "--no-media"]) == EXIT_OK
    assert main(["analyze", str(project), "--mode", "ai"]) == EXIT_OK
    assert main(["refresh", str(project), "--mode", "ai", "--provider", "anthropic"]) == EXIT_OK
    assert main(["ask", str(project), "Shorter intro"]) == EXIT_OK
    assert [(phase.value, request, provider.value) for phase, request, provider in calls] == [
        ("analyze", None, "claude_code"),
        ("followup", None, "anthropic"),
        ("followup", "Shorter intro", "claude_code"),
    ]
    captured = capsys.readouterr()
    assert "→ get_project_state()" in captured.out
    assert "Tokens: 120 in, 30 out." in captured.err


def test_relative_paths_work_end_to_end(
    media_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg renders in a work folder: relative project paths must still find the proxies."""
    shutil.copy(media_dir / "bursts.mp4", tmp_path / "bursts.mp4")
    monkeypatch.chdir(tmp_path)
    assert main(["new", ".", "--name", "Rel"]) == EXIT_OK
    assert main(["import", "Rel.eve", "bursts.mp4"]) == EXIT_OK
    for command in ("analyze", "refine", "assemble"):
        assert main([command, "Rel.eve"]) == EXIT_OK
    assert main(["render", "Rel.eve", "--draft"]) == EXIT_OK
    assert list((tmp_path / "Rel.eve" / "exports").glob("*-draft.mp4"))
