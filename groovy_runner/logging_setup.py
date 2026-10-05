from __future__ import annotations

"""Lightweight rotating file logger.

Streamlit only prints to stdout, which is easy to lose. Every Groovy run
and preset change flows through this logger so the Diagnostics page's
"Download logs" button has something real to hand over.
"""

import logging
import logging.handlers
from pathlib import Path

from .utils import harden_file_permissions

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_PATH = LOG_DIR / "groovy-runner.log"

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("groovy_runner")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.handlers.RotatingFileHandler(
                LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            harden_file_permissions(LOG_DIR, mode=0o700)
            harden_file_permissions(LOG_PATH)
        except OSError:
            # Read-only filesystem or similar — fall back to an in-memory
            # no-op handler rather than crashing the app over logging.
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    _logger = logger
    return logger
