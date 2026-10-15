"""A project folder that AI tools work on (shared by the MCP server and the API agent).

Before every tool call the project is reloaded if it changed on disk; after every change it is saved
atomically, so the GUI (and any other client) can follow along.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from easy_video_editor.ai.tools import ToolContext, ToolPhase, ToolResult, call_tool
from easy_video_editor.analysis.pipeline import ensure_beats
from easy_video_editor.core.errors import EveError
from easy_video_editor.core.persistence import load_project, save_project
from easy_video_editor.core.session import ProjectSession
from easy_video_editor.media.binaries import Toolchain
from easy_video_editor.media.cache import MediaCache

log = logging.getLogger(__name__)


class Workspace:
    def __init__(
        self,
        project_dir: Path,
        phase: ToolPhase,
        toolchain: Toolchain | None = None,
        frame_budget: int | None = None,
        log_activity: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.project_dir = project_dir
        self.phase = phase
        if toolchain is None:
            try:
                toolchain = Toolchain.discover()
            except EveError as error:
                log.warning("No ffmpeg: %s", error)
        session = ProjectSession(load_project(project_dir))
        beats = None
        if toolchain is not None:
            try:
                beats = ensure_beats(session.project, MediaCache(project_dir), toolchain)
            except EveError as error:
                log.warning("Beat detection failed: %s", error)
        self.context = ToolContext(session, project_dir, toolchain, beats, log=log_activity)
        if frame_budget is not None:
            self.context.frame_budget = frame_budget

    def _refresh(self) -> None:
        disk = load_project(self.project_dir)
        if disk != self.context.project:
            self.context.session.replace_project(disk)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool against the latest saved project and save any change. Raises `ToolError`."""
        self._refresh()
        before = self.context.project.revision
        try:
            return call_tool(self.context, self.phase, name, arguments)
        finally:
            if self.context.project.revision != before:
                save_project(self.context.project, self.project_dir)
