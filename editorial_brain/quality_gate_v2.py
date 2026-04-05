from __future__ import annotations

from dataclasses import dataclass, field
import re

from workspace.models import WorkspaceState

from .contracts_core import BrainStepResult


@dataclass(slots=True)
class QualityGateResult:
    passed: bool
    final_text: str = ""
    reasons: list[str] = field(default_factory=list)


class QualityGateError(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("Quality gate failed: " + " | ".join(reasons))


class QualityGate:
    def ensure_passed(
        self,
        workspace: WorkspaceState,
        step: BrainStepResult,
    ) -> QualityGateResult:
        reasons: list[str] = []
        final_text = self._resolve_final_text(workspace, step)
        self_review = workspace.self_review
        has_self_review = self._has_self_review(self_review)

        if not final_text.strip():
            reasons.append("定稿文本为空。")

        must_follow = list(workspace.directive_ledger.must_follow)
        responded_directives = {
            str(item).strip()
            for item in self_review.responded_directives
            if str(item).strip()
        }
        if has_self_review and must_follow and not responded_directives.intersection(must_follow):
            reasons.append("must_follow 指令未被显式响应。")

        open_gaps = [
            str(item).strip()
            for item in list(self_review.open_gaps or [])
            if str(item).strip()
        ]
        if has_self_review and open_gaps and step.action_taken == "finalize":
            reasons.append("self_review 仍存在待补缺口：" + "；".join(open_gaps[:3]))

        if not workspace.draft_artifact.full_text.strip() and not final_text.strip():
            reasons.append("缺少有效正文内容，无法定稿。")

        if self._looks_incomplete(final_text):
            reasons.append("稿件仍像未完成稿，存在占位或明显空泛问题。")

        if reasons:
            raise QualityGateError(reasons)

        return QualityGateResult(passed=True, final_text=final_text, reasons=[])

    def _resolve_final_text(self, workspace: WorkspaceState, step: BrainStepResult) -> str:
        action_payload = step.action_payload
        final_text = str(getattr(action_payload, "final_text", "") or "").strip()
        if final_text:
            return final_text
        return workspace.draft_artifact.full_text.strip()

    def _has_self_review(self, review: object) -> bool:
        responded_directives = list(getattr(review, "responded_directives", []) or [])
        dominant_issue = str(getattr(review, "dominant_issue", "") or "").strip()
        open_gaps = list(getattr(review, "open_gaps", []) or [])
        content_status_summary = str(getattr(review, "content_status_summary", "") or "").strip()
        language_status_summary = str(getattr(review, "language_status_summary", "") or "").strip()
        notes = list(getattr(review, "notes", []) or [])
        return bool(
            responded_directives
            or dominant_issue
            or open_gaps
            or content_status_summary
            or language_status_summary
            or notes
        )

    def _looks_incomplete(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 40:
            return True

        placeholder_patterns = [
            r"待补充",
            r"待完善",
            r"TODO",
            r"TBD",
            r"XXXX",
            r"\{\{.+?\}\}",
        ]
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in placeholder_patterns)
