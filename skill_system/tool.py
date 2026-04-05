from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.serialization import JsonDataclassMixin
from workspace.models import ActiveSkillsState, WorkspaceState

from .catalog import SkillCatalog
from .models import SkillRequest


@dataclass(slots=True)
class SkillToolResult(JsonDataclassMixin):
    primary_skill_id: str = ""
    revision_skill_ids: list[str] = field(default_factory=list)
    resolved_skill_ids: list[str] = field(default_factory=list)
    loaded_skills: list[dict[str, Any]] = field(default_factory=list)


class SkillTool:
    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog

    def execute(
        self,
        request: SkillRequest | dict[str, Any],
        workspace: WorkspaceState | None = None,
    ) -> dict[str, Any] | None:
        resolved_request = (
            request if isinstance(request, SkillRequest) else SkillRequest.from_dict(request)
        )
        self._validate_request(resolved_request)

        resolved_skill_ids = resolved_request.resolved_skill_ids()
        specs = self.catalog.get_specs(resolved_skill_ids)
        current_active_skills = (
            workspace.active_skills if workspace is not None else ActiveSkillsState()
        )
        if self._is_noop(resolved_request, current_active_skills):
            return None

        return SkillToolResult(
            primary_skill_id=str(resolved_request.primary_skill_id).strip(),
            revision_skill_ids=resolved_request.normalized_revision_skill_ids()[:2],
            resolved_skill_ids=resolved_skill_ids,
            loaded_skills=[spec.to_card().to_dict() for spec in specs],
        ).to_dict()

    def _validate_request(self, request: SkillRequest) -> None:
        primary_skill_id = str(request.primary_skill_id).strip()
        if not primary_skill_id:
            raise ValueError("skill_request.primary_skill_id is required.")

        primary_spec = self.catalog.get_spec(primary_skill_id)
        if primary_spec.skill_kind != "primary":
            raise ValueError("primary_skill_id must point to a primary skill.")

        revision_skill_ids = request.normalized_revision_skill_ids()
        if len(revision_skill_ids) > 2:
            raise ValueError("revision_skill_ids cannot contain more than 2 skills.")
        if primary_skill_id in revision_skill_ids:
            raise ValueError("primary_skill_id cannot appear in revision_skill_ids.")

        for skill_id in revision_skill_ids:
            spec = self.catalog.get_spec(skill_id)
            if spec.skill_kind != "revision":
                raise ValueError("revision_skill_ids must only contain revision skills.")

    def _is_noop(
        self,
        request: SkillRequest,
        active_skills: ActiveSkillsState,
    ) -> bool:
        return (
            active_skills.primary_skill_id == str(request.primary_skill_id).strip()
            and active_skills.revision_skill_ids == request.normalized_revision_skill_ids()[:2]
        )
