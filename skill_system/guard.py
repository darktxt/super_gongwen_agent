from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import SkillCatalog


SKILL_REQUIRED_ACTIONS: set[str] = set()


@dataclass(slots=True)
class SkillSelectionGuard:
    catalog: SkillCatalog

    def ensure_valid(self, step: Any, snapshot: Any) -> None:
        action_taken = getattr(step, "action_taken", "")
        workspace_patch = getattr(step, "workspace_patch", None)
        action_payload = getattr(step, "action_payload", None)

        if action_taken == "load_skill" and workspace_patch is not None:
            if any(
                value not in (None, "", {}, [])
                for value in workspace_patch.to_dict().values()
            ):
                raise ValueError("load_skill step cannot modify workspace_patch.")

        if action_taken == "load_skill":
            self_review = getattr(step, "self_review", None)
            if self_review is not None and (
                getattr(self_review, "responded_directives", [])
                or str(getattr(self_review, "dominant_issue", "") or "").strip()
                or getattr(self_review, "open_gaps", [])
                or str(getattr(self_review, "content_status_summary", "") or "").strip()
                or str(getattr(self_review, "language_status_summary", "") or "").strip()
                or getattr(self_review, "notes", [])
            ):
                raise ValueError("load_skill step cannot emit self_review.")

        if action_taken == "load_skill":
            self._validate_request(action_payload)

    def _validate_request(self, request: Any) -> None:
        primary_skill_id = str(getattr(request, "primary_skill_id", "") or "").strip()
        revision_skill_ids = list(getattr(request, "revision_skill_ids", []) or [])

        if not primary_skill_id:
            raise ValueError("skill_request.primary_skill_id is required.")
        if len(revision_skill_ids) != len(set(revision_skill_ids)):
            raise ValueError("revision_skill_ids contains duplicated values.")
        if len(revision_skill_ids) > 2:
            raise ValueError("revision_skill_ids cannot contain more than 2 skills.")
        if primary_skill_id in revision_skill_ids:
            raise ValueError("primary_skill_id cannot appear in revision_skill_ids.")

        primary_spec = self.catalog.get_spec(primary_skill_id)
        if primary_spec.skill_kind != "primary":
            raise ValueError("primary_skill_id must point to a primary skill.")

        for skill_id in revision_skill_ids:
            spec = self.catalog.get_spec(skill_id)
            if spec.skill_kind != "revision":
                raise ValueError("revision_skill_ids must only contain revision skills.")
