from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.serialization import JsonDataclassMixin


@dataclass(slots=True)
class RequiredSlot(JsonDataclassMixin):
    slot_id: str = ""
    name: str = ""
    purpose: str = ""
    evidence_types: list[str] = field(default_factory=list)
    writing_requirements: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillCard(JsonDataclassMixin):
    skill_id: str = ""
    skill_kind: str = ""
    name: str = ""
    summary: str = ""
    when_to_use: list[str] = field(default_factory=list)
    not_for: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillRequest(JsonDataclassMixin):
    primary_skill_id: str = ""
    revision_skill_ids: list[str] = field(default_factory=list)

    def normalized_revision_skill_ids(self) -> list[str]:
        ordered: list[str] = []
        for skill_id in self.revision_skill_ids:
            normalized = str(skill_id).strip()
            if normalized and normalized not in ordered:
                ordered.append(normalized)
        return ordered

    def resolved_skill_ids(self) -> list[str]:
        skill_ids: list[str] = []
        primary_skill_id = str(self.primary_skill_id).strip()
        if primary_skill_id:
            skill_ids.append(primary_skill_id)
        skill_ids.extend(self.normalized_revision_skill_ids())
        return skill_ids


@dataclass(slots=True)
class SkillSpec(JsonDataclassMixin):
    skill_id: str = ""
    skill_kind: str = ""
    name: str = ""
    summary: str = ""
    when_to_use: list[str] = field(default_factory=list)
    not_for: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    writing_goals: list[str] = field(default_factory=list)
    required_slots: list[RequiredSlot] = field(default_factory=list)
    review_rubric: list[str] = field(default_factory=list)
    query_hints: list[str] = field(default_factory=list)
    preserve_rules: list[str] = field(default_factory=list)
    output_preferences: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    def to_card(self) -> SkillCard:
        return SkillCard(
            skill_id=self.skill_id,
            skill_kind=self.skill_kind,
            name=self.name,
            summary=self.summary,
            when_to_use=list(self.when_to_use),
            not_for=list(self.not_for),
            aliases=list(self.aliases),
        )

    def to_active_block(self) -> dict[str, Any]:
        return self.to_dict()

    @property
    def storage_group(self) -> str:
        return self.skill_kind
