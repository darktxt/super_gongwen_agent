from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from structured_output_repair import (
    StructuredOutputRepairProfile,
    StructuredOutputRepairer,
    extract_last_response_text as _extract_last_response_text,
    extract_last_run_data_text as _extract_last_run_data_text,
)

from runtime_models import CoordinatorResult, JudgeResult, PendingQuestion, RuntimeContext

STRUCTURED_OUTPUT_REPAIRER = StructuredOutputRepairer()
COORDINATOR_REPAIR_PROFILE = StructuredOutputRepairProfile(name="CoordinatorResult", validator=CoordinatorResult.model_validate)
JUDGE_REPAIR_PROFILE = StructuredOutputRepairProfile(name="JudgeResult", validator=JudgeResult.model_validate)


def _fallback_result_from_text(text: str) -> CoordinatorResult:
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("模型返回为空，无法兜底生成结果。")
    return CoordinatorResult(
        action="finalize",
        decision_rationale="模型未返回结构化结果，已按保守交付稿兜底消费。",
        completion_mode="conservative_delivery",
        response_text="已在材料有限的情况下先形成一版保守稿，可继续补充事实后再修订。",
        final_text=normalized,
        assumptions=["当前材料库可能为空或不足，本稿依据用户已提供信息作保守生成。"],
        major_risks=["文中涉及的事实、数据、地区或时间信息可能仍需按实际情况补齐核对。"],
    )


def _coerce_coordinator_result(output: Any) -> CoordinatorResult:
    if isinstance(output, CoordinatorResult):
        return output
    if isinstance(output, BaseModel):
        return CoordinatorResult.model_validate(output.model_dump())
    if isinstance(output, dict):
        return CoordinatorResult.model_validate(output)
    if isinstance(output, str):
        repair = STRUCTURED_OUTPUT_REPAIRER.recover(profile=COORDINATOR_REPAIR_PROFILE, raw_output=output)
        if repair.value is not None:
            return repair.value
        return _fallback_result_from_text(output)
    return CoordinatorResult.model_validate(output)


def _coerce_judge_result(output: Any) -> JudgeResult:
    if isinstance(output, JudgeResult):
        return output
    if isinstance(output, BaseModel):
        return JudgeResult.model_validate(output.model_dump())
    if isinstance(output, dict):
        return JudgeResult.model_validate(output)
    if isinstance(output, str):
        repair = STRUCTURED_OUTPUT_REPAIRER.recover(profile=JUDGE_REPAIR_PROFILE, raw_output=output)
        if repair.value is not None:
            return repair.value
    raise ValueError("judge 未返回合法的 JudgeResult。")


def _fallback_result_from_model_error(
    ctx: RuntimeContext,
    *,
    last_text: str = "",
    error_summary: dict[str, Any] | None = None,
) -> CoordinatorResult:
    result = _coerce_coordinator_result(last_text) if last_text.strip() else _fallback_result_from_max_turns(ctx)
    result.response_text = result.response_text or "已按最后可解析内容保守收口，并保留中间态。"
    if "结构化输出失败" not in result.response_text:
        result.response_text = "本轮结构化输出失败，" + result.response_text
    if "模型未按 CoordinatorResult JSON 输出" not in result.major_risks:
        result.major_risks.append("模型未按 CoordinatorResult JSON 输出，当前结果由运行时兜底恢复。")
    classification_label = str((error_summary or {}).get("classification_label", "") or "").strip()
    if classification_label:
        detail = f"结构化输出失败分类：{classification_label}。"
        if detail not in result.major_risks:
            result.major_risks.append(detail)
    suspected_cause = str((error_summary or {}).get("suspected_cause", "") or "").strip()
    if suspected_cause and suspected_cause not in result.major_risks:
        result.major_risks.append(suspected_cause)
    return result


def _fallback_result_from_max_turns(ctx: RuntimeContext, *, last_text: str = "") -> CoordinatorResult:
    if last_text.strip():
        result = _coerce_coordinator_result(last_text)
        result.response_text = result.response_text or "已按最后可解析内容保守收口，并保留中间态。"
        if "最大回合数" not in result.response_text:
            result.response_text = "本轮已达到最大回合数，" + result.response_text
        if "模型在限制回合内未完成收口" not in result.major_risks:
            result.major_risks.append("模型在限制回合内未完成收口，当前结果可能仍需下一轮补充或校订。")
        return result
    draft_text = str(ctx.workspace.draft_artifact.full_text or "").strip()
    if draft_text:
        return CoordinatorResult(
            action="revise_draft",
            decision_rationale="已达到最大回合数，先保留当前草稿并结束本轮空转。",
            completion_mode="continue",
            response_text="本轮已达到最大回合数，已保留当前草稿和取材中间态，可继续补充要求后再修订。",
            draft_text=draft_text,
            major_risks=["模型在限制回合内未完成结构化收口。"],
        )
    return CoordinatorResult(
        action="ask_user",
        decision_rationale="已达到最大回合数，当前轮次持续取材但未完成收口，转为保留中间态并请求更明确输入。",
        completion_mode="continue",
        response_text="本轮已达到最大回合数，已保留已获取的材料线索与中间态。请进一步缩小写作范围，或直接给出必须保留的要点。",
        question_pack=[PendingQuestion(question="请补充必须覆盖的核心要点、篇幅和文种要求。")],
        major_risks=["模型在限制回合内未完成结构化收口。"],
    )


__all__ = [
    "COORDINATOR_REPAIR_PROFILE",
    "JUDGE_REPAIR_PROFILE",
    "STRUCTURED_OUTPUT_REPAIRER",
    "_coerce_coordinator_result",
    "_coerce_judge_result",
    "_extract_last_response_text",
    "_extract_last_run_data_text",
    "_fallback_result_from_max_turns",
    "_fallback_result_from_model_error",
    "_fallback_result_from_text",
]
