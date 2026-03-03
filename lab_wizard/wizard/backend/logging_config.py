from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def configure_wizard_logging(
    *,
    logs_dir: Path,
    debug: bool | None = None,
) -> Path:
    """Configure process-wide logging for lab_wizard.

    - File logs: DEBUG+ with daily rotation, 14 backups.
    - Console logs: WARNING+ by default, DEBUG+ in debug mode.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "wizard.log"

    if debug is None:
        debug = os.environ.get("LAB_WIZARD_LOG_LEVEL", "").strip().upper() == "DEBUG"

    logger = logging.getLogger("lab_wizard")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Avoid duplicate handlers on reload/tests.
    if logger.handlers:
        return log_file

    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return log_file
