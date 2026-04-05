from __future__ import annotations

from uuid import uuid4


def generate_session_id(prefix: str = "session") -> str:
    return f"{prefix}-{uuid4().hex[:12]}"
