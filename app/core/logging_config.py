"""
Plain-language version of what this file does:

- correlation_id_var: the "sticky note" — holds this request's ticket
  number, readable from anywhere, without passing it as an argument
  through every function call.

- CorrelationIdFilter: right before any log line gets written, this
  peeks at the sticky note and stamps its value onto the log line
  automatically. Without this, every single logger.info() call in the
  whole project would need to remember to add the correlation ID by
  hand — easy to forget, easy to get wrong.

- JSONFormatter: turns a log line into one JSON object per line,
  instead of a plain sentence — so logs can be searched and filtered
  by a machine (a log viewer, or a command like `grep`/`jq`), not just
  read top-to-bottom by a human.
"""

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

# The "sticky note." Defaults to "-" so logs written OUTSIDE of a
# request (e.g. at app startup, or in the Kafka consumer, which has no
# HTTP request at all) still print something sensible instead of
# crashing or printing "None".
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True  # True means "keep this log line" — we're not filtering anything out, just adding data to it


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> None:
    """
    Called once, at app startup (and separately at consumer worker
    startup, since that's a completely different process). Every
    logger.info(...) anywhere in the codebase after this point will
    automatically produce a JSON line with a correlation ID attached —
    nothing else needs to change at each individual call site.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    handler.addFilter(CorrelationIdFilter())

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]  # replace any default handlers, avoid duplicate log lines
    root_logger.setLevel(level)
