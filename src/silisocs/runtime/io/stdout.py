"""Stdout logging redirection."""

import logging
import sys


class StdoutToLogger:
    """File-like stdout redirector backed by a logger."""

    def __init__(self, logger: logging.Logger, log_level: int = logging.INFO) -> None:
        self.logger = logger
        self.log_level = log_level

    def write(self, buf: str) -> None:
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

    def flush(self) -> None:
        return None


def configure_logging(logger: logging.Logger) -> None:
    """Redirect stdout to the provided logger and quiet noisy libraries."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.stdout = StdoutToLogger(logger)
