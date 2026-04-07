from __future__ import annotations

from dataclasses import dataclass, field

from utils.serialization import JsonDataclassMixin


@dataclass(slots=True)
class RoundReviewViewModel(JsonDataclassMixin):
    content_status_summary: str = ""
    language_status_summary: str = ""
    dominant_issue: str = ""
    open_gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(
            self.content_status_summary
            or self.language_status_summary
            or self.dominant_issue
            or self.open_gaps
            or self.notes
        )


@dataclass(slots=True)
class RoundContextViewModel(JsonDataclassMixin):
    session_id: str = ""
    rounds_used: int = 0
    action_taken: str = ""
    action_label: str = ""
    primary_skill_display: str = ""
    revision_skill_displays: list[str] = field(default_factory=list)
    review: RoundReviewViewModel = field(default_factory=RoundReviewViewModel)
    artifact_title: str = ""
    artifact_text: str = ""
    material_actions: list[str] = field(default_factory=list)
    material_names: list[str] = field(default_factory=list)
    next_step_hint: str = ""
    message: str = ""


@dataclass(slots=True)
class AskUserViewModel(RoundContextViewModel):
    status: str = "needs_user_input"
    question_pack: list[dict[str, object]] = field(default_factory=list)
    pending_questions: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class CompletedViewModel(RoundContextViewModel):
    status: str = "completed"
    final_text: str = ""
    final_output_path: str = ""


@dataclass(slots=True)
class FailedViewModel(RoundContextViewModel):
    status: str = "failed"
    error_message: str = ""
    llm_raw_output: str = ""


@dataclass(slots=True)
class MaxRoundsExceededViewModel(RoundContextViewModel):
    status: str = "max_rounds_exceeded"
    error_message: str = ""


ResultViewModel = (
    AskUserViewModel
    | CompletedViewModel
    | FailedViewModel
    | MaxRoundsExceededViewModel
)
