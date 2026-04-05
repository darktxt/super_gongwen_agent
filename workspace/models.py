from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from utils.clock import utc_now_iso
from utils.serialization import JsonDataclassMixin


def _default_session_meta() -> dict[str, Any]:
    timestamp = utc_now_iso()
    return {
        "created_at": timestamp,
        "updated_at": timestamp,
        "latest_user_message": "",
        "user_messages": [],
    }


@dataclass(slots=True)
class DirectiveLedger(JsonDataclassMixin):
    must_follow: list[str] = field(default_factory=list)
    must_preserve: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    rejected_patterns: list[str] = field(default_factory=list)
    confirmed_structure: list[str] = field(default_factory=list)
    open_issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SeedArtifact(JsonDataclassMixin):
    doc_type_hint: str | None = None
    purpose: str | None = None
    audience: str | None = None
    role_voice: str | None = None
    occasion: str | None = None
    length_hint: str | None = None
    required_points: list[str] = field(default_factory=list)
    input_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ActiveSkillsState(JsonDataclassMixin):
    primary_skill_id: str = ""
    revision_skill_ids: list[str] = field(default_factory=list)

    def resolved_skill_ids(self) -> list[str]:
        skill_ids: list[str] = []
        primary_skill_id = str(self.primary_skill_id).strip()
        if primary_skill_id:
            skill_ids.append(primary_skill_id)
        for skill_id in self.revision_skill_ids:
            normalized = str(skill_id).strip()
            if normalized and normalized not in skill_ids:
                skill_ids.append(normalized)
        return skill_ids

    def has_primary_skill(self) -> bool:
        return bool(str(self.primary_skill_id).strip())

    @classmethod
    def from_skill_ids(cls, skill_ids: list[str]) -> "ActiveSkillsState":
        normalized = [str(skill_id).strip() for skill_id in skill_ids if str(skill_id).strip()]
        if not normalized:
            return cls()

        primary_skill_id = ""
        revision_skill_ids: list[str] = []
        for skill_id in normalized:
            if skill_id.startswith("primary.") and not primary_skill_id:
                primary_skill_id = skill_id
                continue
            if skill_id.startswith("revision."):
                if skill_id not in revision_skill_ids:
                    revision_skill_ids.append(skill_id)
                continue
            if not primary_skill_id:
                primary_skill_id = skill_id
            elif skill_id not in revision_skill_ids:
                revision_skill_ids.append(skill_id)

        return cls(
            primary_skill_id=primary_skill_id,
            revision_skill_ids=revision_skill_ids[:2],
        )


@dataclass(slots=True)
class MaterialItem(JsonDataclassMixin):
    path: str = ""
    title: str = ""
    kind: str = ""
    size: int = 0
    last_modified: str | None = None
    discovered_by: str = ""


@dataclass(slots=True)
class MaterialCatalog(JsonDataclassMixin):
    items: list[MaterialItem] = field(default_factory=list)
    allowed_roots: list[str] = field(default_factory=list)
    selected_files: list[str] = field(default_factory=list)
    file_digests: dict[str, str] = field(default_factory=dict)
    search_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class MaterialExcerpt(JsonDataclassMixin):
    excerpt_id: str = ""
    source_path: str = ""
    tool_name: str = ""
    query: str = ""
    line_start: int = 0
    line_end: int = 0
    text: str = ""
    preview: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class RetrievedMaterialsState(JsonDataclassMixin):
    excerpts: list[MaterialExcerpt] = field(default_factory=list)
    recent_queries: list[str] = field(default_factory=list)
    recent_source_paths: list[str] = field(default_factory=list)
    recent_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceBoard(JsonDataclassMixin):
    facts: list[dict[str, Any]] = field(default_factory=list)
    data_points: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)
    problem_list: list[dict[str, Any]] = field(default_factory=list)
    measure_handles: list[dict[str, Any]] = field(default_factory=list)
    usable_phrases: list[str] = field(default_factory=list)
    slot_mapping: dict[str, list[str]] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SelfReview(JsonDataclassMixin):
    responded_directives: list[str] = field(default_factory=list)
    dominant_issue: str = ""
    open_gaps: list[str] = field(default_factory=list)
    content_status_summary: str = ""
    language_status_summary: str = ""
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "SelfReview",
    ) -> "SelfReview":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("SelfReview.from_dict expects a mapping payload.")

        normalized = dict(payload)
        if "dominant_issue" not in normalized:
            for alias in ("largest_risk", "main_issue"):
                if alias in normalized:
                    normalized["dominant_issue"] = str(normalized.get(alias, "") or "")
                    break
        if "open_gaps" not in normalized:
            for alias in ("missing_evidence", "gaps"):
                if alias in normalized:
                    normalized["open_gaps"] = list(normalized.get(alias, []) or [])
                    break
        if "notes" not in normalized:
            for alias in ("observations", "remarks"):
                if alias in normalized:
                    normalized["notes"] = list(normalized.get(alias, []) or [])
                    break
        return JsonDataclassMixin.from_dict.__func__(cls, normalized)


@dataclass(slots=True)
class OutlineSection(JsonDataclassMixin):
    section_id: str = ""
    heading: str = ""
    goal: str = ""
    required_points: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OutlineArtifact(JsonDataclassMixin):
    title: str = ""
    sections: list[OutlineSection] = field(default_factory=list)
    global_objective: str = ""
    outline_text: str = ""
    open_gaps: list[str] = field(default_factory=list)
    status: str = "empty"

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "OutlineArtifact",
    ) -> "OutlineArtifact":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("OutlineArtifact.from_dict expects a mapping payload.")

        normalized = dict(payload)
        if "sections" not in normalized and "outline_sections" in normalized:
            normalized["sections"] = list(normalized.get("outline_sections", []) or [])
        if "outline_text" not in normalized:
            for alias in ("text", "content"):
                if alias in normalized:
                    normalized["outline_text"] = str(normalized.get(alias, "") or "")
                    break
        if "open_gaps" not in normalized and "open_outline_risks" in normalized:
            normalized["open_gaps"] = list(normalized.get("open_outline_risks", []) or [])
        if "status" not in normalized:
            if (
                normalized.get("title")
                or normalized.get("outline_text")
                or normalized.get("sections")
            ):
                normalized["status"] = "drafted"
            else:
                normalized["status"] = "empty"

        return JsonDataclassMixin.from_dict.__func__(cls, normalized)

@dataclass(slots=True)
class DraftArtifact(JsonDataclassMixin):
    title: str = ""
    full_text: str = ""
    section_map: dict[str, str] = field(default_factory=dict)
    assembly_mode: str = "full_text"
    status: str = "empty"
    word_count: int = 0

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "DraftArtifact",
    ) -> "DraftArtifact":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("DraftArtifact.from_dict expects a mapping payload.")

        normalized = dict(payload)
        if "full_text" not in normalized:
            for alias in ("draft_text", "revised_text", "polished_text", "text", "content"):
                if alias in normalized:
                    normalized["full_text"] = str(normalized.get(alias, "") or "")
                    break

        if "assembly_mode" not in normalized:
            normalized["assembly_mode"] = (
                "sectional"
                if normalized.get("section_map") and not normalized.get("full_text")
                else "full_text"
            )
        if "status" not in normalized:
            normalized["status"] = "drafted" if normalized.get("full_text") else "empty"
        return JsonDataclassMixin.from_dict.__func__(cls, normalized)


@dataclass(slots=True)
class RevisionHistoryEntry(JsonDataclassMixin):
    revision_id: str = ""
    source: str = ""
    action_taken: str = ""
    summary: str = ""
    focus: list[str] = field(default_factory=list)
    target_sections: list[str] = field(default_factory=list)
    before_word_count: int = 0
    after_word_count: int = 0
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "RevisionHistoryEntry",
    ) -> "RevisionHistoryEntry":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("RevisionHistoryEntry.from_dict expects a mapping payload.")

        normalized = dict(payload)
        if "revision_id" not in normalized:
            for alias in ("intent_id", "history_id", "id"):
                if alias in normalized:
                    normalized["revision_id"] = str(normalized.get(alias, "") or "")
                    break
        if "summary" not in normalized:
            for alias in ("goal", "description", "content", "text"):
                if alias in normalized:
                    normalized["summary"] = str(normalized.get(alias, "") or "")
                    break
        if "focus" not in normalized:
            for alias in ("focuses", "key_focuses", "focus_points"):
                if alias in normalized:
                    normalized["focus"] = list(normalized.get(alias, []) or [])
                    break
        if "notes" not in normalized:
            for alias in ("observations", "remarks"):
                if alias in normalized:
                    normalized["notes"] = list(normalized.get(alias, []) or [])
                    break
        return JsonDataclassMixin.from_dict.__func__(cls, normalized)


@dataclass(slots=True)
class QualityBacklogItem(JsonDataclassMixin):
    item_id: str = ""
    type: str = ""
    severity: str = "medium"
    description: str = ""
    target_section: str = ""
    suggested_action: str = ""
    status: str = "open"


@dataclass(slots=True)
class QualityBacklog(JsonDataclassMixin):
    items: list[QualityBacklogItem] = field(default_factory=list)


@dataclass(slots=True)
class VersionRecord(JsonDataclassMixin):
    version_id: str = ""
    artifact_kind: str = ""
    label: str = ""
    summary: str = ""
    parent_version_id: str = ""
    diff_summary: str = ""
    file_path: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class VersionChain(JsonDataclassMixin):
    versions: list[VersionRecord] = field(default_factory=list)


@dataclass(slots=True)
class DebugRoundSummary(JsonDataclassMixin):
    round_no: int = 0
    action_taken: str = ""
    result_status: str = ""
    context_block_titles: list[str] = field(default_factory=list)
    truncated_block_titles: list[str] = field(default_factory=list)
    active_skill_ids: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    question_count: int = 0
    outline_status: str = ""
    outline_section_count: int = 0
    draft_status: str = ""
    draft_word_count: int = 0
    dominant_issue: str = ""
    open_gaps: list[str] = field(default_factory=list)
    output_digest: str = ""
    patch_digest: str = ""
    llm_request_chars: int = 0
    llm_response_chars: int = 0
    llm_response_preview: str = ""
    debug_files: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class WorkspaceDebugState(JsonDataclassMixin):
    last_round_no: int = 0
    last_user_input: str = ""
    last_event: str = ""
    last_action: str = ""
    last_error: str = ""
    last_compiled_context_summary: dict[str, Any] = field(default_factory=dict)
    last_llm_request_summary: dict[str, Any] = field(default_factory=dict)
    last_llm_response_summary: dict[str, Any] = field(default_factory=dict)
    last_step: dict[str, Any] = field(default_factory=dict)
    last_workspace_summary: dict[str, Any] = field(default_factory=dict)
    recent_rounds: list[DebugRoundSummary] = field(default_factory=list)

    def upsert_round(
        self,
        summary: DebugRoundSummary,
        *,
        limit: int = 10,
    ) -> None:
        normalized = (
            summary
            if isinstance(summary, DebugRoundSummary)
            else DebugRoundSummary.from_dict(summary)
        )
        updated = False
        for index, item in enumerate(self.recent_rounds):
            if item.round_no == normalized.round_no:
                self.recent_rounds[index] = normalized
                updated = True
                break
        if not updated:
            self.recent_rounds.append(normalized)
        self.recent_rounds = self.recent_rounds[-limit:]


@dataclass(slots=True)
class WorkspacePatch(JsonDataclassMixin):
    directive_updates: dict[str, Any] = field(default_factory=dict)
    evidence_updates: dict[str, Any] = field(default_factory=dict)
    outline_update: dict[str, Any] = field(default_factory=dict)
    revision_history_updates: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "WorkspacePatch",
    ) -> "WorkspacePatch":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("WorkspacePatch.from_dict expects a mapping payload.")

        normalized = dict(payload)
        if "revision_history_updates" not in normalized:
            for alias in ("revision_intent_updates", "revision_intents"):
                if alias in normalized:
                    normalized["revision_history_updates"] = list(
                        normalized.get(alias, []) or []
                    )
                    break
        return JsonDataclassMixin.from_dict.__func__(cls, normalized)


@dataclass(slots=True)
class WorkspaceState(JsonDataclassMixin):
    session_id: str
    task_brief: str = ""
    directive_ledger: DirectiveLedger = field(default_factory=DirectiveLedger)
    active_skills: ActiveSkillsState = field(default_factory=ActiveSkillsState)
    material_catalog: MaterialCatalog = field(default_factory=MaterialCatalog)
    retrieved_materials: RetrievedMaterialsState = field(default_factory=RetrievedMaterialsState)
    evidence_board: EvidenceBoard = field(default_factory=EvidenceBoard)
    outline_artifact: OutlineArtifact = field(default_factory=OutlineArtifact)
    draft_artifact: DraftArtifact = field(default_factory=DraftArtifact)
    self_review: SelfReview = field(default_factory=SelfReview)
    revision_history: list[RevisionHistoryEntry] = field(default_factory=list)
    pending_questions: list[dict[str, Any]] = field(default_factory=list)
    session_meta: dict[str, Any] = field(default_factory=_default_session_meta)
    debug_state: WorkspaceDebugState = field(default_factory=WorkspaceDebugState)

    @classmethod
    def create_empty(cls, session_id: str) -> "WorkspaceState":
        workspace = cls(session_id=session_id)
        workspace.session_meta.setdefault("session_id", session_id)
        return workspace

    @property
    def active_skill_ids(self) -> list[str]:
        return self.active_skills.resolved_skill_ids()

    @active_skill_ids.setter
    def active_skill_ids(self, values: list[str]) -> None:
        self.active_skills = ActiveSkillsState.from_skill_ids(list(values))

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "WorkspaceState",
    ) -> "WorkspaceState":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("WorkspaceState.from_dict expects a mapping payload.")

        normalized = dict(payload)
        if "active_skills" not in normalized and "active_skill_ids" in normalized:
            normalized["active_skills"] = ActiveSkillsState.from_skill_ids(
                list(normalized.get("active_skill_ids", []))
            ).to_dict()
        if "revision_history" not in normalized and "revision_intents" in normalized:
            normalized["revision_history"] = list(normalized.get("revision_intents", []) or [])

        return JsonDataclassMixin.from_dict.__func__(cls, normalized)
