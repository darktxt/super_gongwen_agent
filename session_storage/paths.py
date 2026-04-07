from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import resolve_app_home


@dataclass(slots=True, frozen=True)
class SessionPaths:
    app_home: Path
    sessions_root: Path
    session_root: Path
    workspace_path: Path
    events_path: Path
    debug_dir: Path
    tool_results_dir: Path
    versions_dir: Path
    outputs_dir: Path
    final_output_path: Path
    final_markdown_path: Path


def _resolve_app_home_path(app_home: str | Path | None) -> Path:
    if app_home is None:
        return resolve_app_home()
    return Path(app_home).expanduser().resolve()


def build_session_paths(session_id: str, app_home: str | Path | None = None) -> SessionPaths:
    resolved_app_home = _resolve_app_home_path(app_home)
    sessions_root = resolved_app_home / "sessions"
    session_root = sessions_root / session_id
    outputs_dir = session_root / "outputs"

    return SessionPaths(
        app_home=resolved_app_home,
        sessions_root=sessions_root,
        session_root=session_root,
        workspace_path=session_root / "workspace.json",
        events_path=session_root / "events.jsonl",
        debug_dir=session_root / "debug",
        tool_results_dir=session_root / "tool_results",
        versions_dir=session_root / "versions",
        outputs_dir=outputs_dir,
        final_output_path=outputs_dir / "final.docx",
        final_markdown_path=outputs_dir / "final.md",
    )


def get_workspace_path(session_id: str, app_home: str | Path | None = None) -> Path:
    return build_session_paths(session_id=session_id, app_home=app_home).workspace_path
