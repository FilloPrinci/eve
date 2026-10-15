"""Command execution, transactions and undo/redo."""

import pytest

from easy_video_editor.core.commands import CreateZone, UpdateZone
from easy_video_editor.core.errors import CommandError
from easy_video_editor.core.model import Project
from easy_video_editor.core.session import ProjectSession

from .conftest import AI, AUTO, BLUE, RED, USER, S, zone


def test_undo_and_redo_restore_exact_states(session: ProjectSession) -> None:
    empty = session.project.model_dump(exclude={"revision"})
    blue = zone(session, BLUE, 0, 5)
    with_zone = session.project.model_dump(exclude={"revision"})
    session.execute(UpdateZone(zone_id=blue, end_us=8 * S), USER)

    session.undo()
    assert session.project.model_dump(exclude={"revision"}) == with_zone
    session.undo()
    assert session.project.model_dump(exclude={"revision"}) == empty
    session.redo()
    session.redo()
    assert session.project.zone(blue).end_us == 8 * S
    assert not session.can_redo


def test_undo_restores_zones_trimmed_by_red(session: ProjectSession) -> None:
    zone(session, BLUE, 0, 30)
    zone(session, RED, 10, 12)
    session.undo()
    assert [(z.start_us, z.end_us) for z in session.project.zones] == [(0, 30 * S)]


def test_new_command_clears_redo(session: ProjectSession) -> None:
    zone(session, BLUE, 0, 5)
    session.undo()
    assert session.can_redo
    zone(session, BLUE, 10, 15)
    assert not session.can_redo


def test_nothing_to_undo() -> None:
    session = ProjectSession(Project(name="Empty"))
    with pytest.raises(CommandError, match="Nothing to undo"):
        session.undo()
    with pytest.raises(CommandError, match="Nothing to redo"):
        session.redo()


def test_revision_increases_on_every_change(session: ProjectSession) -> None:
    revision = session.project.revision
    zone(session, BLUE, 0, 5)
    session.undo()
    session.redo()
    assert session.project.revision == revision + 3


def test_failed_command_changes_nothing(session: ProjectSession) -> None:
    revision, entries = session.project.revision, len(session.history)
    with pytest.raises(CommandError):
        zone(session, RED, 0, 5, AI)
    assert session.project.revision == revision
    assert len(session.history) == entries


def test_transaction_is_one_undo_step_with_finer_steps(session: ProjectSession) -> None:
    entries = len(session.history)
    with session.transaction("Phase 1 (auto)", AUTO):
        zone(session, BLUE, 0, 5, AUTO)
        zone(session, BLUE, 10, 15, AUTO)
    assert len(session.history) == entries + 1
    entry = session.history[-1]
    assert entry.description == "Phase 1 (auto)"
    assert entry.origin is AUTO
    assert entry.steps == ["Create candidate zone", "Create candidate zone"]
    session.undo()
    assert session.project.zones == []


def test_rejected_command_inside_transaction_keeps_it_open(session: ProjectSession) -> None:
    with session.transaction("Phase 1 (ai)", AI):
        zone(session, BLUE, 0, 5, AI)
        with pytest.raises(CommandError, match="Only the user"):
            zone(session, RED, 10, 15, AI)
        zone(session, BLUE, 10, 15, AI)
    assert len(session.project.zones) == 2
    assert all(z.origin is AI for z in session.project.zones)


def test_exception_rolls_back_whole_transaction(session: ProjectSession) -> None:
    entries = len(session.history)
    with pytest.raises(RuntimeError), session.transaction("Phase 2 (ai)", AI):
        zone(session, BLUE, 0, 5, AI)
        raise RuntimeError("cancelled")
    assert session.project.zones == []
    assert len(session.history) == entries


def test_empty_transaction_adds_no_history(session: ProjectSession) -> None:
    entries = len(session.history)
    with session.transaction("Nothing", AI):
        pass
    assert len(session.history) == entries


def test_transaction_rules(session: ProjectSession) -> None:
    with session.transaction("Outer", AI):
        with pytest.raises(CommandError, match="nested"), session.transaction("Inner", AI):
            pass
        with pytest.raises(CommandError, match="origin"):
            session.execute(CreateZone(kind=BLUE, clip_id="clip", start_us=0, end_us=S), USER)
        assert not session.can_undo


def test_command_outside_transaction_needs_origin(session: ProjectSession) -> None:
    with pytest.raises(CommandError, match="origin"):
        session.execute(CreateZone(kind=BLUE, clip_id="clip", start_us=0, end_us=S))


def test_listeners_are_notified(session: ProjectSession) -> None:
    seen: list[int] = []
    unsubscribe = session.subscribe(lambda project: seen.append(project.revision))
    zone(session, BLUE, 0, 5)
    session.undo()
    unsubscribe()
    zone(session, BLUE, 0, 5)
    assert len(seen) == 2


def test_replace_project_clears_history(session: ProjectSession) -> None:
    zone(session, BLUE, 0, 5)
    session.replace_project(Project(name="Other"))
    assert session.project.name == "Other"
    assert not session.can_undo


# --- Changes made by another process (the AI editing the saved project) ------------------------


def _other_process_edit(session: ProjectSession, *edits: tuple[float, float]) -> list[Project]:
    """States written by "another process": a separate session working on a copy."""
    other = ProjectSession(session.project.model_copy(deep=True))
    states = []
    for start, end in edits:
        other.execute(
            CreateZone(kind=BLUE, clip_id="clip", start_us=int(start * S), end_us=int(end * S)),
            AI,
        )
        states.append(other.project.model_copy(deep=True))
    return states


def test_external_changes_become_one_undo_step(session: ProjectSession) -> None:
    before = session.project.model_dump(exclude={"revision"})
    revision = session.project.revision
    first, second = _other_process_edit(session, (0, 5), (10, 15))
    session.begin_external("AI: phase 1", AI)
    session.set_external(first)
    session.set_external(second)
    assert len(session.project.zones) == 2
    assert session.project.revision == revision + 2
    entry = session.end_external()
    assert entry is not None and entry.origin is AI and entry.description == "AI: phase 1"
    session.undo()
    assert session.project.model_dump(exclude={"revision"}) == before
    session.redo()
    assert len(session.project.zones) == 2


def test_local_edits_and_undo_are_paused_during_external_changes(
    session: ProjectSession,
) -> None:
    zone(session, BLUE, 30, 35)
    session.begin_external("AI", AI)
    assert not session.can_undo
    with pytest.raises(CommandError, match="being edited by the AI"):
        zone(session, BLUE, 0, 5)
    with pytest.raises(CommandError):
        session.begin_external("again", AI)
    assert session.end_external() is None  # nothing changed: no history entry
    assert session.can_undo


def test_external_states_must_respect_the_red_zone_rules(session: ProjectSession) -> None:
    zone(session, RED, 10, 20)
    broken = session.project.model_copy(deep=True)
    broken.zones.append(
        broken.zones[0].model_copy(update={"id": "blue", "kind": BLUE, "locked": False})
    )
    session.begin_external("AI", AI)
    with pytest.raises(CommandError):
        session.set_external(broken)
    assert len(session.project.zones) == 1
    session.end_external()
