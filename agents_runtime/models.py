from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ActionTaken = Literal[
    "load_skill",
    "read_materials",
    "build_outline",
    "write_draft",
    "write_section",
    "revise_draft",
    "polish_language",
    "ask_user",
    "finalize",
]


class AgentActionPayloadEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    load_skill: "AgentLoadSkillPayload | None" = None
    read_materials: "AgentReadMaterialsPayload | None" = None
    build_outline: "AgentBuildOutlinePayload | None" = None
    write_draft: "AgentWriteDraftPayload | None" = None
    write_section: "AgentWriteSectionPayload | None" = None
    revise_draft: "AgentReviseDraftPayload | None" = None
    polish_language: "AgentPolishLanguagePayload | None" = None
    ask_user: "AgentAskUserPayload | None" = None
    finalize: "AgentFinalizePayload | None" = None


class AgentLoadSkillPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_skill_id: str = ""
    revision_skill_ids: list[str] = Field(default_factory=list)


class AgentToolRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""


class AgentReadMaterialsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_requests: list[AgentToolRequestPayload] = Field(default_factory=list)


class AgentOutlineSectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = ""
    heading: str = ""
    goal: str = ""
    required_points: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentBuildOutlinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline_text: str = ""
    outline_sections: list[AgentOutlineSectionPayload] = Field(default_factory=list)


class AgentWriteDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_text: str = ""


class AgentWriteSectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = ""
    section_text: str = ""


class AgentReviseDraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revised_text: str = ""


class AgentPolishLanguagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    polished_text: str = ""


class AgentQuestionItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str = ""
    question: str = ""
    why_needed: str = ""
    expected_format: str = ""
    target_slot: str = ""
    options: list[Any] = Field(default_factory=list)
    allow_multi_select: bool = False


class AgentAskUserPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_pack: list[AgentQuestionItemPayload] = Field(default_factory=list)

    @field_validator("question_pack", mode="before")
    @classmethod
    def _coerce_question_pack(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


class AgentFinalizePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_text: str = ""


class AgentSelfReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responded_directives: list[str] = Field(default_factory=list)
    dominant_issue: str = ""
    open_gaps: list[str] = Field(default_factory=list)
    content_status_summary: str = ""
    language_status_summary: str = ""
    notes: list[str] = Field(default_factory=list)

    def has_updates(self) -> bool:
        return any(
            [
                self.responded_directives,
                self.dominant_issue.strip(),
                self.open_gaps,
                self.content_status_summary.strip(),
                self.language_status_summary.strip(),
                self.notes,
            ]
        )


class AgentOutlineUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline_text: str = ""
    sections: list[AgentOutlineSectionPayload] = Field(default_factory=list)

    def has_updates(self) -> bool:
        return bool(self.outline_text.strip() or self.sections)


class AgentRevisionHistoryUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str = ""
    source: str = ""
    action_taken: str = ""
    summary: str = ""
    focus: list[str] = Field(default_factory=list)
    target_sections: list[str] = Field(default_factory=list)
    before_word_count: int = 0
    after_word_count: int = 0
    notes: list[str] = Field(default_factory=list)
    created_at: str = ""


class AgentWorkspacePatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_updates: dict[str, Any] = Field(default_factory=dict)
    evidence_updates: dict[str, Any] = Field(default_factory=dict)
    outline_update: AgentOutlineUpdatePayload = Field(default_factory=AgentOutlineUpdatePayload)
    revision_history_updates: list[AgentRevisionHistoryUpdatePayload] = Field(default_factory=list)

    def has_updates(self) -> bool:
        return any(
            [
                self.directive_updates,
                self.evidence_updates,
                self.outline_update.has_updates(),
                self.revision_history_updates,
            ]
        )


class AgentBrainStepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_taken: ActionTaken
    action_payload: AgentActionPayloadEnvelope = Field(default_factory=AgentActionPayloadEnvelope)
    workspace_patch: AgentWorkspacePatchPayload = Field(default_factory=AgentWorkspacePatchPayload)
    self_review: AgentSelfReviewPayload = Field(default_factory=AgentSelfReviewPayload)

    def to_brain_step_dict(self) -> dict[str, Any]:
        action_payload = self.action_payload.model_dump(exclude_none=True, exclude_defaults=True)
        result: dict[str, Any] = {
            "action_taken": self.action_taken,
            "action_payload": action_payload,
        }
        if self.workspace_patch.has_updates():
            result["workspace_patch"] = self.workspace_patch.model_dump(
                exclude_defaults=True,
            )
        if self.self_review.has_updates():
            result["self_review"] = self.self_review.model_dump(
                exclude_defaults=True,
            )
        return result
