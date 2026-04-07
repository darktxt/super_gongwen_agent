from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .docx_export import export_official_docx
from .events import EventRecord, build_event
from .paths import SessionPaths, build_session_paths


def initialize_session_storage(
    session_id: str,
    app_home: str | Path | None = None,
) -> SessionPaths:
    paths = build_session_paths(session_id=session_id, app_home=app_home)
    paths.sessions_root.mkdir(parents=True, exist_ok=True)
    paths.session_root.mkdir(parents=True, exist_ok=True)
    paths.debug_dir.mkdir(parents=True, exist_ok=True)
    paths.tool_results_dir.mkdir(parents=True, exist_ok=True)
    paths.versions_dir.mkdir(parents=True, exist_ok=True)
    paths.outputs_dir.mkdir(parents=True, exist_ok=True)
    if not paths.events_path.exists():
        paths.events_path.touch()
    return paths


def append_event(
    session_id: str,
    event: EventRecord | Mapping[str, Any],
    app_home: str | Path | None = None,
    *,
    encoding: str = "utf-8",
) -> Path:
    paths = initialize_session_storage(session_id=session_id, app_home=app_home)
    record = event if isinstance(event, EventRecord) else EventRecord.from_dict(event)
    with paths.events_path.open("a", encoding=encoding) as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False))
        handle.write("\n")
    return paths.events_path


def write_version_file(
    session_id: str,
    filename: str,
    content: str,
    app_home: str | Path | None = None,
    *,
    encoding: str = "utf-8",
) -> Path:
    paths = initialize_session_storage(session_id=session_id, app_home=app_home)
    target = paths.versions_dir / filename
    target.write_text(content, encoding=encoding)
    append_event(
        session_id=session_id,
        event=build_event(
            session_id=session_id,
            event_type="version_saved",
            payload={"file_path": str(target), "filename": filename},
        ),
        app_home=app_home,
        encoding=encoding,
    )
    return target


def save_final_output(
    session_id: str,
    content: str,
    app_home: str | Path | None = None,
    *,
    encoding: str = "utf-8",
) -> Path:
    paths = initialize_session_storage(session_id=session_id, app_home=app_home)
    paths.final_markdown_path.write_text(content, encoding=encoding)
    export_official_docx(content, paths.final_output_path)
    append_event(
        session_id=session_id,
        event=build_event(
            session_id=session_id,
            event_type="final_output_saved",
            payload={
                "file_path": str(paths.final_output_path),
                "markdown_path": str(paths.final_markdown_path),
            },
        ),
        app_home=app_home,
        encoding=encoding,
    )
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
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding=encoding,
    )
    return target
