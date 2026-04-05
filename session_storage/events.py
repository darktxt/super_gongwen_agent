from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from utils.clock import utc_now_iso
from utils.serialization import JsonDataclassMixin


@dataclass(slots=True)
class EventRecord(JsonDataclassMixin):
    event_type: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


def build_event(
    session_id: str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
) -> EventRecord:
    return EventRecord(
        event_type=event_type,
        session_id=session_id,
        payload=dict(payload or {}),
    )
