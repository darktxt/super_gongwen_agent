from __future__ import annotations

import copy
import hashlib
import re
from typing import Any, Iterable
from uuid import uuid4

from .common import utc_now_iso
from .models import (
    MaterialItem,
    MaterialExcerpt,
    OutlineArtifact,
    RetrievedMaterialsState,
    RevisionHistoryEntry,
    WorkspacePatch,
    WorkspaceState,
)


MAX_RETRIEVED_EXCERPTS = 24
MAX_RETRIEVED_QUERIES = 12
MAX_RETRIEVED_SOURCE_PATHS = 16
MAX_EXCERPT_TEXT_CHARS = 8000
MAX_EXCERPT_PREVIEW_CHARS = 240


def _count_text_units(text: str) -> int:
    compact = re.sub(r"\s+", "", text)
    return len(compact)


def _merge_mapping_into_dataclass(instance: Any, updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if hasattr(instance, key):
            setattr(instance, key, copy.deepcopy(value))


def _rebuild_dataclass(instance: Any, updates: dict[str, Any], model_cls: type[Any]) -> Any:
    current = instance.to_dict()
    current.update(copy.deepcopy(updates))
    return model_cls.from_dict(current)


def _merge_material_items(
    items: list[MaterialItem],
    payloads: Iterable[dict[str, Any]],
) -> list[MaterialItem]:
    existing = {item.path: item for item in items if item.path}
    ordered: list[MaterialItem] = [item for item in items]

    for payload in payloads:
        item = MaterialItem.from_dict(payload)
        if not item.path:
            continue
        if item.path in existing:
            index = ordered.index(existing[item.path])
            ordered[index] = item
            existing[item.path] = item
            continue
        ordered.append(item)
        existing[item.path] = item

    return ordered


def _extend_unique(values: list[str], additions: Iterable[str]) -> list[str]:
    for value in additions:
        if value and value not in values:
            values.append(value)
    return values


def _append_recent_unique(
    values: list[str],
    additions: Iterable[str],
    *,
    limit: int,
) -> list[str]:
    ordered = [str(value).strip() for value in values if str(value).strip()]
    for raw_value in additions:
        value = str(raw_value).strip()
        if not value:
            continue
        if value in ordered:
            ordered.remove(value)
        ordered.append(value)
    return ordered[-limit:]


def _truncate_text(text: str, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    marker = "\n...[truncated]"
    if limit <= len(marker):
        return marker[:limit]
    return normalized[: limit - len(marker)].rstrip() + marker


def _build_preview(text: str, limit: int = MAX_EXCERPT_PREVIEW_CHARS) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _build_excerpt_id(*parts: object) -> str:
    raw = "||".join(str(part or "") for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return "excerpt_" + digest


def _new_revision_id() -> str:
    return "revision_" + uuid4().hex[:12]


def _append_revision_history_entries(
    items: list[RevisionHistoryEntry],
    payloads: Iterable[dict[str, Any]],
) -> list[RevisionHistoryEntry]:
    ordered: list[RevisionHistoryEntry] = list(items)
    used_ids = {
        str(item.revision_id).strip()
        for item in ordered
        if str(item.revision_id).strip()
    }

    for payload in payloads:
        item = RevisionHistoryEntry.from_dict(payload)
        revision_id = str(item.revision_id).strip()
        if not revision_id or revision_id in used_ids:
            revision_id = _new_revision_id()
            item.revision_id = revision_id
        used_ids.add(revision_id)
        ordered.append(item)

    return ordered


def _normalize_outline_update(update: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(update)
    if "sections" not in normalized and "outline_sections" in normalized:
        normalized["sections"] = list(normalized.get("outline_sections", []) or [])
    if "outline_text" not in normalized:
        for alias in ("text", "content"):
            if alias in normalized:
                normalized["outline_text"] = str(normalized.get(alias, "") or "")
                break
    if "open_gaps" not in normalized and "open_outline_risks" in normalized:
        normalized["open_gaps"] = list(normalized.get("open_outline_risks", []) or [])
    if "sections" in normalized:
        normalized["sections"] = [
            section.to_dict() if hasattr(section, "to_dict") else section
            for section in normalized["sections"]
        ]
    for key in ("readiness", "reason", "suggested_next_action"):
        normalized.pop(key, None)
    has_outline_body = bool(
        normalized.get("title")
        or normalized.get("global_objective")
        or normalized.get("outline_text")
        or normalized.get("sections")
    )
    if not has_outline_body:
        normalized.pop("status", None)
    if not normalized.get("status") and has_outline_body:
        normalized["status"] = "drafted"
    return normalized


def _coerce_search_excerpts(
    payload: dict[str, Any],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    query = str(payload.get("query", "") or "").strip()
    excerpts: list[dict[str, Any]] = []
    for item in list(payload.get("items", []) or []):
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("path", "") or "").strip()
        preview = str(item.get("preview", "") or "").strip()
        if not source_path or not preview:
            continue
        excerpts.append(
            {
                "excerpt_id": _build_excerpt_id("search", source_path, query, preview),
                "source_path": source_path,
                "tool_name": "search",
                "query": query,
                "line_start": 0,
                "line_end": 0,
                "text": _truncate_text(preview, 600),
                "preview": _build_preview(preview),
                "created_at": created_at,
            }
        )
    return excerpts


def _coerce_grep_excerpts(
    payload: dict[str, Any],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    pattern = str(payload.get("pattern", "") or "").strip()
    excerpts: list[dict[str, Any]] = []
    for match in list(payload.get("matches", []) or []):
        if not isinstance(match, dict):
            continue
        source_path = str(match.get("path", "") or "").strip()
        line_text = str(match.get("line_text", "") or "").strip()
        line_no = int(match.get("line_no", 0) or 0)
        if not source_path or not line_text:
            continue
        excerpts.append(
            {
                "excerpt_id": _build_excerpt_id("grep", source_path, pattern, line_no, line_text),
                "source_path": source_path,
                "tool_name": "grep",
                "query": pattern,
                "line_start": line_no,
                "line_end": line_no,
                "text": _truncate_text(line_text, 8000),
                "preview": _build_preview(line_text),
                "created_at": created_at,
            }
        )
    return excerpts


def _coerce_read_excerpt(
    payload: dict[str, Any],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    source_path = str(payload.get("path", "") or "").strip()
    text = str(payload.get("text", "") or "").strip()
    if not source_path or not text:
        return []
    line_start = int(payload.get("start_line", 0) or 0)
    line_end = int(payload.get("end_line", 0) or 0)
    preview = str(payload.get("preview", "") or "").strip() or _build_preview(text)
    return [
        {
            "excerpt_id": _build_excerpt_id("read", source_path, line_start, line_end, text),
            "source_path": source_path,
            "tool_name": "read",
            "query": "",
            "line_start": line_start,
            "line_end": line_end,
            "text": _truncate_text(text, MAX_EXCERPT_TEXT_CHARS),
            "preview": _build_preview(preview),
            "created_at": created_at,
        }
    ]


def _coerce_tool_result_excerpts(
    tool_name: str,
    payload: dict[str, Any],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    if tool_name == "search":
        return _coerce_search_excerpts(payload, created_at=created_at)
    if tool_name == "grep":
        return _coerce_grep_excerpts(payload, created_at=created_at)
    if tool_name == "read":
        return _coerce_read_excerpt(payload, created_at=created_at)
    return []


def _append_retrieved_material_excerpts(
    state: RetrievedMaterialsState,
    payloads: Iterable[dict[str, Any]],
    *,
    limit: int = MAX_RETRIEVED_EXCERPTS,
) -> RetrievedMaterialsState:
    ordered: list[MaterialExcerpt] = list(state.excerpts)
    existing_indexes = {
        str(item.excerpt_id).strip(): index
        for index, item in enumerate(ordered)
        if str(item.excerpt_id).strip()
    }

    for payload in payloads:
        item = MaterialExcerpt.from_dict(payload)
        excerpt_id = str(item.excerpt_id).strip()
        if not excerpt_id:
            excerpt_id = _build_excerpt_id(
                item.tool_name,
                item.source_path,
                item.query,
                item.line_start,
                item.line_end,
                item.text,
            )
            item.excerpt_id = excerpt_id

        if excerpt_id in existing_indexes:
            ordered.pop(existing_indexes[excerpt_id])
            existing_indexes = {
                str(existing_item.excerpt_id).strip(): index
                for index, existing_item in enumerate(ordered)
                if str(existing_item.excerpt_id).strip()
            }
        ordered.append(item)
        existing_indexes[excerpt_id] = len(ordered) - 1

    state.excerpts = ordered[-limit:]
    return state


class WorkspacePatcher:
    def ingest_user_message(
        self,
        workspace: WorkspaceState,
        user_input: str,
    ) -> WorkspaceState:
        message = user_input.strip()
        timestamp = utc_now_iso()

        if message and not workspace.task_brief:
            workspace.task_brief = message

        messages = workspace.session_meta.setdefault("user_messages", [])
        messages.append({"content": message, "created_at": timestamp})
        workspace.session_meta["latest_user_message"] = message
        workspace.session_meta["updated_at"] = timestamp
        workspace.session_meta["last_action"] = "user_message_ingested"
        if message:
            workspace.pending_questions = []
        return workspace

    def apply(
        self,
        workspace: WorkspaceState,
        workspace_patch: WorkspacePatch | dict[str, Any] | None,
    ) -> WorkspaceState:
        if workspace_patch is None:
            return workspace

        patch = (
            workspace_patch
            if isinstance(workspace_patch, WorkspacePatch)
            else WorkspacePatch.from_dict(workspace_patch)
        )

        if patch.directive_updates:
            _merge_mapping_into_dataclass(
                workspace.directive_ledger, patch.directive_updates
            )

        if patch.evidence_updates:
            workspace.evidence_board = _rebuild_dataclass(
                workspace.evidence_board,
                patch.evidence_updates,
                type(workspace.evidence_board),
            )

        if patch.outline_update:
            patch.outline_update = _normalize_outline_update(patch.outline_update)
            current = workspace.outline_artifact.to_dict()
            current.update(copy.deepcopy(patch.outline_update))
            workspace.outline_artifact = OutlineArtifact.from_dict(current)

        workspace.session_meta["updated_at"] = utc_now_iso()
        return workspace

    def append_revision_history_entries(
        self,
        workspace: WorkspaceState,
        entries: Iterable[dict[str, Any]] | None,
    ) -> WorkspaceState:
        if not entries:
            return workspace
        workspace.revision_history = _append_revision_history_entries(
            workspace.revision_history,
            entries,
        )
        workspace.session_meta["updated_at"] = utc_now_iso()
        return workspace

    def apply_tool_results(
        self,
        workspace: WorkspaceState,
        tool_results: Iterable[dict[str, Any]] | None,
    ) -> WorkspaceState:
        if not tool_results:
            return workspace

        for raw_result in tool_results:
            result = (
                raw_result.to_dict()
                if hasattr(raw_result, "to_dict")
                else copy.deepcopy(dict(raw_result))
            )
            payload = dict(result.get("payload", {}))
            tool_name = str(result.get("tool_name", ""))

            if tool_name in {"list", "search"}:
                workspace.material_catalog.items = _merge_material_items(
                    workspace.material_catalog.items,
                    payload.get("items", []),
                )
                if tool_name == "search":
                    workspace.material_catalog.search_history.append(
                        {
                            "query": payload.get("query", ""),
                            "created_at": result.get("created_at", utc_now_iso()),
                            "result_count": len(payload.get("items", [])),
                        }
                    )

            if tool_name in {"grep", "read", "search"}:
                selected_files = payload.get("selected_files", [])
                workspace.material_catalog.selected_files = _extend_unique(
                    list(workspace.material_catalog.selected_files),
                    [str(path) for path in selected_files],
                )

            excerpts = _coerce_tool_result_excerpts(
                tool_name,
                payload,
                created_at=str(result.get("created_at", utc_now_iso()) or utc_now_iso()),
            )
            if excerpts:
                _append_retrieved_material_excerpts(
                    workspace.retrieved_materials,
                    excerpts,
                )
                workspace.retrieved_materials.recent_queries = _append_recent_unique(
                    list(workspace.retrieved_materials.recent_queries),
                    [str(payload.get("query", "") or ""), str(payload.get("pattern", "") or "")],
                    limit=MAX_RETRIEVED_QUERIES,
                )
                workspace.retrieved_materials.recent_source_paths = _append_recent_unique(
                    list(workspace.retrieved_materials.recent_source_paths),
                    [excerpt.get("source_path", "") for excerpt in excerpts],
                    limit=MAX_RETRIEVED_SOURCE_PATHS,
                )

            if tool_name == "add_info":
                workspace.pending_questions.extend(copy.deepcopy(payload.get("questions", [])))

        workspace.session_meta["updated_at"] = utc_now_iso()
        return workspace
