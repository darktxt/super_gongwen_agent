from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from agents.extensions.models.litellm_model import LitellmModel

from runtime_fallbacks import (
    COORDINATOR_REPAIR_PROFILE,
    JUDGE_REPAIR_PROFILE,
    STRUCTURED_OUTPUT_REPAIRER,
    _coerce_coordinator_result,
    _coerce_judge_result,
    _extract_last_response_text,
    _extract_last_run_data_text,
    _fallback_result_from_max_turns,
    _fallback_result_from_model_error,
    _fallback_result_from_text,
)
from runtime_judge_flow import (
    _apply_judge_feedback,
    _build_runtime_delivery_decision,
    _build_writer_input_after_coordinator,
    _detect_repeated_judge_feedback,
    _judge_agent,
    _mark_outline_auto_advance_exhausted,
    _should_auto_advance_from_outline,
    _should_run_judge,
)
from runtime_materials import (
    _iter_material_files,
    _read_material_text as _materials_read_material_text,
    _record_tool_event,
    _resolve_material_path,
    _search_materials_payload as _search_materials_payload_impl,
    grep_materials,
    list_materials,
    read_material,
    resolve_materials_root,
    search_materials,
)
from runtime_models import (
    ACTION_ALIASES,
    COORDINATOR_OUTPUT_CONTRACT,
    JUDGE_OUTPUT_CONTRACT,
    CoordinatorResult,
    CoordinatorTurnResult,
    JudgeResult,
    OutlineSectionResult,
    PendingQuestion,
    RuntimeContext,
    RuntimeDeliveryDecision,
    RuntimeOutcome,
)
from runtime_observability import _build_runtime_diagnostics, _build_runtime_request_summary
from runtime_prompting import (
    _build_judge_feedback_message,
    _build_judge_input,
    _build_outline_to_draft_message,
    _build_user_input,
    _coordinator_instructions,
    _judge_instructions,
)
from structured_output_repair import (
    build_judge_run_record,
    build_recovery_summary,
    classify_model_behavior_error,
    summarize_run_data,
)
from workspace.models import WorkspaceState

_read_material_text = _materials_read_material_text


def _search_materials_payload(materials_root: Path, query: str, limit: int) -> dict[str, Any]:
    return _search_materials_payload_impl(
        materials_root,
        query,
        limit,
        read_text_func=_read_material_text,
    )


class LiteLLMAgentsRuntime:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str = "",
        temperature: float | None = None,
        enable_tracing: bool = True,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model_name = str(model_name or "").strip()
        self.base_url = str(base_url or "").strip()
        self.temperature = temperature
        self.enable_tracing = bool(enable_tracing)
        if not self.model_name:
            raise RuntimeError("未配置 LITELLM_MODEL，无法运行 LiteLLM Agents Runtime。")
        self.model = LitellmModel(
            model=self.model_name,
            base_url=self.base_url or None,
            api_key=self.api_key or None,
        )

    @classmethod
    def from_config(cls, config: Any) -> "LiteLLMAgentsRuntime":
        return cls(
            api_key=getattr(config, "litellm_api_key", ""),
            model_name=getattr(config, "litellm_model", ""),
            base_url=getattr(config, "litellm_base_url", ""),
            temperature=getattr(config, "litellm_temperature", None),
            enable_tracing=getattr(config, "openai_agents_enable_tracing", True),
        )

    def _run_coordinator_turn(
        self,
        *,
        coordinator: Agent[Any],
        writer_input: str | list[dict[str, Any]],
        runtime_context: RuntimeContext,
        run_config: RunConfig,
        max_turns: int,
        fallback_handler: Callable[[Any], dict[str, Any]],
    ) -> CoordinatorTurnResult:
        try:
            run_result = Runner.run_sync(
                coordinator,
                writer_input,
                context=runtime_context,
                max_turns=max_turns,
                run_config=run_config,
                error_handlers={"max_turns": fallback_handler},
            )
            output = _coerce_coordinator_result(run_result.final_output)
            return CoordinatorTurnResult(
                output=output,
                raw_output=run_result.final_output,
                run_data=run_result,
                structured_output_succeeded=not isinstance(run_result.final_output, str),
                continue_flow=True,
                next_writer_input=run_result.to_input_list(mode="normalized"),
            )
        except ModelBehaviorError as exc:
            raw_output = _extract_last_run_data_text(exc.run_data) or str(exc)
            run_data_summary = summarize_run_data(exc.run_data)
            repair = STRUCTURED_OUTPUT_REPAIRER.recover(
                profile=COORDINATOR_REPAIR_PROFILE,
                raw_output=raw_output,
                run_data=exc.run_data,
                error_message=str(exc),
                has_tool_activity=bool(run_data_summary.get("has_tool_activity")),
            )
            error_summary = classify_model_behavior_error(
                repairer=STRUCTURED_OUTPUT_REPAIRER,
                profile=COORDINATOR_REPAIR_PROFILE,
                error_message=str(exc),
                last_text=str(raw_output or ""),
                run_data_summary=run_data_summary,
                model_name=self.model_name,
                base_url=self.base_url,
                repair_result=repair,
            )
            if repair.value is not None:
                output = repair.value
                return CoordinatorTurnResult(
                    output=output,
                    raw_output=raw_output,
                    run_data=exc.run_data,
                    structured_output_succeeded=False,
                    continue_flow=True,
                    next_writer_input=_build_writer_input_after_coordinator(writer_input, output),
                    error_summary=error_summary,
                    recovery_summary=build_recovery_summary(repair=repair, result=output),
                )
            output = _fallback_result_from_model_error(runtime_context, last_text=raw_output, error_summary=error_summary)
            return CoordinatorTurnResult(
                output=output,
                raw_output=raw_output,
                run_data=exc.run_data,
                structured_output_succeeded=False,
                continue_flow=False,
                next_writer_input=[],
                error_summary=error_summary,
                recovery_summary={
                    "fallback_source": "last_text" if str(raw_output or "").strip() else "max_turns_fallback",
                    "result_action": output.action,
                    "result_completion_mode": output.completion_mode,
                    "repair_steps": list(repair.repair_steps),
                },
            )

    def run_turn(
        self,
        *,
        session_id: str,
        workspace: WorkspaceState,
        user_input: str,
        working_root: str | Path | None = None,
        on_tool_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> RuntimeOutcome:
        resolved_working_root = Path(working_root).resolve() if working_root is not None else Path.cwd()
        max_turns = 8
        judge_max_rounds = 3
        runtime_context = RuntimeContext(
            session_id=session_id,
            working_root=resolved_working_root,
            materials_root=resolve_materials_root(resolved_working_root),
            workspace=workspace,
            on_tool_event=on_tool_event,
        )
        request_text = _build_user_input(workspace, user_input)
        request_summary = _build_runtime_request_summary(
            session_id=session_id,
            request_text=request_text,
            user_input=user_input,
            runtime_context=runtime_context,
            model_name=self.model_name,
            base_url=self.base_url,
            temperature=self.temperature,
            enable_tracing=self.enable_tracing,
            max_turns=max_turns,
            judge_max_rounds=judge_max_rounds,
        )
        coordinator = Agent(
            name="CoordinatorAgent",
            instructions=_coordinator_instructions(),
            model=self.model,
            model_settings=ModelSettings(temperature=self.temperature),
            output_type=CoordinatorResult,
            tool_use_behavior="run_llm_again",
            tools=[list_materials, search_materials, read_material, grep_materials],
        )
        judge = _judge_agent(self.model, self.temperature)
        run_config = RunConfig(
            workflow_name="super-gongwen-lite",
            tracing_disabled=not self.enable_tracing,
            trace_metadata={"session_id": session_id, "runtime": "litellm_agents_sdk"},
        )

        def _handle_max_turns(handler_input: Any) -> dict[str, Any]:
            return {
                "final_output": _fallback_result_from_max_turns(
                    runtime_context,
                    last_text=_extract_last_response_text(getattr(handler_input.run_data, "raw_responses", [])),
                )
            }

        judge_runs: list[dict[str, Any]] = []
        coordinator_error_summary: dict[str, Any] = {}
        coordinator_recovery_summary: dict[str, Any] = {}
        structured_output_succeeded = False
        auto_advance_after_max_judge = False
        judge_stop_reason = ""
        outline_auto_advance_count = 0
        max_outline_auto_advance = 2
        delivery_decision = RuntimeDeliveryDecision()
        try:
            writer_input: str | list[dict[str, Any]] = request_text
            run_result = None
            raw_output: Any = None
            output: CoordinatorResult | None = None
            latest_judge: JudgeResult | None = None
            for judge_round in range(1, judge_max_rounds + 1):
                coordinator_turn = self._run_coordinator_turn(
                    coordinator=coordinator,
                    writer_input=writer_input,
                    runtime_context=runtime_context,
                    run_config=run_config,
                    max_turns=max_turns,
                    fallback_handler=_handle_max_turns,
                )
                run_result = coordinator_turn.run_data
                raw_output = coordinator_turn.raw_output
                output = coordinator_turn.output
                structured_output_succeeded = coordinator_turn.structured_output_succeeded
                if coordinator_turn.error_summary:
                    coordinator_error_summary = dict(coordinator_turn.error_summary)
                if coordinator_turn.recovery_summary:
                    coordinator_recovery_summary = dict(coordinator_turn.recovery_summary)
                if _should_auto_advance_from_outline(output):
                    if outline_auto_advance_count >= max_outline_auto_advance:
                        output = _mark_outline_auto_advance_exhausted(output)
                        break
                    outline_auto_advance_count += 1
                    writer_input = list(coordinator_turn.next_writer_input)
                    writer_input.append({"role": "user", "content": _build_outline_to_draft_message(user_input)})
                    continue
                if not coordinator_turn.continue_flow or not _should_run_judge(output):
                    break
                judge_raw_output = None
                try:
                    judge_run = Runner.run_sync(
                        judge,
                        _build_judge_input(user_input, workspace, output),
                        context=runtime_context,
                        max_turns=2,
                        run_config=run_config,
                    )
                    judge_raw_output = judge_run.final_output
                    previous_judge = latest_judge
                    latest_judge = _coerce_judge_result(judge_run.final_output)
                    judge_runs.append(
                        build_judge_run_record(
                            round_no=judge_round,
                            judge_result=latest_judge,
                            raw_output=judge_raw_output,
                        )
                    )
                    if latest_judge.score == "pass":
                        output = _apply_judge_feedback(output, latest_judge, final_round=True)
                        break
                    repeated_feedback = _detect_repeated_judge_feedback(previous_judge, latest_judge)
                    if repeated_feedback is not None:
                        judge_stop_reason = repeated_feedback[0]
                        if repeated_feedback[1] not in output.major_risks:
                            output.major_risks.append(repeated_feedback[1])
                        break
                    writer_input = list(coordinator_turn.next_writer_input)
                    writer_input.append({"role": "user", "content": _build_judge_feedback_message(latest_judge)})
                    if judge_round == judge_max_rounds:
                        auto_advance_after_max_judge = True
                        judge_stop_reason = "judge_max_rounds_exhausted"
                        break
                except ModelBehaviorError as judge_exc:
                    repair = STRUCTURED_OUTPUT_REPAIRER.recover(
                        profile=JUDGE_REPAIR_PROFILE,
                        raw_output=judge_raw_output,
                        run_data=getattr(judge_exc, "run_data", None),
                        error_message=str(judge_exc),
                    )
                    if repair.value is not None:
                        previous_judge = latest_judge
                        latest_judge = repair.value
                        judge_runs.append(
                            build_judge_run_record(
                                round_no=judge_round,
                                judge_result=latest_judge,
                                raw_output=judge_raw_output,
                                repair=repair,
                                recovered=True,
                                default_source="judge_run_data",
                            )
                        )
                        if latest_judge.score == "pass":
                            output = _apply_judge_feedback(output, latest_judge, final_round=True)
                            break
                        repeated_feedback = _detect_repeated_judge_feedback(previous_judge, latest_judge)
                        if repeated_feedback is not None:
                            judge_stop_reason = repeated_feedback[0]
                            if repeated_feedback[1] not in output.major_risks:
                                output.major_risks.append(repeated_feedback[1])
                            break
                        writer_input = list(coordinator_turn.next_writer_input)
                        writer_input.append({"role": "user", "content": _build_judge_feedback_message(latest_judge)})
                        if judge_round == judge_max_rounds:
                            auto_advance_after_max_judge = True
                            judge_stop_reason = "judge_max_rounds_exhausted"
                            break
                        continue
                    judge_runs.append(
                        build_judge_run_record(
                            round_no=judge_round,
                            raw_output=judge_raw_output,
                            repair=repair,
                            error=judge_exc,
                            default_source="judge_run_data",
                        )
                    )
                    break
                except ValueError as judge_exc:
                    repair = STRUCTURED_OUTPUT_REPAIRER.recover(
                        profile=JUDGE_REPAIR_PROFILE,
                        raw_output=judge_raw_output,
                        error_message=str(judge_exc),
                    )
                    judge_runs.append(
                        build_judge_run_record(
                            round_no=judge_round,
                            raw_output=judge_raw_output,
                            repair=repair,
                            error=judge_exc,
                            default_source="judge_final_output",
                        )
                    )
                    break
            if output is None or run_result is None:
                raise RuntimeError("writer 未返回有效结果。")
            if auto_advance_after_max_judge:
                coordinator_turn = self._run_coordinator_turn(
                    coordinator=coordinator,
                    writer_input=writer_input,
                    runtime_context=runtime_context,
                    run_config=run_config,
                    max_turns=max_turns,
                    fallback_handler=_handle_max_turns,
                )
                run_result = coordinator_turn.run_data
                raw_output = coordinator_turn.raw_output
                output = coordinator_turn.output
                structured_output_succeeded = coordinator_turn.structured_output_succeeded
                if coordinator_turn.error_summary:
                    coordinator_error_summary = dict(coordinator_turn.error_summary)
                if coordinator_turn.recovery_summary:
                    coordinator_recovery_summary = dict(coordinator_turn.recovery_summary)
            if latest_judge is not None and latest_judge.score != "pass" and judge_stop_reason:
                output = _apply_judge_feedback(
                    output,
                    latest_judge,
                    final_round=True,
                    judge_stop_reason=judge_stop_reason,
                )
            delivery_decision = _build_runtime_delivery_decision(output, judge_stop_reason=judge_stop_reason)
            diagnostics = _build_runtime_diagnostics(
                request_summary=request_summary,
                run_data=run_result,
                raw_output=raw_output,
                tool_events=list(runtime_context.tool_events),
                result=output,
                delivery_decision=delivery_decision,
                structured_output_succeeded=structured_output_succeeded,
                error_summary=coordinator_error_summary,
                recovery_summary=coordinator_recovery_summary,
                judge_runs=judge_runs,
                judge_stop_reason=judge_stop_reason,
            )
        except ModelBehaviorError as exc:
            raw_output = _extract_last_run_data_text(exc.run_data) or str(exc)
            run_data_summary = summarize_run_data(exc.run_data)
            coordinator_repair = STRUCTURED_OUTPUT_REPAIRER.recover(
                profile=COORDINATOR_REPAIR_PROFILE,
                raw_output=raw_output,
                run_data=exc.run_data,
                error_message=str(exc),
                has_tool_activity=bool(run_data_summary.get("has_tool_activity")),
            )
            error_summary = classify_model_behavior_error(
                repairer=STRUCTURED_OUTPUT_REPAIRER,
                profile=COORDINATOR_REPAIR_PROFILE,
                error_message=str(exc),
                last_text=str(raw_output or ""),
                run_data_summary=run_data_summary,
                model_name=self.model_name,
                base_url=self.base_url,
                repair_result=coordinator_repair,
            )
            if coordinator_repair.value is not None:
                output = coordinator_repair.value
                recovery_summary = build_recovery_summary(repair=coordinator_repair, result=output)
            else:
                output = _fallback_result_from_model_error(runtime_context, last_text=raw_output, error_summary=error_summary)
                recovery_summary = build_recovery_summary(
                    repair=coordinator_repair,
                    result=output,
                    fallback_source="last_text" if str(raw_output or "").strip() else "max_turns_fallback",
                )
            delivery_decision = _build_runtime_delivery_decision(output)
            diagnostics = _build_runtime_diagnostics(
                request_summary=request_summary,
                run_data=exc.run_data,
                raw_output=raw_output,
                tool_events=list(runtime_context.tool_events),
                result=output,
                delivery_decision=delivery_decision,
                structured_output_succeeded=False,
                error_summary=error_summary,
                recovery_summary=recovery_summary,
                judge_runs=judge_runs,
                judge_stop_reason=judge_stop_reason,
            )
        return RuntimeOutcome(
            result=output,
            tool_events=list(runtime_context.tool_events),
            raw_output=raw_output,
            diagnostics=diagnostics,
            delivery_decision=delivery_decision,
        )


__all__ = [
    "ACTION_ALIASES",
    "COORDINATOR_OUTPUT_CONTRACT",
    "CoordinatorResult",
    "JudgeResult",
    "LiteLLMAgentsRuntime",
    "OutlineSectionResult",
    "PendingQuestion",
    "RuntimeContext",
    "RuntimeDeliveryDecision",
    "RuntimeOutcome",
    "_coerce_coordinator_result",
    "_coordinator_instructions",
    "_extract_last_run_data_text",
    "_fallback_result_from_max_turns",
    "_fallback_result_from_text",
    "_iter_material_files",
    "_judge_agent",
    "_judge_instructions",
    "_read_material_text",
    "_record_tool_event",
    "_resolve_material_path",
    "_search_materials_payload",
]
