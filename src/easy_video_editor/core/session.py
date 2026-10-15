"""Command execution, transactions and undo/redo (SPEC §7.3).

Undo/redo is snapshot-based: each history entry stores the project before and after it.
Projects are small (no media data), so this is cheap and makes undo exact by construction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from pydantic import ValidationError

from easy_video_editor.core.commands.base import Command, CommandContext, CommandResult
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.ids import new_id
from easy_video_editor.core.model import Origin, Project
from easy_video_editor.core.rules import check_invariants


@dataclass
class HistoryEntry:
    description: str
    origin: Origin
    before: Project
    after: Project
    steps: list[str] = field(default_factory=list)
    """Descriptions of the commands inside a transaction (finer steps)."""
    notices: list[str] = field(default_factory=list)


@dataclass
class _Transaction:
    description: str
    origin: Origin
    before: Project
    steps: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


Listener = Callable[[Project], None]


class ProjectSession:
    """Owns the current project and its history. The only way to change a project.

    The `project` property returns the live model: treat it as read-only.
    """

    def __init__(self, project: Project, id_factory: Callable[[str], str] = new_id) -> None:
        self._project = project
        self._id_factory = id_factory
        self._undo: list[HistoryEntry] = []
        self._redo: list[HistoryEntry] = []
        self._transaction: _Transaction | None = None
        self._listeners: list[Listener] = []

    @property
    def project(self) -> Project:
        return self._project

    @property
    def history(self) -> list[HistoryEntry]:
        """Undoable entries, oldest first."""
        return list(self._undo)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo) and self._transaction is None

    @property
    def can_redo(self) -> bool:
        return bool(self._redo) and self._transaction is None

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` after every change. Returns a function that unsubscribes it."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # --- Execution -----------------------------------------------------------------------------

    def execute(self, command: Command, origin: Origin | None = None) -> CommandResult:
        """Apply ``command`` atomically, or raise `CommandError` leaving the project unchanged.

        Inside a transaction the origin defaults to the transaction's origin.
        """
        if self._transaction is not None:
            if origin is not None and origin is not self._transaction.origin:
                raise CommandError("A command's origin must match its transaction's origin.")
            origin = self._transaction.origin
        if origin is None:
            raise CommandError("A command needs an origin outside a transaction.")

        before = self._project
        work = before.model_copy(deep=True)
        result = command.apply(work, CommandContext(origin=origin, id_factory=self._id_factory))
        work = self._validated(work)
        work.revision = before.revision + 1
        self._project = work

        if self._transaction is not None:
            self._transaction.steps.append(command.describe())
            self._transaction.notices.extend(result.notices)
        else:
            self._push(
                HistoryEntry(command.describe(), origin, before, work, notices=result.notices)
            )
        self._notify()
        return result

    @contextmanager
    def transaction(self, description: str, origin: Origin) -> Iterator[None]:
        """Group commands into one undo step.

        A rejected command inside the transaction leaves the transaction open (callers such as
        the AI agent may continue); an exception escaping the block rolls the whole group back.
        """
        if self._transaction is not None:
            raise CommandError("Transactions cannot be nested.")
        self._transaction = _Transaction(description, origin, self._project)
        try:
            yield
        except BaseException:
            transaction, self._transaction = self._transaction, None
            if self._project is not transaction.before:
                self._project = transaction.before.model_copy(deep=True)
                self._project.revision = transaction.before.revision + 1
                self._notify()
            raise
        transaction, self._transaction = self._transaction, None
        if self._project is not transaction.before:
            self._push(
                HistoryEntry(
                    transaction.description,
                    transaction.origin,
                    transaction.before,
                    self._project,
                    steps=transaction.steps,
                    notices=transaction.notices,
                )
            )

    def undo(self) -> HistoryEntry:
        if not self.can_undo:
            raise CommandError("Nothing to undo.")
        entry = self._undo.pop()
        self._redo.append(entry)
        self._restore(entry.before)
        return entry

    def redo(self) -> HistoryEntry:
        if not self.can_redo:
            raise CommandError("Nothing to redo.")
        entry = self._redo.pop()
        self._undo.append(entry)
        self._restore(entry.after)
        return entry

    def replace_project(self, project: Project) -> None:
        """Load a different project state (e.g. reloaded from disk) and clear the history."""
        if self._transaction is not None:
            raise CommandError("Cannot replace the project during a transaction.")
        self._project = project
        self._undo.clear()
        self._redo.clear()
        self._notify()

    # --- Internals -----------------------------------------------------------------------------

    @staticmethod
    def _validated(project: Project) -> Project:
        try:
            project = Project.model_validate(project.model_dump())
        except ValidationError as error:
            raise CommandError(f"Invalid change: {error}") from error
        problems = check_invariants(project)
        if problems:
            raise CommandError(" ".join(problems))
        return project

    def _push(self, entry: HistoryEntry) -> None:
        self._undo.append(entry)
        self._redo.clear()

    def _restore(self, snapshot: Project) -> None:
        revision = self._project.revision + 1
        self._project = snapshot.model_copy(deep=True)
        self._project.revision = revision
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self._project)
