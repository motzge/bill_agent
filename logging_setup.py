"""Logging setup for bill_agent.

One call at startup wires the root logger: readable console output for the
operator, detailed rotating file output for debugging. Every record carries
the run ID so a support request ("Fehler, Referenz abc123") can be matched
to the exact run in the log file.
"""

from __future__ import annotations

import logging
import uuid
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR

LOG_FILE = LOGS_DIR / "bill_agent.log"

CONSOLE_FORMAT = "%(levelname)-8s %(message)s"
FILE_FORMAT = "%(asctime)s | %(run_id)s | %(levelname)-8s | %(name)s | %(message)s"

MAX_LOG_BYTES = 1_000_000  # ~1 MB per file
BACKUP_COUNT = 5  #keep bill_agent.log.1 .. .5, oldest gets dropped

#Third-party libs that spam DEBUG. capped at WARNING in our log file
NOISY_LOGGERS = ("pdfminer", "urllib3", "PIL")


def new_run_id() -> str:
    """Short unique ID for one program run, e.g. 'a3f9c1d2'."""
    return uuid.uuid4().hex[:8]


class _RunIdFilter(logging.Filter):
    """Stamps the current run ID onto every log record."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True  #never drops records, only annotates them


def setup_logging(run_id: str) -> None:
    """Configure the root logger. Call once, after ensure_directories()."""
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))

    log_file = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_LOG_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    log_file.setLevel(logging.DEBUG)
    log_file.setFormatter(logging.Formatter(FILE_FORMAT))

    run_filter = _RunIdFilter(run_id)
    root = logging.getLogger()
    root.handlers.clear()  #idempotent setup: no duplicate output on re-init
    root.setLevel(logging.DEBUG)
    for handler in (console, log_file):
        handler.addFilter(run_filter)
        root.addHandler(handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)