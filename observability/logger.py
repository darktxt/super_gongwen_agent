from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, TextIO

from .events import sanitize_sensitive_payload


def build_app_logger(
    name: str = "super_gongwen",
    *,
    level: int = logging.INFO,
    stream: TextIO | None = None,
    console_enabled: bool | None = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    effective_console_enabled = (
        _read_console_enabled_flag()
        if console_enabled is None
        else bool(console_enabled)
    )
    if effective_console_enabled:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
    else:
        handler = logging.NullHandler()
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


def _read_console_enabled_flag() -> bool:
    raw_value = str(os.getenv("SUPER_GONGWEN_CONSOLE_LOG", "") or "").strip().lower()
    return raw_value in {"1", "true", "yes", "y", "on"}
