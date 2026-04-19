from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .paths import SessionPaths, build_session_paths
def initialize_session_storage(
    session_id: str,
    app_home: str | Path | None = None,
) -> SessionPaths:
    paths = build_session_paths(session_id=session_id, app_home=app_home)
    paths.sessions_root.mkdir(parents=True, exist_ok=True)
    paths.session_root.mkdir(parents=True, exist_ok=True)
    paths.debug_dir.mkdir(parents=True, exist_ok=True)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    return paths
def save_final_output(
    session_id: str,
    content: str,
    app_home: str | Path | None = None,
    *,
    encoding: str = "utf-8",
) -> Path:
    paths = initialize_session_storage(session_id=session_id, app_home=app_home)
    paths.final_output_path.write_text(content, encoding=encoding)
    return paths.final_output_path
def write_debug_json(
    session_id: str,
    filename: str,
    payload: Any,
    app_home: str | Path | None = None,
    *,
    encoding: str = "utf-8",
) -> Path:
    paths = initialize_session_storage(session_id=session_id, app_home=app_home)
    target = paths.debug_dir / filename
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding=encoding)
    return target
