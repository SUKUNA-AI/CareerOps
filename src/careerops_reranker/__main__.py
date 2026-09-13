from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .config import RerankerRuntimeConfig
from .server import serve


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, dict):
            payload.update(event_fields)
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> int:
    _configure_logging()
    return serve(RerankerRuntimeConfig.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
