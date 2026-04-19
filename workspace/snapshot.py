from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import JsonDataclassMixin
from .models import (
    DirectiveLedger,
    DraftArtifact,
    EvidenceBoard,
    MaterialCatalog,
    OutlineArtifact,
    QualityBacklog,
    RetrievedMaterialsState,
    RevisionHistoryEntry,
    SelfReview,
    WorkflowState,
    WorkspaceState,
)


@dataclass(slots=True)
class WorkspaceSnapshot(JsonDataclassMixin):
    latest_user_message: str = ""
    task_brief: str = ""
    recent_user_messages: list[dict[str, Any]] = field(default_factory=list)
    recent_brain_trace: list[dict[str, Any]] = field(default_factory=list)
    runtime_workflow: str = ""
    provider_profile: dict[str, Any] = field(default_factory=dict)
    quality_review_snapshots: list[dict[str, Any]] = field(default_factory=list)
    finalization_blockers: list[str] = field(default_factory=list)
    workflow_state: WorkflowState = field(default_factory=WorkflowState)
    quality_backlog: QualityBacklog = field(default_factory=QualityBacklog)
    directive_ledger: DirectiveLedger = field(default_factory=DirectiveLedger)
    material_catalog: MaterialCatalog = field(default_factory=MaterialCatalog)
    retrieved_materials: RetrievedMaterialsState = field(default_factory=RetrievedMaterialsState)
    evidence_board: EvidenceBoard = field(default_factory=EvidenceBoard)
    current_outline: OutlineArtifact = field(default_factory=OutlineArtifact)
    current_draft: DraftArtifact = field(default_factory=DraftArtifact)
    current_self_review: SelfReview = field(default_factory=SelfReview)
    revision_history: list[RevisionHistoryEntry] = field(default_factory=list)
    pending_questions: list[dict[str, Any]] = field(default_factory=list)
    available_tools: list[dict[str, Any]] = field(default_factory=list)


def build_workspace_snapshot(
    workspace: WorkspaceState,
    *,
    available_tools: list[Any] | None = None,
) -> WorkspaceSnapshot:
    latest_user_message = str(workspace.session_meta.get("latest_user_message", ""))
    recent_user_messages = [
        {
            "content": str(message.get("content", "") or ""),
            "created_at": str(message.get("created_at", "") or ""),
        }
        for message in list(workspace.session_meta.get("user_messages", []) or [])[-6:]
        if isinstance(message, dict)
    ]
    recent_brain_trace = [
        round_summary.to_dict()
        for round_summary in list(workspace.debug_state.recent_rounds or [])[-4:]
    ]
    return WorkspaceSnapshot(
        latest_user_message=latest_user_message,
        task_brief=workspace.task_brief,
        recent_user_messages=recent_user_messages,
        recent_brain_trace=recent_brain_trace,
        runtime_workflow=str(workspace.session_meta.get("runtime_workflow", "") or ""),
        provider_profile=dict(workspace.session_meta.get("provider_profile", {}) or {}),
        quality_review_snapshots=[
            dict(item)
            for item in list(workspace.session_meta.get("quality_review_snapshots", []) or [])[-4:]
            if isinstance(item, dict)
        ],
        finalization_blockers=[
            str(item).strip()
            for item in list(workspace.session_meta.get("finalization_blockers", []) or [])
            if str(item).strip()
        ][:8],
        workflow_state=WorkflowState.from_dict(workspace.workflow_state),
        quality_backlog=QualityBacklog.from_dict(workspace.quality_backlog),
        directive_ledger=DirectiveLedger.from_dict(workspace.directive_ledger),
        material_catalog=MaterialCatalog.from_dict(workspace.material_catalog),
        retrieved_materials=RetrievedMaterialsState.from_dict(workspace.retrieved_materials),
        evidence_board=EvidenceBoard.from_dict(workspace.evidence_board),
        current_outline=OutlineArtifact.from_dict(workspace.outline_artifact),
        current_draft=DraftArtifact.from_dict(workspace.draft_artifact),
        current_self_review=SelfReview.from_dict(workspace.self_review),
        revision_history=[
            RevisionHistoryEntry.from_dict(item)
            for item in workspace.revision_history[-8:]
        ],
        pending_questions=list(workspace.pending_questions),
        available_tools=_serialize_entries(available_tools),
    )


def _serialize_entries(values: list[Any] | None) -> list[dict[str, Any]]:
    if not values:
        return []
    serialized: list[dict[str, Any]] = []
    for value in values:
        if hasattr(value, "to_dict"):
            serialized.append(value.to_dict())
        elif isinstance(value, dict):
            serialized.append(dict(value))
    return serialized
