from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import RequiredSlot, SkillSpec


def default_skills_root() -> Path:
    return Path(__file__).resolve().parent / "skills"


class SkillLoader:
    def __init__(self, skills_root: str | Path | None = None) -> None:
        self.skills_root = Path(skills_root).resolve() if skills_root else default_skills_root()

    def load_all(self) -> list[SkillSpec]:
        specs: list[SkillSpec] = []
        for skill_kind in ("primary", "revision"):
            group_root = self.skills_root / skill_kind
            if not group_root.exists():
                continue
            for path in sorted(group_root.glob("*.json")):
                specs.append(self.load_file(path, skill_kind=skill_kind))
        return specs

    def load_file(
        self,
        path: str | Path,
        *,
        skill_kind: str | None = None,
    ) -> SkillSpec:
        resolved_path = Path(path).resolve()
        payload = json.loads(resolved_path.read_text(encoding="utf-8-sig"))
        resolved_kind = skill_kind or resolved_path.parent.name
        return self._normalize_skill(
            payload,
            source_path=resolved_path,
            skill_kind=resolved_kind,
        )

    def _normalize_skill(
        self,
        payload: dict[str, Any],
        *,
        source_path: Path,
        skill_kind: str,
    ) -> SkillSpec:
        when_to_use = self._string_list(payload.get("when_to_use") or payload.get("applies_to"))
        aliases = self._string_list(payload.get("aliases") or payload.get("variants"))

        return SkillSpec(
            skill_id=str(payload.get("skill_id", "")),
            skill_kind=skill_kind,
            name=str(payload.get("name", "")),
            summary=str(payload.get("summary", "")),
            when_to_use=when_to_use,
            not_for=self._string_list(payload.get("not_for")),
            aliases=aliases,
            writing_goals=self._string_list(payload.get("writing_goals")),
            required_slots=[
                RequiredSlot.from_dict(item)
                for item in payload.get("required_slots", [])
                if isinstance(item, dict)
            ],
            review_rubric=self._string_list(payload.get("review_rubric")),
            query_hints=self._string_list(payload.get("query_hints")),
            preserve_rules=self._string_list(payload.get("preserve_rules")),
            output_preferences=dict(payload.get("output_preferences", {})),
            source_path=str(source_path),
        )

    def _string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)]
