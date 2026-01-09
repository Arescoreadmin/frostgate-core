import os
import sys

from loguru import logger


def configure_logging() -> None:
    """
    Configure loguru to emit structured JSON logs to stdout.

    Fields include:
      - time, level, message
      - module, function, line
      - any `extra={...}` fields from logger calls
    """
    logger.remove()

    log_level = os.getenv("FG_LOG_LEVEL", "INFO").upper()

    logger.add(
        sys.stdout,
        level=log_level,
        serialize=True,  # JSON output
        backtrace=False,
        diagnose=False,
    )
