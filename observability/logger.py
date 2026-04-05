from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

from .events import sanitize_sensitive_payload


def build_app_logger(
    name: str = "super_gongwen",
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_structured(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    payload = {"event": event}
    payload.update(sanitize_sensitive_payload(fields))
    logger.log(level, json.dumps(payload, ensure_ascii=False))
