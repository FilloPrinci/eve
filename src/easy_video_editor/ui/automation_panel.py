"""Agent / automation panel (SPEC §3, §5): run the phases and follow what they do.

Auto mode plans each phase locally and applies it as one undo step. AI mode lets the configured
provider (the user's Claude Code by default) edit the saved project through EVE's tools; the panel
follows the project file live, editing is paused meanwhile, and each run becomes one undo step.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.ai.agent import AgentEvent, AgentResult
from easy_video_editor.ai.runner import AiProviderKind, run_ai_phase
from easy_video_editor.ai.tools import ToolPhase
from easy_video_editor.analysis.pipeline import Phase, ensure_features, plan_phase
from easy_video_editor.analysis.plan import Plan
from easy_video_editor.core.jobs import Job, JobContext
from easy_video_editor.ui.controller import ProjectController
from easy_video_editor.ui.jobs import JobRunner

PHASE_TITLES = {
    Phase.ANALYZE: "Phase 1: find candidates",
    Phase.REFINE: "Phase 2: refine highlights",
    Phase.ASSEMBLE: "Phase 3: assemble the edit",
    Phase.REFRESH: "Refresh stale",
}
ALL_PHASES = [Phase.ANALYZE, Phase.REFINE, Phase.ASSEMBLE]
AI_PHASES = {
    Phase.ANALYZE: ToolPhase.ANALYZE,
    Phase.REFINE: ToolPhase.REFINE,
    Phase.ASSEMBLE: ToolPhase.ASSEMBLE,
    Phase.REFRESH: ToolPhase.FOLLOWUP,
}
PROVIDER_NAMES = {
    AiProviderKind.CLAUDE_CODE: "Claude Code (your Claude plan)",
    AiProviderKind.ANTHROPIC: "Anthropic API (your API key)",
}
MODE_AUTO, MODE_AI = "auto", "ai"


class AutomationPanel(QWidget):
    _agentEvent = Signal(object)
    """Agent events, emitted from the worker thread and delivered on the UI thread."""

    def __init__(
        self, controller: ProjectController, runner: JobRunner, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.runner = runner
        self.mode = QComboBox()
        self.mode.addItem(self.tr("Auto (no AI, offline)"), MODE_AUTO)
        self.mode.addItem(self.tr("AI"), MODE_AI)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.provider = QLabel()
        self.provider.setWordWrap(True)
        self.buttons: dict[Phase, QPushButton] = {}
        grid = QGridLayout()
        for index, phase in enumerate([*ALL_PHASES, Phase.REFRESH]):
            button = QPushButton(self.tr(PHASE_TITLES[phase]))
            button.clicked.connect(lambda _=False, p=phase: self.run([p]))
            self.buttons[phase] = button
            grid.addWidget(button, index // 2, index % 2)
        self.run_all = QPushButton(self.tr("Run all phases"))
        self.run_all.clicked.connect(lambda: self.run(list(ALL_PHASES)))
        grid.addWidget(self.run_all, 2, 0, 1, 2)
        self.request = QPlainTextEdit()
        self.request.setPlaceholderText(
            self.tr("Ask the AI for a change, e.g. “make the intro shorter” (AI mode)")
        )
        self.request.setMaximumHeight(70)
        self.ask = QPushButton(self.tr("Ask"))
        self.ask.clicked.connect(self.ask_ai)
        self.stop = QPushButton(self.tr("Stop"))
        self.stop.clicked.connect(self.cancel)
        actions = QHBoxLayout()
        actions.addWidget(self.ask)
        actions.addWidget(self.stop)
        self.usage = QLabel()
        self.usage.setWordWrap(True)
        self.activity = QListWidget()
        self.activity.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Mode")))
        layout.addWidget(self.mode)
        layout.addWidget(self.provider)
        layout.addLayout(grid)
        layout.addWidget(self.request)
        layout.addLayout(actions)
        layout.addWidget(QLabel(self.tr("Activity")))
        layout.addWidget(self.activity, 1)
        layout.addWidget(self.usage)
        self._running = False
        self._job: Job | None = None
        self._agentEvent.connect(self._on_agent_event)
        self.update_mode()

    @property
    def ai_mode(self) -> bool:
        return bool(self.mode.currentData() == MODE_AI)

    def set_mode(self, mode: str) -> None:
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))

    def log(self, message: str) -> None:
        self.activity.addItem(message)
        self.activity.scrollToBottom()

    def update_mode(self, *_: object) -> None:
        kind = self.controller.settings.ai.provider
        self.provider.setText(
            self.tr("AI provider: {name}. Change it in Preferences.").replace(
                "{name}", self.tr(PROVIDER_NAMES[kind])
            )
            if self.ai_mode
            else ""
        )
        self.provider.setVisible(self.ai_mode)
        self._update_buttons()

    def _update_buttons(self) -> None:
        for button in [*self.buttons.values(), self.run_all]:
            button.setEnabled(not self._running)
        self.mode.setEnabled(not self._running)
        self.request.setEnabled(self.ai_mode and not self._running)
        self.ask.setEnabled(self.ai_mode and not self._running)
        self.stop.setEnabled(self._running)

    def _set_running(self, running: bool) -> None:
        self._running = running
        if not running:
            self._job = None
        self._update_buttons()

    def cancel(self) -> None:
        if self._job is not None:
            self.runner.cancel(self._job.id)

    def run(self, phases: list[Phase], then: Callable[[], None] | None = None) -> None:
        """Run ``phases`` one after the other; each one is a single undo step."""
        if self.controller.project is None or self._running:
            return
        if not phases:
            self._finish(then)
            return
        if self.ai_mode:
            self._run_ai(AI_PHASES[phases[0]], None, phases[1:], then, PHASE_TITLES[phases[0]])
        else:
            self._run_auto(phases[0], phases[1:], then)

    def ask_ai(self) -> None:
        request = self.request.toPlainText().strip()
        if not request or self.controller.project is None or self._running:
            return
        self._run_ai(ToolPhase.FOLLOWUP, request, [], None, "AI request")

    # --- Auto mode -----------------------------------------------------------------------------

    def _run_auto(self, phase: Phase, rest: list[Phase], then: Callable[[], None] | None) -> None:
        project, cache = self.controller.project, self.controller.cache
        if project is None or cache is None:
            return
        self._set_running(True)
        snapshot = project.model_copy(deep=True)
        brand = self.controller.brand_kit()
        height = self.controller.settings.proxy_height
        self.log(self.tr("Started: {phase}").replace("{phase}", self.tr(PHASE_TITLES[phase])))

        def work(ctx: JobContext) -> Plan:
            return plan_phase(phase, snapshot, cache, self.controller.toolchain(), brand,
                              ctx.progress, ctx.token, height)  # fmt: skip

        def done(plan: Plan) -> None:
            for message in plan.messages:
                self.log(message)
            self.controller.apply_plan(plan)
            if phase is Phase.ASSEMBLE:
                self._show_first_timeline()
            self._continue(rest, then)

        self._job = self.runner.run(
            self.tr(PHASE_TITLES[phase]),
            work,
            on_success=done,
            on_error=self._failed,
            on_cancel=self._cancelled,
        )

    # --- AI mode -------------------------------------------------------------------------------

    def _run_ai(
        self,
        phase: ToolPhase,
        request: str | None,
        rest: list[Phase],
        then: Callable[[], None] | None,
        title: str,
    ) -> None:
        project, cache, directory = (
            self.controller.project,
            self.controller.cache,
            self.controller.project_dir,
        )
        if project is None or cache is None or directory is None:
            return
        self._set_running(True)
        snapshot = project.model_copy(deep=True)
        settings = self.controller.settings.ai.model_copy(deep=True)
        height = self.controller.settings.proxy_height
        title = self.tr(title)
        self.log(self.tr("Started (AI): {phase}").replace("{phase}", title))
        self.controller.begin_external(f"AI: {title}")

        def work(ctx: JobContext) -> AgentResult:
            toolchain = self.controller.toolchain()
            ensure_features(snapshot, cache, toolchain, ctx.progress, ctx.token, height)
            ctx.progress(None, "The AI is working")
            return run_ai_phase(
                phase,
                directory,
                settings,
                self._agentEvent.emit,
                ctx.token,
                request=request,
                toolchain=toolchain,
            )

        def done(result: AgentResult) -> None:
            self.controller.end_external()
            self._show_usage(result)
            if result.summary:
                self.log(result.summary)
            if result.error:
                self._failed(result.error)
                return
            if phase is ToolPhase.ASSEMBLE:
                self._show_first_timeline()
            if request is not None:
                self.request.clear()
            self._continue(rest, then)

        def failed(message: str) -> None:
            self.controller.end_external()
            self._failed(message)

        def cancelled() -> None:
            self.controller.end_external()
            self._cancelled()

        self._job = self.runner.run(
            title, work, on_success=done, on_error=failed, on_cancel=cancelled
        )

    def _on_agent_event(self, event: AgentEvent) -> None:
        prefix = {"tool": "→ ", "error": "✗ "}.get(event.kind, "")
        self.log(prefix + event.message)
        self.controller.follow_external()

    def _show_usage(self, result: AgentResult) -> None:
        usage = result.usage
        text = self.tr("Last AI run: {steps} step(s), {inp} input and {out} output tokens.")
        text = (
            text.replace("{steps}", str(result.steps))
            .replace("{inp}", f"{usage.input_tokens:,}")
            .replace("{out}", f"{usage.output_tokens:,}")
        )
        if self.controller.settings.ai.provider is AiProviderKind.CLAUDE_CODE:
            text += " " + self.tr("Counted against your Claude plan's usage limits.")
        elif usage.cost_usd is not None:
            text += " " + self.tr("Estimated cost: ${cost}.").replace(
                "{cost}", f"{usage.cost_usd:.2f}"
            )
        self.usage.setText(text)

    # --- Shared --------------------------------------------------------------------------------

    def _show_first_timeline(self) -> None:
        project = self.controller.project
        if project and project.timelines:
            self.controller.set_current_timeline(project.timelines[0].id)

    def _continue(self, rest: list[Phase], then: Callable[[], None] | None) -> None:
        self._running = False
        if rest:
            self.run(rest, then)
        else:
            self._finish(then)

    def _failed(self, message: str) -> None:
        self.log(self.tr("Stopped: {error}").replace("{error}", message))
        self.controller.error.emit(message)
        self._set_running(False)

    def _cancelled(self) -> None:
        self.log(self.tr("Cancelled."))
        self._set_running(False)

    def _finish(self, then: Callable[[], None] | None) -> None:
        self._set_running(False)
        self.log(self.tr("Done."))
        if then:
            then()
