from __future__ import annotations

from typing import Literal


WorkflowPhase = Literal[
    "intake",
    "gather_evidence",
    "plan_outline",
    "draft",
    "revise",
    "quality_review",
    "ask_user",
    "finalize",
    "completed",
]


ACTION_TO_PHASE: dict[str, WorkflowPhase] = {
    "build_outline": "plan_outline",
    "write_draft": "draft",
    "write_section": "draft",
    "revise_draft": "revise",
    "polish_language": "revise",
    "ask_user": "ask_user",
    "finalize": "finalize",
}
