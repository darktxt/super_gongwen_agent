from __future__ import annotations

import json
from pathlib import Path

from session_storage.history import initialize_session_storage
from session_storage.paths import get_workspace_path
from utils.clock import utc_now_iso
from .models import WorkspaceState
from .snapshot import WorkspaceSnapshot, build_workspace_snapshot


class WorkspaceStore:
    def __init__(self, app_home: str | Path | None = None, *, encoding: str = "utf-8") -> None:
        self.app_home = Path(app_home).resolve() if app_home is not None else None
        self.encoding = encoding

    def load_or_create(self, session_id: str) -> WorkspaceState:
        initialize_session_storage(session_id=session_id, app_home=self.app_home)
        workspace_path = get_workspace_path(session_id=session_id, app_home=self.app_home)

        if workspace_path.exists():
            return self.load(session_id=session_id)

        workspace = WorkspaceState.create_empty(session_id=session_id)
        workspace.session_meta["created_at"] = utc_now_iso()
        self.save(workspace)
        return workspace

    def load(self, session_id: str) -> WorkspaceState:
        workspace_path = get_workspace_path(session_id=session_id, app_home=self.app_home)
        payload = json.loads(workspace_path.read_text(encoding=self.encoding))
        return WorkspaceState.from_dict(payload)

    def save(self, workspace: WorkspaceState) -> Path:
        initialize_session_storage(session_id=workspace.session_id, app_home=self.app_home)
        workspace.session_meta["updated_at"] = utc_now_iso()
        workspace_path = get_workspace_path(
            session_id=workspace.session_id,
            app_home=self.app_home,
        )
        workspace_path.write_text(
            json.dumps(workspace.to_dict(), ensure_ascii=False, indent=2),
            encoding=self.encoding,
        )
        return workspace_path

    def snapshot(
        self,
        workspace: WorkspaceState,
        *,
        available_tools: list[object] | None = None,
    ) -> WorkspaceSnapshot:
        return build_workspace_snapshot(
            workspace,
            available_tools=available_tools,
        )
