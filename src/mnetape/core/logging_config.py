"""Application logging configuration.

Call setup_logging() once at startup to initialize the root logger.
The log level can be controlled via the EEG_UI_LOG_LEVEL environment variable.
"""

from __future__ import annotations

import logging
import os


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger for the application.

    Does nothing if handlers are already attached, so it is safe to call multiple times.

    The effective level is resolved in priority order:
    1. The level argument.
    2. The EEG_UI_LOG_LEVEL environment variable.
    3. Default: INFO.

    Args:
        level: Optional log level name.
            Case-insensitive. Falls back to the environment variable or INFO when None.
    """

    if logging.getLogger().handlers:
        return

    env_level = (level or os.getenv("EEG_UI_LOG_LEVEL", "INFO")).upper()
    resolved = getattr(logging, env_level, logging.INFO)

    logging.basicConfig(
        level=resolved,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
