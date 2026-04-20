from __future__ import annotations

import json
import re
from typing import Any, Literal

from agents import Agent, ModelSettings
from agents.extensions.models.litellm_model import LitellmModel

from runtime_models import CoordinatorResult, JudgeResult, RuntimeDeliveryDecision, _normalize_string_list


def _candidate_text(result: CoordinatorResult) -> str:
    return str(result.final_text if result.action == "finalize" else result.draft_text).strip()


def _coordinator_result_payload(
    result: CoordinatorResult,
    *,
    mode: Literal["python", "json"] = "python",
) -> dict[str, Any]:
    return result.model_dump(mode=mode, exclude_none=True)


def _normalize_feedback_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _judge_feedback_signature(judge_result: JudgeResult) -> tuple[Any, ...]:
    return (
        judge_result.score,
        judge_result.suggested_action,
        tuple(_normalize_string_list(judge_result.issues)),
        tuple(_normalize_string_list(judge_result.absorb_points)),
        _normalize_feedback_text(judge_result.review_summary),
        _normalize_feedback_text(judge_result.feedback),
    )


def _detect_repeated_judge_feedback(
    previous_judge: JudgeResult | None,
    current_judge: JudgeResult,
) -> tuple[str, str] | None:
    if previous_judge is None or current_judge.score == "pass":
        return None
    if _judge_feedback_signature(previous_judge) != _judge_feedback_signature(current_judge):
        return None
    return (
        "judge_repeated_feedback",
        "judge 连续两轮返回相同的结构化反馈，未形成新的改进增量，运行时已停止继续空转。",
    )


def _build_runtime_delivery_decision(
    result: CoordinatorResult,
    *,
    judge_stop_reason: str = "",
) -> RuntimeDeliveryDecision:
    text = _candidate_text(result)
    text_source: Literal["", "draft_text", "final_text"] = ""
    if text:
        if result.action == "finalize":
            text_source = "final_text"
        elif result.action in {"write_draft", "revise_draft"}:
            text_source = "draft_text"
    elif judge_stop_reason:
        fallback_final_text = str(result.final_text or "").strip()
        if fallback_final_text:
            text = fallback_final_text
            text_source = "final_text"
    if judge_stop_reason and text:
        return RuntimeDeliveryDecision(
            should_export=True,
            completed=False,
            text=text,
            text_source=text_source or ("final_text" if result.action == "finalize" else "draft_text"),
            reason=judge_stop_reason,
            auto_delivered=True,
        )
    if result.action == "finalize" and text:
        return RuntimeDeliveryDecision(
            should_export=True,
            completed=True,
            text=text,
            text_source=text_source or "final_text",
            reason="finalize_action",
            auto_delivered=False,
        )
    return RuntimeDeliveryDecision(
        should_export=False,
        completed=False,
        text=text,
        text_source=text_source,
        reason="",
        auto_delivered=False,
    )


def _build_writer_input_after_coordinator(
    writer_input: str | list[dict[str, Any]],
    result: CoordinatorResult,
) -> list[dict[str, Any]]:
    messages = list(writer_input) if isinstance(writer_input, list) else [{"role": "user", "content": str(writer_input)}]
    messages.append(
        {
            "role": "assistant",
            "content": json.dumps(_coordinator_result_payload(result, mode="json"), ensure_ascii=False),
        }
    )
    return messages


def _should_auto_advance_from_outline(result: CoordinatorResult) -> bool:
    return (
        result.action == "build_outline"
        and result.completion_mode == "continue"
        and bool(result.outline_sections)
        and result.outline_follow_up_policy == "auto_continue_to_draft"
    )


def _mark_outline_auto_advance_exhausted(result: CoordinatorResult) -> CoordinatorResult:
    merged = CoordinatorResult.model_validate(result.model_dump())
    note = "运行时已自动要求由提纲继续起草正文，但模型仍未完成正文收口。"
    if note not in merged.major_risks:
        merged.major_risks.append(note)
    if not str(merged.response_text or "").strip():
        merged.response_text = "已自动续接起草，但当前轮次仍停留在提纲阶段。"
    return merged


def _should_run_judge(result: CoordinatorResult) -> bool:
    return result.action in {"write_draft", "revise_draft", "finalize"} and bool(_candidate_text(result))


def _apply_judge_feedback(
    result: CoordinatorResult,
    judge_result: JudgeResult,
    *,
    final_round: bool,
    judge_stop_reason: str = "",
) -> CoordinatorResult:
    merged = CoordinatorResult.model_validate(result.model_dump())
    merged.review_summary = judge_result.review_summary or judge_result.feedback
    if final_round and judge_result.score != "pass":
        stop_notes = {
            "judge_max_rounds_exhausted": "judge 未通过审阅且已达到最大轮次，系统按保守策略导出当前稿件。",
            "judge_repeated_feedback": "judge 连续返回重复反馈，系统已停止继续空转并保留当前稿件。",
        }
        note = stop_notes.get(judge_stop_reason, "judge 认为当前稿件仍有改进空间，已保留其反馈供后续继续修订。")
        if note not in merged.major_risks:
            merged.major_risks.append(note)
        for issue in judge_result.issues[:3]:
            item = str(issue or "").strip()
            if item and item not in merged.major_risks:
                merged.major_risks.append(item)
    return merged


def _judge_agent(model: LitellmModel, temperature: float | None) -> Agent[Any]:
    from runtime_prompting import _judge_instructions

    return Agent(
        name="JudgeAgent",
        instructions=_judge_instructions(),
        model=model,
        model_settings=ModelSettings(temperature=temperature),
        output_type=JudgeResult,
    )


__all__ = [
    "_apply_judge_feedback",
    "_build_runtime_delivery_decision",
    "_build_writer_input_after_coordinator",
    "_candidate_text",
    "_coordinator_result_payload",
    "_detect_repeated_judge_feedback",
    "_judge_agent",
    "_judge_feedback_signature",
    "_mark_outline_auto_advance_exhausted",
    "_normalize_feedback_text",
    "_should_auto_advance_from_outline",
    "_should_run_judge",
]
