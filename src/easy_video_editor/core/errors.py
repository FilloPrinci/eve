"""Exceptions raised by the core layer."""

from __future__ import annotations


class EveError(Exception):
    """Base class for all EVE errors."""


class CommandError(EveError):
    """A command was rejected.

    The message is user-facing and is returned to the AI as a tool error.
    """


class NotFoundError(CommandError):
    """A command referenced an item that does not exist."""


class ProjectFormatError(EveError):
    """A project file is missing, unreadable, invalid or from an unsupported schema version."""
