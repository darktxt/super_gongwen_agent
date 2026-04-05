from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from workspace.models import ActiveSkillsState
from .loader import SkillLoader
from .models import SkillCard, SkillSpec


@dataclass(slots=True)
class SkillCatalog:
    specs_by_id: dict[str, SkillSpec]

    @classmethod
    def from_loader(cls, loader: SkillLoader | None = None) -> "SkillCatalog":
        resolved_loader = loader or SkillLoader()
        specs = resolved_loader.load_all()
        return cls(specs_by_id={spec.skill_id: spec for spec in specs})

    @classmethod
    def from_skills_root(cls, skills_root: str | Path) -> "SkillCatalog":
        return cls.from_loader(SkillLoader(skills_root=skills_root))

    def list_cards(self) -> list[SkillCard]:
        return [self.specs_by_id[skill_id].to_card() for skill_id in sorted(self.specs_by_id)]

    def list_specs(self) -> list[SkillSpec]:
        return [self.specs_by_id[skill_id] for skill_id in sorted(self.specs_by_id)]

    def get_spec(self, skill_id: str) -> SkillSpec:
        try:
            return self.specs_by_id[skill_id]
        except KeyError as exc:
            raise KeyError(f"Unknown skill_id: {skill_id}") from exc

    def get_specs(self, skill_ids: list[str]) -> list[SkillSpec]:
        return [self.get_spec(skill_id) for skill_id in skill_ids]

    def get_active_specs(
        self,
        active_skills: ActiveSkillsState | Iterable[str] | None,
    ) -> list[SkillSpec]:
        if active_skills is None:
            return []
        if isinstance(active_skills, ActiveSkillsState):
            active_skill_ids = active_skills.resolved_skill_ids()
        else:
            active_skill_ids = [str(skill_id) for skill_id in active_skills]
        return [
            self.get_spec(skill_id)
            for skill_id in active_skill_ids
            if skill_id in self.specs_by_id
        ]

    def has_skill(self, skill_id: str) -> bool:
        return skill_id in self.specs_by_id
