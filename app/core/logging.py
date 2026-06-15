import logging
import sys
import uuid
from app.config import settings


class RequestIDFilter(logging.Filter):
    """Inject request_id into log records when set on the current context."""

    def __init__(self):
        super().__init__()
        self._request_id = ""

    def set_request_id(self, request_id: str) -> None:
        self._request_id = request_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = self._request_id or "-"
        return True


_request_id_filter = RequestIDFilter()


def get_request_id_filter() -> RequestIDFilter:
    return _request_id_filter


class JSONFormatter(logging.Formatter):
    """Outputs structured JSON log records."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            log_entry.update(record.extra)
        return json.dumps(log_entry, default=str)


def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    if settings.debug and settings.log_level == "INFO":
        level = logging.DEBUG

    root = logging.getLogger()
    root.setLevel(level)
    root.addFilter(_request_id_filter)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_request_id_filter)

    if settings.log_format == "json":
        fmt = JSONFormatter()
    else:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s")

    handler.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
