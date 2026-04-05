from __future__ import annotations

from dataclasses import fields, is_dataclass
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from session_storage.history import append_event, write_debug_json


REDACTED_VALUE = "***REDACTED***"
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "openai_api_key",
    "x_api_key",
}


def sanitize_sensitive_payload(
    value: Any,
    *,
    max_string_length: int | None = 1000,
) -> Any:
    if hasattr(value, "to_dict"):
        return sanitize_sensitive_payload(
            value.to_dict(),
            max_string_length=max_string_length,
        )

    if is_dataclass(value):
        return sanitize_sensitive_payload(
            {
                field.name: getattr(value, field.name)
                for field in fields(value)
            },
            max_string_length=max_string_length,
        )

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SENSITIVE_KEYS:
                sanitized[key_text] = REDACTED_VALUE
                continue
            sanitized[key_text] = sanitize_sensitive_payload(
                item,
                max_string_length=max_string_length,
            )
        return sanitized

    if isinstance(value, list):
        return [
            sanitize_sensitive_payload(item, max_string_length=max_string_length)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            sanitize_sensitive_payload(item, max_string_length=max_string_length)
            for item in value
        ]

    if isinstance(value, str):
        if max_string_length is None or len(value) <= max_string_length:
            return value
        return value[:max_string_length].rstrip() + "...[truncated]"

    return value


@dataclass(slots=True)
class ObservabilityEventWriter:
    app_home: Path | None = None
    enabled: bool = True
    max_string_length: int = 2000

    def record(
        self,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Path | None:
        if not self.enabled:
            return None

        sanitized_payload = sanitize_sensitive_payload(
            dict(payload or {}),
            max_string_length=self.max_string_length,
        )
        return append_event(
            session_id=session_id,
            event={
                "session_id": session_id,
                "event_type": event_type,
                "payload": sanitized_payload,
            },
            app_home=self.app_home,
        )

    def write_debug_json(
        self,
        session_id: str,
        filename: str,
        payload: Any,
    ) -> Path | None:
        if not self.enabled:
            return None

        sanitized_payload = sanitize_sensitive_payload(
            payload,
            max_string_length=None,
        )
        return write_debug_json(
            session_id=session_id,
            filename=filename,
            payload=sanitized_payload,
            app_home=self.app_home,
        )
