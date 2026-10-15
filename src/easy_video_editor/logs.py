"""Logging setup (SPEC §12): a rotating file in the user log folder plus the console."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import platformdirs

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"
LOG_FILE_NAME = "eve.log"


def default_log_dir() -> Path:
    return Path(platformdirs.user_log_dir("EVE", appauthor=False))


def setup_logging(console_level: int = logging.WARNING, log_dir: Path | None = None) -> Path | None:
    """Configure the ``easy_video_editor`` logger. Returns the log file path (None if unavailable).

    Logs must never contain secrets (API keys) or full base64 images.
    """
    logger = logging.getLogger("easy_video_editor")
    logger.setLevel(logging.DEBUG)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console)

    directory = log_dir or default_log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / LOG_FILE_NAME
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
    except OSError as error:
        logger.warning("Cannot write the log file in %s: %s", directory, error)
        return None
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(file_handler)
    return path
