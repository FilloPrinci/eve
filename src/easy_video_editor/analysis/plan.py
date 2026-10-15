"""Automation plans: lists of commands computed from a snapshot, then applied in one transaction.

Phases compute their plan off the UI thread (pure functions of the project and the features);
applying it is one undo step whose finer steps are the individual commands (SPEC §7.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from easy_video_editor.core.commands import Command, CommandResult
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.model import Origin
from easy_video_editor.core.session import ProjectSession

log = logging.getLogger(__name__)


@dataclass
class Plan:
    description: str
    commands: list[Command] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    """Plain-English progress notes for the activity panel."""
    optional: set[int] = field(default_factory=set)
    """Indexes of commands whose rejection is expected and harmless (e.g. a transition
    that would need excluded frames)."""

    def add(self, command: Command, optional: bool = False) -> None:
        if optional:
            self.optional.add(len(self.commands))
        self.commands.append(command)


@dataclass
class PlanResult:
    results: list[CommandResult] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def apply_plan(session: ProjectSession, plan: Plan, origin: Origin = Origin.AUTO) -> PlanResult:
    outcome = PlanResult()
    if not plan.commands:
        return outcome
    with session.transaction(plan.description, origin):
        for index, command in enumerate(plan.commands):
            try:
                result = session.execute(command)
            except CommandError as error:
                if index not in plan.optional:
                    log.warning("%s: %s rejected: %s", plan.description, command.describe(), error)
                    outcome.rejected.append(f"{command.describe()}: {error}")
                continue
            outcome.results.append(result)
            outcome.notices.extend(result.notices)
    return outcome
