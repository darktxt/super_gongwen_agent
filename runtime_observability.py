from __future__ import annotations

import re
from typing import Any

from structured_output_repair import preview_value, summarize_run_data

from runtime_models import COORDINATOR_OUTPUT_CONTRACT, CoordinatorResult, RuntimeDeliveryDecision


def _preview(text: Any, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _build_runtime_request_summary(
    *,
    session_id: str,
    request_text: str,
    user_input: str,
    runtime_context: Any,
    model_name: str,
    base_url: str,
    temperature: float | None,
    enable_tracing: bool,
    max_turns: int,
    judge_max_rounds: int,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "model_name": model_name,
        "base_url": base_url,
        "temperature": temperature,
        "tracing_enabled": enable_tracing,
        "max_turns": max_turns,
        "working_root": str(runtime_context.working_root),
        "materials_root": str(runtime_context.materials_root),
        "tool_names": ["list_materials", "search_materials", "read_material", "grep_materials"],
        "judge_loop_enabled": True,
        "judge_max_rounds": judge_max_rounds,
        "user_input_chars": len(str(user_input or "").strip()),
        "compiled_input_chars": len(request_text),
        "user_input_preview": _preview(user_input, limit=180),
        "compiled_input_preview": _preview(request_text, limit=280),
        "pending_question_count": len(runtime_context.workspace.pending_questions),
        "selected_file_count": len(runtime_context.workspace.material_catalog.selected_files),
        "draft_word_count": runtime_context.workspace.draft_artifact.word_count,
        "output_contract": COORDINATOR_OUTPUT_CONTRACT,
    }


def _build_runtime_diagnostics(
    *,
    request_summary: dict[str, Any],
    run_data: Any,
    raw_output: Any,
    tool_events: list[dict[str, Any]],
    result: CoordinatorResult,
    delivery_decision: RuntimeDeliveryDecision,
    structured_output_succeeded: bool,
    error_summary: dict[str, Any] | None = None,
    recovery_summary: dict[str, Any] | None = None,
    judge_runs: list[dict[str, Any]] | None = None,
    judge_stop_reason: str = "",
) -> dict[str, Any]:
    run_data_summary = summarize_run_data(run_data)
    raw_output_preview = preview_value(raw_output, limit=600)
    judge_history = list(judge_runs or [])
    latest_judge_run = judge_history[-1] if judge_history else {}
    return {
        "status": "recovered_model_behavior_error" if error_summary else "ok",
        "request_summary": dict(request_summary),
        "response_summary": {
            **run_data_summary,
            "raw_output_type": type(raw_output).__name__,
            "raw_output_preview": raw_output_preview,
            "structured_output_succeeded": structured_output_succeeded,
            "tool_event_count": len(tool_events),
        },
        "delivery_decision": delivery_decision.to_dict(),
        "error": dict(error_summary or {}),
        "recovery": {
            "used_fallback": bool(recovery_summary),
            **dict(recovery_summary or {}),
        },
        "judge_runs": judge_history,
        "judge_summary": {
            "run_count": len(judge_history),
            "stop_reason": judge_stop_reason,
            "passed": str(latest_judge_run.get("score", "") or "") == "pass",
            "last_score": str(latest_judge_run.get("score", "") or ""),
        },
        "result_summary": {
            "action": result.action,
            "completion_mode": result.completion_mode,
            "question_count": len(result.question_pack),
            "major_risk_count": len(result.major_risks),
            "response_preview": _preview(result.response_text, limit=180),
            "delivery_completed": delivery_decision.completed,
            "delivery_reason": delivery_decision.reason,
            "judge_stop_reason": judge_stop_reason,
        },
    }


__all__ = [
    "_build_runtime_diagnostics",
    "_build_runtime_request_summary",
    "_preview",
]
