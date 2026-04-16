from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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

    load_skill: dict[str, Any] | None = None
    read_materials: dict[str, Any] | None = None
    build_outline: dict[str, Any] | None = None
    write_draft: dict[str, Any] | None = None
    write_section: dict[str, Any] | None = None
    revise_draft: dict[str, Any] | None = None
    polish_language: dict[str, Any] | None = None
    ask_user: dict[str, Any] | None = None
    finalize: dict[str, Any] | None = None


class AgentBrainStepOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_taken: ActionTaken
    action_payload: AgentActionPayloadEnvelope = Field(default_factory=AgentActionPayloadEnvelope)
    workspace_patch: dict[str, Any] = Field(default_factory=dict)
    self_review: dict[str, Any] = Field(default_factory=dict)

    def to_brain_step_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action_taken": self.action_taken,
            "action_payload": self.action_payload.model_dump(exclude_none=True),
        }
        if self.workspace_patch:
            result["workspace_patch"] = dict(self.workspace_patch)
        if self.self_review:
            result["self_review"] = dict(self.self_review)
        return result
