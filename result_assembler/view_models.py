from __future__ import annotations

from dataclasses import dataclass, field

from utils.serialization import JsonDataclassMixin


@dataclass(slots=True)
class AskUserViewModel(JsonDataclassMixin):
    status: str = "needs_user_input"
    session_id: str = ""
    rounds_used: int = 0
    question_pack: list[dict[str, object]] = field(default_factory=list)
    pending_questions: list[dict[str, object]] = field(default_factory=list)
    last_action: str = ""
    message: str = ""


@dataclass(slots=True)
class CompletedViewModel(JsonDataclassMixin):
    status: str = "completed"
    session_id: str = ""
    rounds_used: int = 0
    final_text: str = ""
    final_output_path: str = ""
    primary_skill_id: str = ""
    revision_skill_ids: list[str] = field(default_factory=list)
    last_action: str = ""
    message: str = ""

    @property
    def active_skill_ids(self) -> list[str]:
        skill_ids: list[str] = []
        primary_skill_id = str(self.primary_skill_id).strip()
        if primary_skill_id:
            skill_ids.append(primary_skill_id)
        for skill_id in self.revision_skill_ids:
            normalized = str(skill_id).strip()
            if normalized and normalized not in skill_ids:
                skill_ids.append(normalized)
        return skill_ids


@dataclass(slots=True)
class FailedViewModel(JsonDataclassMixin):
    status: str = "failed"
    session_id: str = ""
    rounds_used: int = 0
    error_message: str = ""
    llm_raw_output: str = ""
    last_action: str = ""
    message: str = ""


@dataclass(slots=True)
class MaxRoundsExceededViewModel(JsonDataclassMixin):
    status: str = "max_rounds_exceeded"
    session_id: str = ""
    rounds_used: int = 0
    error_message: str = ""
    last_action: str = ""
    message: str = ""


ResultViewModel = (
    AskUserViewModel
    | CompletedViewModel
    | FailedViewModel
    | MaxRoundsExceededViewModel
)
