"""Agent / automation panel (SPEC §3, §5): run the phases and follow what they do."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from easy_video_editor.analysis.pipeline import Phase, plan_phase
from easy_video_editor.analysis.plan import Plan
from easy_video_editor.core.jobs import JobContext
from easy_video_editor.ui.controller import ProjectController
from easy_video_editor.ui.jobs import JobRunner

PHASE_TITLES = {
    Phase.ANALYZE: "Phase 1: find candidates",
    Phase.REFINE: "Phase 2: refine highlights",
    Phase.ASSEMBLE: "Phase 3: assemble the edit",
    Phase.REFRESH: "Refresh stale",
}
ALL_PHASES = [Phase.ANALYZE, Phase.REFINE, Phase.ASSEMBLE]


class AutomationPanel(QWidget):
    def __init__(
        self, controller: ProjectController, runner: JobRunner, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.runner = runner
        self.mode = QComboBox()
        self.mode.addItem(self.tr("Auto (no AI, offline)"), "auto")
        self.mode.addItem(self.tr("AI (available in a later version)"), "ai")
        self.mode.model().item(1).setEnabled(False)  # type: ignore[attr-defined]
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
        self.activity = QListWidget()
        self.activity.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Mode")))
        layout.addWidget(self.mode)
        layout.addLayout(grid)
        layout.addWidget(QLabel(self.tr("Activity")))
        layout.addWidget(self.activity, 1)
        self._running = False

    def log(self, message: str) -> None:
        self.activity.addItem(message)
        self.activity.scrollToBottom()

    def _set_running(self, running: bool) -> None:
        self._running = running
        for button in [*self.buttons.values(), self.run_all]:
            button.setEnabled(not running)

    def run(self, phases: list[Phase], then: Callable[[], None] | None = None) -> None:
        """Run ``phases`` one after the other; each is planned in the background and applied as
        one undo step."""
        project, cache = self.controller.project, self.controller.cache
        if project is None or cache is None or self._running:
            return
        if not phases:
            self._set_running(False)
            if then:
                then()
            return
        phase, rest = phases[0], phases[1:]
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
            if (
                phase is Phase.ASSEMBLE
                and self.controller.project
                and self.controller.project.timelines
            ):
                self.controller.set_current_timeline(self.controller.project.timelines[0].id)
            self._running = False
            self.run(rest, then) if rest else self._finish(then)

        def failed(message: str) -> None:
            self.log(self.tr("Stopped: {error}").replace("{error}", message))
            self.controller.error.emit(message)
            self._set_running(False)

        self.runner.run(self.tr(PHASE_TITLES[phase]), work, on_success=done, on_error=failed)

    def _finish(self, then: Callable[[], None] | None) -> None:
        self._set_running(False)
        self.log(self.tr("Done."))
        if then:
            then()
