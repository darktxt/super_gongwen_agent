from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from workspace.models import WorkspaceState

COORDINATOR_OUTPUT_CONTRACT = (
    "下一条 assistant 消息必须只输出一个合法的 CoordinatorResult JSON 对象；"
    "禁止输出正文原文、说明文字、Markdown、代码块或 JSON 之外的任何内容。"
    "即使刚刚调用过材料工具或收到 judge 反馈，也必须回到该 JSON 协议收口。"
    "工具返回仅供决策，最终正文必须写入 draft_text 或 final_text。"
)
JUDGE_OUTPUT_CONTRACT = (
    "下一条 assistant 消息必须只输出一个合法的 JudgeResult JSON 对象；"
    "禁止输出自然语言评语、Markdown、代码块或 JSON 之外的任何内容。"
)
ACTION_ALIASES = {
    "起草": "write_draft",
    "草拟": "write_draft",
    "撰写": "write_draft",
    "draft": "write_draft",
    "write_draft": "write_draft",
    "定稿": "finalize",
    "完成": "finalize",
    "final": "finalize",
    "finalize": "finalize",
    "修订": "revise_draft",
    "修改": "revise_draft",
    "revise": "revise_draft",
    "revise_draft": "revise_draft",
    "提纲": "build_outline",
    "outline": "build_outline",
    "build_outline": "build_outline",
    "追问": "ask_user",
    "补问": "ask_user",
    "ask": "ask_user",
    "ask_user": "ask_user",
}


@dataclass(slots=True)
class RuntimeContext:
    session_id: str
    working_root: Path
    materials_root: Path
    workspace: WorkspaceState
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    on_tool_event: Callable[[dict[str, Any]], None] | None = None


class PendingQuestion(BaseModel):
    question: str
    reason: str = ""


class OutlineSectionResult(BaseModel):
    heading: str
    goal: str = ""
    required_points: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    items = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_pending_question_list(value: Any) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else ([value] if value is not None else [])
    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            question = str(item.get("question") or item.get("text") or "").strip()
            reason = str(item.get("reason") or "").strip()
        else:
            question = str(item or "").strip()
            reason = ""
        if question:
            normalized.append({"question": question, "reason": reason})
    return normalized


def _outline_sections_from_text(value: Any) -> list[dict[str, str]]:
    text = str(value or "").strip()
    if not text:
        return []
    sections: list[dict[str, str]] = []
    for line in text.splitlines():
        heading = str(line or "").strip()
        if heading:
            sections.append({"heading": heading})
    return sections


def _format_judge_issue_entry(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return str(entry or "").strip()
    description = str(entry.get("description") or entry.get("issue") or entry.get("text") or "").strip()
    location = str(entry.get("location") or "").strip()
    dimension = str(entry.get("dimension") or "").strip()
    severity = str(entry.get("severity") or "").strip()
    if not description:
        return ""
    prefix_parts = [part for part in (location, dimension, severity.upper() if severity else "") if part]
    return f"{' / '.join(prefix_parts)}：{description}" if prefix_parts else description


def _normalize_judge_issue_list(*values: Any) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = _format_judge_issue_entry(item)
            if text and text not in normalized:
                normalized.append(text)
    return normalized


def _collect_absorb_points_from_issue_groups(*groups: Any) -> list[str]:
    absorb_points: list[str] = []
    for group in groups:
        items = group if isinstance(group, list) else ([group] if group is not None else [])
        for item in items:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "").strip().lower()
            if severity not in {"critical", "high"}:
                continue
            description = str(item.get("description") or item.get("issue") or item.get("text") or "").strip()
            if description and description not in absorb_points:
                absorb_points.append(description)
    return absorb_points


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: Literal["pass", "needs_improvement", "fail"] = "needs_improvement"
    feedback: str
    issues: list[str] = Field(default_factory=list)
    suggested_action: Literal["write_draft", "revise_draft", "ask_user", "finalize"] = "revise_draft"
    review_summary: str = ""
    absorb_points: list[str] = Field(default_factory=list)
    output_contract: str = JUDGE_OUTPUT_CONTRACT

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        issues = _normalize_judge_issue_list(
            data.get("issues"),
            data.get("details"),
            data.get("critical_issues"),
            data.get("minor_issues"),
        )
        absorb_points = _normalize_string_list([data.get("primary_issue"), data.get("absorb_points")])
        if not absorb_points:
            absorb_points = _collect_absorb_points_from_issue_groups(
                data.get("details"),
                data.get("critical_issues"),
                data.get("minor_issues"),
            )
        if not absorb_points and isinstance(data.get("scores"), dict):
            score_items = sorted(
                (
                    (str(name).strip(), score)
                    for name, score in dict(data.get("scores") or {}).items()
                    if str(name).strip()
                ),
                key=lambda item: item[1] if isinstance(item[1], (int, float)) else 101,
            )
            absorb_points = [f"{name}：{score}" for name, score in score_items[:3]]
        summary = str(
            data.get("summary")
            or data.get("review_summary")
            or data.get("overall_note")
            or data.get("feedback")
            or ""
        ).strip()
        feedback = str(data.get("feedback") or data.get("summary") or data.get("overall_note") or "").strip()
        data.setdefault("review_summary", summary)
        data.setdefault("feedback", feedback or summary or "请继续完善当前稿件。")
        data["issues"] = issues
        data["absorb_points"] = absorb_points
        score = str(data.get("score") or data.get("verdict") or "").strip().lower()
        aliases = {
            "ready": "pass",
            "pass": "pass",
            "approved": "pass",
            "revise": "needs_improvement",
            "needs_improvement": "needs_improvement",
            "improve": "needs_improvement",
            "fail": "fail",
            "reject": "fail",
        }
        if score:
            data["score"] = aliases.get(score, score)
        return data


class CoordinatorResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: Literal["build_outline", "write_draft", "revise_draft", "ask_user", "finalize"]
    decision_rationale: str
    completion_mode: Literal["continue", "conservative_delivery", "final"] = "continue"
    outline_follow_up_policy: Literal["stop_after_outline", "auto_continue_to_draft"] | None = None
    assumptions: list[str] = Field(default_factory=list)
    major_risks: list[str] = Field(default_factory=list)
    response_text: str = ""
    outline_title: str = ""
    outline_sections: list[OutlineSectionResult] = Field(default_factory=list)
    draft_text: str = ""
    final_text: str = ""
    question_pack: list[PendingQuestion] = Field(default_factory=list)
    review_summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        action = str(data.get("action") or data.get("coordinator_decision") or data.get("decision") or "").strip()
        data["draft_text"] = _normalize_optional_text(data.get("draft_text"))
        data["final_text"] = _normalize_optional_text(data.get("final_text"))
        if not data.get("outline_sections"):
            data["outline_sections"] = _outline_sections_from_text(data.get("outline_text"))
        if not data.get("question_pack"):
            data["question_pack"] = _normalize_pending_question_list(data.get("clarifying_questions"))
        if action:
            data["action"] = ACTION_ALIASES.get(action, action)
        elif data.get("question_pack"):
            data["action"] = "ask_user"
        elif data.get("final_text"):
            data["action"] = "finalize"
        elif data.get("draft_text"):
            data["action"] = "write_draft"
        elif data.get("outline_sections"):
            data["action"] = "build_outline"
        follow_up_policy = str(
            data.get("outline_follow_up_policy")
            or data.get("outline_next_step_policy")
            or data.get("follow_up_policy")
            or ""
        ).strip().lower()
        follow_up_aliases = {
            "stop_after_outline": "stop_after_outline",
            "outline_only": "stop_after_outline",
            "stop": "stop_after_outline",
            "stay_on_outline": "stop_after_outline",
            "auto_continue_to_draft": "auto_continue_to_draft",
            "continue_to_draft": "auto_continue_to_draft",
            "auto_continue": "auto_continue_to_draft",
            "continue": "auto_continue_to_draft",
            "draft_next": "auto_continue_to_draft",
        }
        if data.get("action") == "build_outline":
            data["outline_follow_up_policy"] = follow_up_aliases.get(
                follow_up_policy,
                follow_up_policy or "stop_after_outline",
            )
        else:
            data.pop("outline_follow_up_policy", None)
            data.pop("outline_next_step_policy", None)
            data.pop("follow_up_policy", None)
        data.setdefault("assumptions", data.get("major_assumptions") or [])
        data.setdefault("review_summary", data.get("reviewer_summary") or "")
        data.setdefault(
            "decision_rationale",
            data.get("decision_rationale")
            or data.get("response_text")
            or data.get("next_action")
            or "模型已按兼容字段返回，运行时已自动归一化。",
        )
        return data


@dataclass(slots=True)
class RuntimeDeliveryDecision:
    should_export: bool = False
    completed: bool = False
    text: str = ""
    text_source: Literal["", "draft_text", "final_text"] = ""
    reason: str = ""
    auto_delivered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_export": self.should_export,
            "completed": self.completed,
            "text": self.text,
            "text_source": self.text_source,
            "reason": self.reason,
            "auto_delivered": self.auto_delivered,
        }


@dataclass(slots=True)
class RuntimeOutcome:
    result: CoordinatorResult
    tool_events: list[dict[str, Any]]
    raw_output: Any = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    delivery_decision: RuntimeDeliveryDecision | None = None

    def __post_init__(self) -> None:
        if self.delivery_decision is None:
            from runtime_judge_flow import _build_runtime_delivery_decision

            self.delivery_decision = _build_runtime_delivery_decision(self.result)


@dataclass(slots=True)
class CoordinatorTurnResult:
    output: CoordinatorResult
    raw_output: Any
    run_data: Any
    structured_output_succeeded: bool
    continue_flow: bool
    next_writer_input: list[dict[str, Any]]
    error_summary: dict[str, Any] = field(default_factory=dict)
    recovery_summary: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "ACTION_ALIASES",
    "COORDINATOR_OUTPUT_CONTRACT",
    "CoordinatorResult",
    "CoordinatorTurnResult",
    "JUDGE_OUTPUT_CONTRACT",
    "JudgeResult",
    "OutlineSectionResult",
    "PendingQuestion",
    "RuntimeContext",
    "RuntimeDeliveryDecision",
    "RuntimeOutcome",
    "_collect_absorb_points_from_issue_groups",
    "_format_judge_issue_entry",
    "_normalize_judge_issue_list",
    "_normalize_optional_text",
    "_normalize_pending_question_list",
    "_normalize_string_list",
    "_outline_sections_from_text",
]
