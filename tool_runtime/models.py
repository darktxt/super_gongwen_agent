from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import uuid

from utils.clock import utc_now_iso
from utils.serialization import JsonDataclassMixin


@dataclass(slots=True)
class ToolSpec(JsonDataclassMixin):
    name: str
    is_read_only: bool
    is_concurrency_safe: bool
    requires_user_interaction: bool
    max_result_chars: int = 4000


@dataclass(slots=True)
class ToolRequest(JsonDataclassMixin):
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: f"tool_{uuid.uuid4().hex[:12]}")


@dataclass(slots=True)
class ToolResult(JsonDataclassMixin):
    tool_name: str
    request_id: str
    status: str = "ok"
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    output_ref: str | None = None
    is_truncated: bool = False
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True, frozen=True)
class ToolExecutionContext:
    working_root: Path
    session_id: str | None = None
    app_home: Path | None = None
