from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from session_storage.history import initialize_session_storage


def maybe_store_tool_payload(
    *,
    session_id: str | None,
    tool_name: str,
    request_id: str,
    payload: dict[str, Any],
    max_result_chars: int,
    app_home: str | Path | None = None,
) -> str | None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(serialized) <= max_result_chars or not session_id:
        return None

    paths = initialize_session_storage(session_id=session_id, app_home=app_home)
    target = paths.tool_results_dir / f"{request_id}_{tool_name}.json"
    target.write_text(serialized, encoding="utf-8")
    return str(target)
