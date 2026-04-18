from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable
import json
import re
import sys
import time

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

from agents import (
    Agent,
    ModelSettings,
    RunConfig,
    Runner,
    SQLiteSession,
)
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import ItemHelpers, MessageOutputItem, ToolCallOutputItem

from agents_runtime.context import CompiledBrainContext
from agents_runtime.protocol import (
    BrainRunError,
    BrainRunResult,
    BrainStepResult,
    LLMRequest,
    LLMResponse,
    OutputParseError,
    OutputParser,
    VALID_ACTIONS,
)
from .models import (
    AgentBrainStepOutput,
    AgentDraftSpecialistOutput,
    AgentFinalizeSpecialistOutput,
    AgentOutlineSpecialistOutput,
    AgentPolishSpecialistOutput,
)
from .tools import (
    AgentsToolRuntimeContext,
    build_material_function_tools,
    list_material_tool_specs,
)


class AgentsSdkBrainRunner:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        app_home: str | Path,
        base_url: str | None = None,
        timeout: float = 300.0,
        temperature: float | None = None,
        enable_tracing: bool = True,
        output_mode: str = "auto",
        workflow_name: str = "super-gongwen-agent",
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model_name = str(model or "").strip()
        self.base_url = str(base_url or "").strip()
        self.timeout = float(timeout)
        self.temperature = temperature
        self.enable_tracing = bool(enable_tracing)
        self.configured_output_mode = str(output_mode or "auto").strip().lower()
        self.output_mode = self._resolve_output_mode(self.configured_output_mode)
        self.workflow_name = str(workflow_name or "super-gongwen-agent").strip()
        self.runtime_workflow = "litellm"
        self.app_home = Path(app_home).expanduser().resolve()
        self._session_db_path = self.app_home / "agents_runtime" / "sessions.sqlite3"
        self._session_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_storage_available = True
        self._session_storage_error = ""
        self._fallback_parser = OutputParser()
        self._max_repair_attempts = 1
        self._max_specialist_feedback_rounds = 1
        self._runner_max_turns = sys.maxsize
        self.provider_profile = self._build_provider_profile()
        self._model = LitellmModel(
            model=self.model_name,
            base_url=self.base_url or None,
            api_key=self.api_key or None,
        )

    @classmethod
    def from_config(cls, config: Any) -> "AgentsSdkBrainRunner":
        return cls(
            api_key=str(getattr(config, "litellm_api_key", "") or ""),
            model=str(getattr(config, "litellm_model", "") or ""),
            app_home=getattr(config, "app_home"),
            base_url=str(getattr(config, "litellm_base_url", "") or ""),
            timeout=float(getattr(config, "litellm_timeout", 300.0) or 300.0),
            temperature=getattr(config, "litellm_temperature", None),
            enable_tracing=bool(getattr(config, "openai_agents_enable_tracing", True)),
            output_mode=str(getattr(config, "openai_agents_output_mode", "auto") or "auto"),
        )

    def run(
        self,
        compiled_context: CompiledBrainContext,
        *,
        session_id: str | None = None,
        working_root: str | Path | None = None,
        app_home: str | Path | None = None,
    ) -> BrainRunResult:
        request = self._build_request(compiled_context)
        response = LLMResponse(content="", model=self.model_name, raw_payload={})
        runtime_context = self._build_agents_tool_context(
            session_id=session_id,
            working_root=working_root,
            app_home=app_home,
        )
        specialist_trace: list[dict[str, Any]] = []
        run_meta: dict[str, Any] = {}
        try:
            result, run_meta = self._run_coordinator_with_recovery(
                instructions=compiled_context.system_prompt,
                request=request,
                session_id=session_id,
                runtime_context=runtime_context,
            )
            payload = self._coerce_run_result_payload(
                result,
                request=request,
                session_id=session_id,
            )
            payload, specialist_trace = self._apply_specialist_pipeline(
                payload,
                request=request,
                session_id=session_id,
            )
            step = BrainStepResult.from_dict(payload)
            orchestration_summary = self._build_orchestration_summary(
                step=step,
                decision_trace=specialist_trace,
            )
            response = LLMResponse(
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                model=self.model_name,
                raw_payload={
                    "sdk": "openai-agents",
                    "runtime_backend": "agents_sdk",
                    "runtime_workflow": self.runtime_workflow,
                    "provider_profile": dict(self.provider_profile),
                    "model_api": "litellm",
                    "material_tool_mode": "function_tool",
                    "output_mode": self.output_mode,
                    "last_response_id": result.last_response_id,
                    "raw_response_count": len(list(result.raw_responses or [])),
                    "final_output": payload,
                    **run_meta,
                    "session_id": session_id or "",
                    "tool_request_count": len(runtime_context.tool_requests),
                    "tool_result_count": len(runtime_context.tool_results),
                    "specialist_count": self._count_specialist_trace(specialist_trace),
                    "specialist_trace": self._specialist_trace_only(specialist_trace),
                    "decision_trace": specialist_trace,
                    "orchestration_summary": orchestration_summary,
                    "business_completion_declared": step.business_completion_declared,
                    "completion_mode": step.completion_mode,
                    "decision_rationale": step.decision_rationale,
                    "session_storage_available": self._session_storage_available,
                    "session_storage_error": self._session_storage_error,
                },
            )
            return BrainRunResult(
                request=request,
                response=response,
                step=step,
                tool_requests=list(runtime_context.tool_requests),
                tool_results=list(runtime_context.tool_results),
            )
        except Exception as exc:
            fallback_step = self._try_recover_step_from_exception(
                exc,
                request=request,
                session_id=session_id,
            )
            if fallback_step is not None:
                payload = fallback_step.to_dict()
                orchestration_summary = self._build_orchestration_summary(
                    step=fallback_step,
                    decision_trace=specialist_trace,
                )
                response = LLMResponse(
                    content=json.dumps(payload, ensure_ascii=False, indent=2),
                    model=self.model_name,
                    raw_payload={
                        "sdk": "openai-agents",
                        "runtime_backend": "agents_sdk",
                        "runtime_workflow": self.runtime_workflow,
                        "provider_profile": dict(self.provider_profile),
                        "model_api": "litellm",
                        "material_tool_mode": "function_tool",
                        "output_mode": self.output_mode,
                        "fallback_parser_used": True,
                        "session_id": session_id or "",
                        "error_preview": str(exc)[:1200],
                        "tool_request_count": len(runtime_context.tool_requests),
                        "tool_result_count": len(runtime_context.tool_results),
                        "specialist_count": self._count_specialist_trace(specialist_trace),
                        "specialist_trace": self._specialist_trace_only(specialist_trace),
                        "decision_trace": specialist_trace,
                        "orchestration_summary": orchestration_summary,
                        "business_completion_declared": fallback_step.business_completion_declared,
                        "completion_mode": fallback_step.completion_mode,
                        "decision_rationale": fallback_step.decision_rationale,
                        "session_storage_available": self._session_storage_available,
                        "session_storage_error": self._session_storage_error,
                    },
                )
                return BrainRunResult(
                    request=request,
                    response=response,
                    step=fallback_step,
                    tool_requests=list(runtime_context.tool_requests),
                    tool_results=list(runtime_context.tool_results),
                )
            if not response.content:
                response = LLMResponse(
                    content="",
                    model=self.model_name,
                    raw_payload={
                        "sdk": "openai-agents",
                        "runtime_backend": "agents_sdk",
                        "runtime_workflow": self.runtime_workflow,
                        "provider_profile": dict(self.provider_profile),
                        "model_api": "litellm",
                        "material_tool_mode": "function_tool",
                        "output_mode": self.output_mode,
                        "session_id": session_id or "",
                        "error": str(exc),
                        "tool_request_count": len(runtime_context.tool_requests),
                        "tool_result_count": len(runtime_context.tool_results),
                        "specialist_count": self._count_specialist_trace(specialist_trace),
                        "specialist_trace": self._specialist_trace_only(specialist_trace),
                        "decision_trace": specialist_trace,
                        "session_storage_available": self._session_storage_available,
                        "session_storage_error": self._session_storage_error,
                    },
                )
            raise BrainRunError(
                message=str(exc),
                request=request,
                response=response,
                raw_output=response.content,
            ) from exc

    def _build_request(self, compiled_context: CompiledBrainContext) -> LLMRequest:
        return LLMRequest(
            model=self.model_name,
            system_prompt=compiled_context.system_prompt,
            user_prompt=compiled_context.user_prompt,
            context_blocks=[compiled_context.action_playbook_block.to_dict()]
            + [block.to_dict() for block in compiled_context.attached_context_blocks],
            metadata={
                "token_budget_report": compiled_context.token_budget_report.to_dict(),
                "runtime_backend": "agents_sdk",
                "runtime_workflow": self.runtime_workflow,
                "provider_profile": dict(self.provider_profile),
                "material_tool_mode": "function_tool",
            },
        )

    def _build_agents_tool_context(
        self,
        *,
        session_id: str | None,
        working_root: str | Path | None,
        app_home: str | Path | None,
    ) -> AgentsToolRuntimeContext:
        resolved_working_root = (
            Path(working_root).resolve() if working_root is not None else Path.cwd().resolve()
        )
        resolved_app_home = (
            Path(app_home).resolve() if app_home is not None else self.app_home
        )
        return AgentsToolRuntimeContext(
            working_root=resolved_working_root,
            session_id=session_id,
            app_home=resolved_app_home,
        )

    def _build_agent(self, instructions: str) -> Agent[Any]:
        return self._build_coordinator_agent(instructions=instructions, allow_tools=True)

    def _build_coordinator_agent(
        self,
        *,
        instructions: str,
        allow_tools: bool,
        recovery_note: str = "",
    ) -> Agent[Any]:
        return Agent(
            name="EditorialBrainCoordinator",
            tools=build_material_function_tools() if allow_tools else [],
            instructions=self._build_runtime_instructions(
                instructions,
                allow_tools=allow_tools,
                recovery_note=recovery_note,
            ),
            model=self._model,
            tool_use_behavior="run_llm_again",
            model_settings=ModelSettings(
                temperature=self.temperature,
            ),
        )

    def list_available_tools(self) -> list[dict[str, Any]]:
        return list_material_tool_specs()

    def _run_coordinator_with_recovery(
        self,
        *,
        instructions: str,
        request: LLMRequest,
        session_id: str | None,
        runtime_context: AgentsToolRuntimeContext,
    ) -> tuple[Any, dict[str, Any]]:
        try:
            return (
                self._run_agent(
                    agent=self._build_coordinator_agent(
                        instructions=instructions,
                        allow_tools=True,
                    ),
                    request=request,
                    session_id=session_id,
                    runtime_context=runtime_context,
                ),
                {"coordinator_mode": "tool_enabled_json"},
            )
        except Exception as exc:
            business_action = self._business_action_from_tool_error(exc)
            if not business_action:
                raise
            recovery_request = self._build_business_action_recovery_request(
                request,
                business_action=business_action,
                error_text=str(exc),
            )
            result = self._run_agent(
                agent=self._build_coordinator_agent(
                    instructions=instructions,
                    allow_tools=False,
                    recovery_note=(
                        "上一轮错误地把业务动作当成了工具调用。"
                        f"本轮禁止再调用任何工具，必须直接输出合法 BrainStepResult JSON；"
                        f"如果确实缺材料，只能在 JSON 中给出 ask_user 或保守修订动作。"
                    ),
                ),
                request=recovery_request,
                session_id=None if session_id is None else f"{session_id}__tool_error_recovery",
            )
            return (
                result,
                {
                    "coordinator_mode": "tool_error_recovery_json",
                    "fallback_mode": "business_action_tool_error_to_json",
                    "recovered_business_action": business_action,
                    "initial_error_preview": str(exc)[:800],
                },
            )

    def _build_specialist_agent(
        self,
        *,
        name: str,
        instructions: str,
    ) -> Agent[Any]:
        return Agent(
            name=name,
            instructions=instructions,
            model=self._model,
            model_settings=ModelSettings(
                temperature=self.temperature,
            ),
        )

    def _build_session(self, session_id: str | None) -> SQLiteSession | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        if not self._session_storage_available:
            return None
        try:
            return SQLiteSession(session_id=normalized, db_path=self._session_db_path)
        except Exception as exc:
            # 某些运行环境会拒绝 sqlite 文件锁或 journal 写入，这里降级为无 session 运行，
            # 保证在线推理链路仍可用；领域态仍由 workspace.json 持久化。
            self._session_storage_available = False
            self._session_storage_error = str(exc)
            return None

    def _build_run_config(self, session_id: str | None) -> RunConfig:
        trace_metadata = {
            "component": "agents_runtime",
            "runtime_backend": "agents_sdk",
        }
        if session_id:
            trace_metadata["session_id"] = str(session_id)
        return RunConfig(
            tracing_disabled=not self.enable_tracing,
            workflow_name=self.workflow_name,
            trace_metadata=trace_metadata,
        )

    def _run_agent(
        self,
        *,
        agent: Agent[Any],
        request: LLMRequest,
        session_id: str | None,
        runtime_context: AgentsToolRuntimeContext | None = None,
        run_config: RunConfig | None = None,
    ) -> Any:
        run_kwargs: dict[str, Any] = {
            "context": runtime_context,
            "session": self._build_session(session_id),
            "run_config": run_config or self._build_run_config(session_id),
            "max_turns": self._runner_max_turns,
        }
        return Runner.run_sync(
            agent,
            self._render_user_content(request),
            **run_kwargs,
        )

    def _business_action_from_tool_error(self, exc: Exception) -> str:
        error_text = str(exc or "").strip()
        if not error_text:
            return ""
        match = None
        for pattern in (
            r"Tool\s+([A-Za-z0-9_:-]+)\s+not\s+found\s+in\s+agent",
            r"Unknown tool(?: name)?[:：]?\s*([A-Za-z0-9_:-]+)",
        ):
            match = re.search(pattern, error_text, re.IGNORECASE)
            if match:
                break
        if match is None:
            return ""
        tool_name = str(match.group(1) or "").strip()
        return tool_name if tool_name in VALID_ACTIONS else ""

    def _build_business_action_recovery_request(
        self,
        request: LLMRequest,
        *,
        business_action: str,
        error_text: str,
    ) -> LLMRequest:
        return LLMRequest(
            model=request.model,
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
            context_blocks=list(request.context_blocks)
            + [
                {
                    "title": "Protocol Recovery Notice",
                    "content": (
                        "上一轮发生协议错误：模型把业务动作当成了工具调用。\n"
                        f"误调用动作：{business_action}\n"
                        f"错误信息：{error_text}\n"
                        "本轮不得再调用任何工具，必须直接返回合法 BrainStepResult JSON。"
                    ),
                }
            ],
            metadata={
                **dict(request.metadata or {}),
                "protocol_recovery": "business_action_tool_error",
                "business_action_tool_error": business_action,
            },
        )

    def _build_specialist_run_config(self, component: str, session_id: str | None) -> RunConfig:
        trace_metadata = {
            "component": str(component or "").strip() or "specialist",
            "runtime_backend": "agents_sdk",
            "runtime_workflow": self.runtime_workflow,
        }
        if session_id:
            trace_metadata["session_id"] = str(session_id)
        return RunConfig(
            tracing_disabled=not self.enable_tracing,
            workflow_name=self.workflow_name + "-" + str(component or "specialist").replace("_", "-"),
            trace_metadata=trace_metadata,
        )

    def _specialist_trace_only(self, decision_trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in list(decision_trace or [])
            if str(item.get("kind", "") or "").strip() == "specialist"
        ]

    def _count_specialist_trace(self, decision_trace: list[dict[str, Any]]) -> int:
        return len(self._specialist_trace_only(decision_trace))

    def _build_orchestration_summary(
        self,
        *,
        step: BrainStepResult,
        decision_trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        initial_proposal: dict[str, Any] = {}
        final_decision: dict[str, Any] = {}
        specialist_feedback: list[dict[str, Any]] = []
        for item in list(decision_trace or []):
            kind = str(item.get("kind", "") or "").strip()
            if kind == "coordinator":
                stage = str(item.get("stage", "") or "").strip()
                compact = {
                    "stage": stage,
                    "action_taken": str(item.get("action_taken", "") or "").strip(),
                    "completion_mode": str(item.get("completion_mode", "") or "").strip(),
                    "business_completion_declared": bool(
                        item.get("business_completion_declared", False)
                    ),
                    "decision_rationale_preview": str(
                        item.get("decision_rationale_preview", "") or ""
                    ).strip(),
                }
                if stage == "initial_proposal":
                    initial_proposal = compact
                if stage == "final_decision":
                    final_decision = compact
                continue
            if kind == "specialist":
                specialist_feedback.append(
                    {
                        "actor": str(item.get("actor", "") or "").strip(),
                        "status": str(item.get("status", "") or "").strip(),
                        "feedback_verdict": str(item.get("feedback_verdict", "") or "").strip(),
                        "recommended_action": str(
                            item.get("recommended_action", "") or ""
                        ).strip(),
                        "requires_final_decision": bool(
                            item.get("requires_final_decision", False)
                        ),
                    }
                )
        if not final_decision:
            final_decision = {
                "stage": "final_decision",
                "action_taken": step.action_taken,
                "completion_mode": step.completion_mode,
                "business_completion_declared": step.business_completion_declared,
                "decision_rationale_preview": step.decision_rationale[:400],
            }
        return {
            "used_specialist": bool(specialist_feedback),
            "initial_proposal": initial_proposal,
            "specialist_feedback": specialist_feedback,
            "final_decision": final_decision,
            "business_completion_declared": step.business_completion_declared,
            "completion_mode": step.completion_mode,
            "assumptions": list(step.assumptions[:5]),
            "major_risks": list(step.major_risks[:5]),
        }

    def _apply_specialist_pipeline(
        self,
        payload: dict[str, Any],
        *,
        request: LLMRequest,
        session_id: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        action_taken = str(payload.get("action_taken", "") or "").strip()
        if action_taken == "build_outline":
            return self._finalize_step_with_specialist(
                payload,
                request=request,
                session_id=session_id,
                action_taken=action_taken,
                specialist_name="outline_specialist",
                producer=self._run_outline_specialist,
            )
        if action_taken in {"write_draft", "write_section", "revise_draft"}:
            return self._finalize_step_with_specialist(
                payload,
                request=request,
                session_id=session_id,
                action_taken=action_taken,
                specialist_name="draft_specialist",
                producer=self._run_draft_specialist,
            )
        if action_taken == "polish_language":
            return self._finalize_step_with_specialist(
                payload,
                request=request,
                session_id=session_id,
                action_taken=action_taken,
                specialist_name="polish_specialist",
                producer=self._run_polish_specialist,
            )
        if action_taken == "finalize":
            return self._finalize_step_with_specialist(
                payload,
                request=request,
                session_id=session_id,
                action_taken=action_taken,
                specialist_name="draft_specialist",
                producer=self._run_finalize_specialist,
            )

        fallback_step = self._try_build_step_from_payload(payload)
        return (fallback_step.to_dict() if fallback_step is not None else payload), []

    def _finalize_step_with_specialist(
        self,
        payload: dict[str, Any],
        *,
        request: LLMRequest,
        session_id: str | None,
        action_taken: str,
        specialist_name: str,
        producer: Callable[..., dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        decision_trace: list[dict[str, Any]] = [
            self._build_coordinator_trace_entry(
                payload,
                stage="initial_proposal",
                specialist_name=specialist_name,
            )
        ]
        try:
            started_at = time.perf_counter()
            specialist_payload = producer(
                request=request,
                payload=payload,
                session_id=session_id,
                action_taken=action_taken,
            )
            specialist_duration_ms = int((time.perf_counter() - started_at) * 1000)
            feedback = self._extract_specialist_feedback(specialist_payload)
            merged_payload = self._merge_specialist_action_payload(
                payload,
                action_taken=action_taken,
                specialist_payload=specialist_payload,
            )
            merged_payload = self._merge_feedback_into_payload(
                merged_payload,
                feedback=feedback,
            )
            specialist_trace = self._build_specialist_trace_entry(
                specialist_name=specialist_name,
                action_taken=action_taken,
                specialist_payload=specialist_payload,
                feedback=feedback,
                duration_ms=specialist_duration_ms,
                requires_final_decision=self._feedback_requires_final_decision(
                    action_taken=action_taken,
                    feedback=feedback,
                ),
            )
            decision_trace.append(specialist_trace)
            if self._feedback_requires_final_decision(
                action_taken=action_taken,
                feedback=feedback,
            ):
                final_payload = self._run_final_coordinator_decision(
                    base_request=request,
                    initial_payload=payload,
                    merged_payload=merged_payload,
                    specialist_name=specialist_name,
                    specialist_payload=specialist_payload,
                    feedback=feedback,
                    session_id=session_id,
                )
                final_step = BrainStepResult.from_dict(final_payload)
                decision_trace.append(
                    self._build_coordinator_trace_entry(
                        final_payload,
                        stage="final_decision",
                        specialist_name=specialist_name,
                    )
                )
                return final_step.to_dict(), decision_trace
            step = BrainStepResult.from_dict(merged_payload)
            decision_trace.append(
                self._build_coordinator_trace_entry(
                    step.to_dict(),
                    stage="final_decision",
                    specialist_name=specialist_name,
                )
            )
            return step.to_dict(), decision_trace
        except Exception as exc:
            fallback_step = self._try_build_step_from_payload(payload)
            if fallback_step is None:
                raise
            decision_trace.append(
                {
                    "kind": "specialist",
                    "actor": specialist_name,
                    "status": "fallback_to_coordinator",
                    "action_taken": action_taken,
                    "error_preview": str(exc)[:800],
                }
            )
            return fallback_step.to_dict(), decision_trace

    def _build_coordinator_trace_entry(
        self,
        payload: dict[str, Any],
        *,
        stage: str,
        specialist_name: str,
    ) -> dict[str, Any]:
        step = BrainStepResult.from_dict(payload)
        return {
            "kind": "coordinator",
            "actor": "coordinator",
            "stage": stage,
            "action_taken": step.action_taken,
            "business_completion_declared": step.business_completion_declared,
            "completion_mode": step.completion_mode,
            "decision_rationale_preview": step.decision_rationale[:400],
            "assumption_count": len(step.assumptions),
            "major_risk_count": len(step.major_risks),
            "related_specialist": specialist_name,
        }

    def _extract_specialist_feedback(self, specialist_payload: dict[str, Any]) -> dict[str, Any]:
        raw_feedback = specialist_payload.get("feedback")
        if not isinstance(raw_feedback, dict):
            return {}
        recommended_action = str(raw_feedback.get("recommended_action", "") or "").strip()
        return {
            "verdict": str(raw_feedback.get("verdict", "") or "").strip(),
            "recommended_action": recommended_action if recommended_action in VALID_ACTIONS else "",
            "rationale": str(raw_feedback.get("rationale", "") or "").strip(),
            "assumptions": [
                str(item).strip()
                for item in list(raw_feedback.get("assumptions", []) or [])
                if str(item).strip()
            ][:6],
            "major_risks": [
                str(item).strip()
                for item in list(raw_feedback.get("major_risks", []) or [])
                if str(item).strip()
            ][:6],
        }

    def _feedback_requires_final_decision(
        self,
        *,
        action_taken: str,
        feedback: dict[str, Any],
    ) -> bool:
        verdict = str(feedback.get("verdict", "") or "").strip().lower()
        recommended_action = str(feedback.get("recommended_action", "") or "").strip()
        if recommended_action and recommended_action != action_taken:
            return True
        return verdict in {"adjust", "redirect", "block", "alternate"}

    def _build_specialist_trace_entry(
        self,
        *,
        specialist_name: str,
        action_taken: str,
        specialist_payload: dict[str, Any],
        feedback: dict[str, Any],
        duration_ms: int,
        requires_final_decision: bool,
    ) -> dict[str, Any]:
        output_fields = sorted(
            key for key in specialist_payload.keys() if str(key).strip() and key != "feedback"
        )
        return {
            "kind": "specialist",
            "actor": specialist_name,
            "status": "applied",
            "action_taken": action_taken,
            "output_fields": output_fields,
            "duration_ms": duration_ms,
            "feedback_verdict": str(feedback.get("verdict", "") or "").strip(),
            "recommended_action": str(feedback.get("recommended_action", "") or "").strip(),
            "feedback_rationale_preview": str(feedback.get("rationale", "") or "").strip()[:400],
            "assumption_count": len(list(feedback.get("assumptions", []) or [])),
            "major_risk_count": len(list(feedback.get("major_risks", []) or [])),
            "requires_final_decision": bool(requires_final_decision),
        }

    def _merge_feedback_into_payload(
        self,
        payload: dict[str, Any],
        *,
        feedback: dict[str, Any],
    ) -> dict[str, Any]:
        if not feedback:
            return dict(payload)
        merged = dict(payload)
        if not str(merged.get("decision_rationale", "") or "").strip() and str(
            feedback.get("rationale", "") or ""
        ).strip():
            merged["decision_rationale"] = str(feedback.get("rationale", "") or "").strip()
        if not list(merged.get("assumptions", []) or []):
            merged["assumptions"] = list(feedback.get("assumptions", []) or [])
        if not list(merged.get("major_risks", []) or []):
            merged["major_risks"] = list(feedback.get("major_risks", []) or [])
        return merged

    def _run_final_coordinator_decision(
        self,
        *,
        base_request: LLMRequest,
        initial_payload: dict[str, Any],
        merged_payload: dict[str, Any],
        specialist_name: str,
        specialist_payload: dict[str, Any],
        feedback: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        request = self._build_final_decision_request(
            base_request=base_request,
            initial_payload=initial_payload,
            merged_payload=merged_payload,
            specialist_name=specialist_name,
            specialist_payload=specialist_payload,
            feedback=feedback,
        )
        result = self._run_agent(
            agent=self._build_coordinator_agent(
                instructions=base_request.system_prompt,
                allow_tools=False,
                recovery_note=(
                    "你正在根据 specialist 反馈做最终业务决策。"
                    "本轮不得再调用工具；你可以维持原动作，也可以改成更合适的动作，但必须给出合法 BrainStepResult JSON。"
                ),
            ),
            request=request,
            session_id=None if session_id is None else f"{session_id}__final_decision",
            run_config=self._build_specialist_run_config("coordinator_final_decision", session_id),
        )
        return self._coerce_run_result_payload(
            result,
            request=request,
            session_id=None if session_id is None else f"{session_id}__final_decision",
        )

    def _build_final_decision_request(
        self,
        *,
        base_request: LLMRequest,
        initial_payload: dict[str, Any],
        merged_payload: dict[str, Any],
        specialist_name: str,
        specialist_payload: dict[str, Any],
        feedback: dict[str, Any],
    ) -> LLMRequest:
        return LLMRequest(
            model=self.model_name,
            system_prompt=base_request.system_prompt,
            user_prompt=(
                "你已经收到了 specialist 的反馈。现在请作为 coordinator 做最终业务决策。"
                "如果 specialist 的反馈有价值，可以调整 action；如果原方案仍更合适，也可以维持原 action。"
                "你的输出必须是一个合法 BrainStepResult JSON。"
            ),
            context_blocks=[
                {
                    "title": "Coordinator Proposal",
                    "content": json.dumps(initial_payload, ensure_ascii=False, indent=2),
                },
                {
                    "title": "Specialist Feedback",
                    "content": json.dumps(
                        {
                            "specialist": specialist_name,
                            "feedback": feedback,
                            "specialist_output": specialist_payload,
                            "candidate_payload_after_merge": merged_payload,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
            ]
            + self._select_specialist_context_blocks(base_request.context_blocks),
            metadata={
                **dict(base_request.metadata or {}),
                "runtime_backend": "agents_sdk",
                "runtime_workflow": self.runtime_workflow,
                "coordinator_stage": "final_decision",
                "specialist_name": specialist_name,
            },
        )

    def _try_build_step_from_payload(self, payload: dict[str, Any]) -> BrainStepResult | None:
        try:
            return BrainStepResult.from_dict(payload)
        except Exception:
            return None

    def _current_action_payload(
        self,
        payload: dict[str, Any],
        *,
        action_taken: str,
    ) -> dict[str, Any]:
        action_payload = payload.get("action_payload")
        if not isinstance(action_payload, dict):
            return {}
        nested = action_payload.get(action_taken)
        if isinstance(nested, dict):
            return dict(nested)
        return dict(action_payload)

    def _pick_first_text(self, payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = str(payload.get(key, "") or "").strip()
            if value:
                return value
        return ""

    def _build_specialist_action_payload(
        self,
        *,
        action_taken: str,
        current_payload: dict[str, Any],
        specialist_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action_taken == "build_outline":
            return {
                "outline_text": self._pick_first_text(specialist_payload, "outline_text")
                or self._pick_first_text(current_payload, "outline_text"),
                "outline_sections": list(
                    specialist_payload.get("outline_sections")
                    or current_payload.get("outline_sections")
                    or []
                ),
            }

        if action_taken == "write_draft":
            return {
                "draft_text": self._pick_first_text(
                    specialist_payload,
                    "draft_text",
                    "revised_text",
                )
                or self._pick_first_text(current_payload, "draft_text"),
            }

        if action_taken == "write_section":
            return {
                "section_id": self._pick_first_text(specialist_payload, "section_id")
                or self._pick_first_text(current_payload, "section_id"),
                "section_text": self._pick_first_text(
                    specialist_payload,
                    "section_text",
                    "draft_text",
                )
                or self._pick_first_text(current_payload, "section_text"),
            }

        if action_taken == "revise_draft":
            return {
                "revised_text": self._pick_first_text(
                    specialist_payload,
                    "revised_text",
                    "draft_text",
                )
                or self._pick_first_text(current_payload, "revised_text", "draft_text"),
            }

        if action_taken == "polish_language":
            return {
                "polished_text": self._pick_first_text(specialist_payload, "polished_text")
                or self._pick_first_text(current_payload, "polished_text"),
            }
        if action_taken == "finalize":
            return {
                "final_text": self._pick_first_text(
                    specialist_payload,
                    "final_text",
                    "delivered_draft",
                )
                or self._pick_first_text(
                    current_payload,
                    "final_text",
                    "delivered_draft",
                ),
            }

        return dict(current_payload)

    def _merge_specialist_action_payload(
        self,
        payload: dict[str, Any],
        *,
        action_taken: str,
        specialist_payload: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(payload)
        merged["action_payload"] = {
            action_taken: self._build_specialist_action_payload(
                action_taken=action_taken,
                current_payload=self._current_action_payload(payload, action_taken=action_taken),
                specialist_payload=specialist_payload,
            )
        }
        return merged

    def _run_outline_specialist(
        self,
        *,
        request: LLMRequest,
        payload: dict[str, Any],
        session_id: str | None,
        action_taken: str,
    ) -> dict[str, Any]:
        result = self._run_agent(
            agent=self._build_specialist_agent(
                name="EditorialOutlineSpecialist",
                instructions=self._build_outline_specialist_instructions(),
            ),
            request=self._build_specialist_request(
                base_request=request,
                payload=payload,
                action_taken=action_taken,
            ),
            session_id=None if session_id is None else f"{session_id}__outline",
            run_config=self._build_specialist_run_config("outline_specialist", session_id),
        )
        return self._parse_typed_json_output(
            result,
            AgentOutlineSpecialistOutput,
            request=self._build_specialist_request(
                base_request=request,
                payload=payload,
                action_taken=action_taken,
            ),
            session_id=None if session_id is None else f"{session_id}__outline",
        )

    def _run_draft_specialist(
        self,
        *,
        request: LLMRequest,
        payload: dict[str, Any],
        session_id: str | None,
        action_taken: str,
    ) -> dict[str, Any]:
        result = self._run_agent(
            agent=self._build_specialist_agent(
                name="EditorialDraftSpecialist",
                instructions=self._build_draft_specialist_instructions(),
            ),
            request=self._build_specialist_request(
                base_request=request,
                payload=payload,
                action_taken=action_taken,
            ),
            session_id=None if session_id is None else f"{session_id}__draft",
            run_config=self._build_specialist_run_config("draft_specialist", session_id),
        )
        return self._parse_typed_json_output(
            result,
            AgentDraftSpecialistOutput,
            request=self._build_specialist_request(
                base_request=request,
                payload=payload,
                action_taken=action_taken,
            ),
            session_id=None if session_id is None else f"{session_id}__draft",
        )

    def _run_polish_specialist(
        self,
        *,
        request: LLMRequest,
        payload: dict[str, Any],
        session_id: str | None,
        action_taken: str,
    ) -> dict[str, Any]:
        result = self._run_agent(
            agent=self._build_specialist_agent(
                name="EditorialPolishSpecialist",
                instructions=self._build_polish_specialist_instructions(),
            ),
            request=self._build_specialist_request(
                base_request=request,
                payload=payload,
                action_taken=action_taken,
            ),
            session_id=None if session_id is None else f"{session_id}__polish",
            run_config=self._build_specialist_run_config("polish_specialist", session_id),
        )
        return self._parse_typed_json_output(
            result,
            AgentPolishSpecialistOutput,
            request=self._build_specialist_request(
                base_request=request,
                payload=payload,
                action_taken=action_taken,
            ),
            session_id=None if session_id is None else f"{session_id}__polish",
        )

    def _run_finalize_specialist(
        self,
        *,
        request: LLMRequest,
        payload: dict[str, Any],
        session_id: str | None,
        action_taken: str,
    ) -> dict[str, Any]:
        finalize_request = self._build_finalize_specialist_request(
            base_request=request,
            payload=payload,
        )
        result = self._run_agent(
            agent=self._build_specialist_agent(
                name="EditorialDraftSpecialist",
                instructions=self._build_finalize_specialist_instructions(),
            ),
            request=finalize_request,
            session_id=None if session_id is None else f"{session_id}__finalize",
            run_config=self._build_specialist_run_config("finalize_specialist", session_id),
        )
        return self._parse_typed_json_output(
            result,
            AgentFinalizeSpecialistOutput,
            request=finalize_request,
            session_id=None if session_id is None else f"{session_id}__finalize",
        )

    def _build_specialist_request(
        self,
        *,
        base_request: LLMRequest,
        payload: dict[str, Any],
        action_taken: str,
    ) -> LLMRequest:
        context_blocks = [
            {
                "title": "Coordinator Proposal",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ] + self._select_specialist_context_blocks(base_request.context_blocks)
        return LLMRequest(
            model=self.model_name,
            system_prompt=f"{action_taken}_specialist",
            user_prompt=self._build_specialist_user_prompt(action_taken),
            context_blocks=context_blocks,
            metadata={
                **dict(base_request.metadata or {}),
                "specialist_action": action_taken,
                "runtime_backend": "agents_sdk",
                "runtime_workflow": self.runtime_workflow,
            },
        )

    def _build_finalize_specialist_request(
        self,
        *,
        base_request: LLMRequest,
        payload: dict[str, Any],
    ) -> LLMRequest:
        context_blocks = [
            {
                "title": "Coordinator Proposal",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ] + self._select_specialist_context_blocks(base_request.context_blocks)
        return LLMRequest(
            model=self.model_name,
            system_prompt="finalize_specialist",
            user_prompt=(
                "请先判断 coordinator 当前关于 finalize 的方案是否合理，再输出可直接交付的 final_text。"
                "如果你认为应先改为 revise_draft、polish_language 或 ask_user，请在 feedback 中明确指出原因。"
                "缺少 evidence 不是硬门槛；可以按现有材料给出保守交付稿，但必须保持正式公文语体，并在 feedback 中披露 assumptions 和 major_risks。"
                "不要输出 BrainStepResult。"
                '最终只输出一个 JSON object，例如 {"final_text":"...","feedback":{"verdict":"support","rationale":"..."}}。'
            ),
            context_blocks=context_blocks,
            metadata={
                **dict(base_request.metadata or {}),
                "specialist_action": "finalize",
                "runtime_backend": "agents_sdk",
                "runtime_workflow": self.runtime_workflow,
            },
        )

    def _build_finalize_specialist_instructions(self) -> str:
        return (
            "你是 super-gongwen-agent 的 finalize specialist，由 draft_specialist 负责终稿级协作。"
            "你的职责是在不虚构事实的前提下，对 coordinator 拟定的 finalize 方案做终稿级通读、定稿和风险反馈。"
            "缺少 evidence 不等于必须拒绝交付；只要正文在现有边界内已经可用，你应优先给出保守但可交付的 final_text。"
            "如果你判断当前仍应改为 revise_draft、polish_language 或 ask_user，请在 feedback 中明确建议动作、理由、assumptions 和 major_risks。"
            "你不是最终业务决策者，不能直接输出 BrainStepResult。"
            + self._build_specialist_guardrails()
        )

    def _select_specialist_context_blocks(
        self,
        context_blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        allowed_titles = {
            "User Thread",
            "Decision Snapshot",
            "Writing Brief",
            "Directive Ledger",
            "Evidence Snapshot",
            "Quality Signals",
            "Retrieved Materials",
            "Current Draft and Outline",
            "Current Self Review",
            "Recent Revision History",
            "Recent Brain Trace",
        }
        for block in list(context_blocks or []):
            title = str(block.get("title", "") or "").strip()
            if title in allowed_titles:
                selected.append(dict(block))
        if selected:
            return selected
        return [dict(block) for block in list(context_blocks or [])[:8] if isinstance(block, dict)]

    def _build_specialist_user_prompt(self, action_taken: str) -> str:
        if action_taken == "build_outline":
            return (
                "请先判断 coordinator 当前关于 build_outline 的方案是否合理，再生成提纲中间产物。"
                "你可以支持原方案，也可以指出应先补结构、改动作或向用户追问的理由。"
                "输出 outline_text 和/或 outline_sections；同时可以在 feedback 中给出 verdict、recommended_action、rationale、assumptions、major_risks。"
                "不要直接 finalize，也不要输出 BrainStepResult。"
                '最终只输出一个 JSON object，例如 {"outline_text":"...","outline_sections":[...],"feedback":{"verdict":"support","rationale":"..."}}。'
            )
        if action_taken == "write_draft":
            return (
                "请先判断 coordinator 当前关于 write_draft 的方案是否合理，再生成可直接写回正文的 draft_text。"
                "如果你认为应该先补结构、改为 revise_draft、改为 ask_user 或采用更保守的完成方式，可以在 feedback 中明确提出。"
                "不要直接 finalize，也不要输出 BrainStepResult。"
                '最终只输出一个 JSON object，例如 {"draft_text":"...","feedback":{"verdict":"adjust","recommended_action":"build_outline","rationale":"..."}}。'
            )
        if action_taken == "write_section":
            return (
                "请先判断 coordinator 当前关于 write_section 的方案是否合理，再补写目标 section_id 对应的 section_text。"
                "如果 section_id 不合理、上下文不足或更适合改动作，请在 feedback 中明确指出。"
                "不要直接 finalize，也不要输出 BrainStepResult。"
                '最终只输出一个 JSON object，例如 {"section_id":"...","section_text":"...","feedback":{"verdict":"support","rationale":"..."}}。'
            )
        if action_taken == "revise_draft":
            return (
                "请先判断 coordinator 当前关于 revise_draft 的方案是否合理，再根据当前正文生成 revised_text。"
                "如果问题并非修订能解决，而是需要补材料、重建结构或 ask_user，请在 feedback 中明确提出。"
                "必须保留已要求保留的事实、结构和口径，不要直接 finalize，也不要输出 BrainStepResult。"
                '最终只输出一个 JSON object，例如 {"revised_text":"...","feedback":{"verdict":"redirect","recommended_action":"ask_user","rationale":"..."}}。'
            )
        if action_taken == "polish_language":
            return (
                "请先判断 coordinator 当前关于 polish_language 的方案是否合理，再输出 polished_text。"
                "如果问题并不只是语言层，而是结构、材料或任务边界问题，请在 feedback 中明确指出。"
                "不要凭空新增事实，不要直接 finalize，也不要输出 BrainStepResult。"
                '最终只输出一个 JSON object，例如 {"polished_text":"...","feedback":{"verdict":"support","rationale":"..."}}。'
            )
        return (
            "请先评估 coordinator 当前方案，再生成该动作所需的中间产物。"
            "你可以在 feedback 中表达支持、反对、补充和替代建议，但不要直接 finalize，不要输出 BrainStepResult。"
            "最终只输出一个 JSON object。"
        )

    def _build_specialist_guardrails(self) -> str:
        return (
            "共同约束："
            "1. 优先遵循 Writing Brief、Directive Ledger、Evidence Snapshot 和 Coordinator Proposal。"
            "2. 输出必须符合中文公文语体，表达正式、克制、清楚，不写口号式空话。"
            "3. 没有证据支持的事实、数据、案例、单位表态、责任安排，不得擅自补写成既成事实。"
            "4. 措施表述要尽量写清抓手、责任主体、推进方式和闭环要求，不要只写原则态度。"
            "5. 如果你认为 coordinator 当前方案不够好，可以在 feedback 中明确提出更合适的 action、理由、假设和主要风险。"
            "6. 如果现有信息只够形成局部文本或保守表达，就保持克制，不要为了完整而编造。"
            "7. 最终只输出一个 JSON object，不得输出解释、Markdown 代码块或额外分析。"
        )

    def _build_outline_specialist_instructions(self) -> str:
        return (
            "你是 super-gongwen-agent 的 outline_specialist。"
            "你负责评估当前提纲策略是否合适，并把任务整理成结构完整、层次清楚、可用于后续起草的提纲中间产物。"
            "你不是最终决策者，不能直接 finalize，但可以建议 coordinator 改动作或 ask_user。"
            + self._build_specialist_guardrails()
        )

    def _build_draft_specialist_instructions(self) -> str:
        return (
            "你是 super-gongwen-agent 的 draft_specialist。"
            "你负责评估当前正文推进策略是否合适，并生成正文、分节正文或修订稿等中间文本产物。"
            "你不是最终决策者，不能直接 finalize，但可以建议 coordinator 改动作或 ask_user。"
            + self._build_specialist_guardrails()
        )

    def _build_polish_specialist_instructions(self) -> str:
        return (
            "你是 super-gongwen-agent 的 polish_specialist。"
            "你负责评估当前是否适合做语言层润色，并在必要时输出 polished_text。"
            "你不负责新增事实或擅改结构，也不能直接 finalize，但可以建议 coordinator 改动作或 ask_user。"
            + self._build_specialist_guardrails()
        )

    def _render_user_content(self, request: LLMRequest) -> str:
        lines = [request.user_prompt.strip()]

        if request.context_blocks:
            lines.append("")
            lines.append("补充上下文：")
            for index, block in enumerate(request.context_blocks, start=1):
                title = str(block.get("title", "") or f"Context {index}")
                content = str(block.get("content", "") or "").strip()
                lines.append(f"## {title}")
                lines.append(content or "[empty]")

        if request.metadata:
            lines.append("")
            lines.append("运行时元数据：")
            lines.append(json.dumps(request.metadata, ensure_ascii=False, indent=2))

        return "\n".join(lines).strip()

    def _try_parse_fallback_step(self, error_text: str) -> BrainStepResult | None:
        text = str(error_text or "").strip()
        if not text:
            return None
        try:
            return self._fallback_parser.parse(text)
        except OutputParseError:
            return None

    def _try_recover_step_from_exception(
        self,
        exc: Exception,
        *,
        request: LLMRequest,
        session_id: str | None,
    ) -> BrainStepResult | None:
        error_text = str(exc or "").strip()
        if not error_text:
            return None
        direct = self._try_parse_fallback_step(error_text)
        if direct is not None:
            return direct
        try:
            return self._parse_text_payload_with_repair(
                error_text,
                request=request,
                session_id=session_id,
            )
        except Exception:
            return None

    def _coerce_run_result_payload(
        self,
        result: Any,
        *,
        request: LLMRequest,
        session_id: str | None,
    ) -> dict[str, Any]:
        final_output = getattr(result, "final_output", None)
        if isinstance(final_output, AgentBrainStepOutput):
            return final_output.to_brain_step_dict()
        if hasattr(final_output, "to_brain_step_dict"):
            return dict(final_output.to_brain_step_dict())

        raw_text = self._extract_text_output(result)
        parsed = self._parse_text_payload_with_repair(
            raw_text,
            request=request,
            session_id=session_id,
        )
        return parsed.to_dict()

    def _extract_text_output(self, result: Any) -> str:
        final_output = getattr(result, "final_output", None)
        if isinstance(final_output, str) and final_output.strip():
            return final_output
        if isinstance(final_output, AgentBrainStepOutput):
            return json.dumps(final_output.to_brain_step_dict(), ensure_ascii=False, indent=2)
        if isinstance(final_output, dict):
            return json.dumps(final_output, ensure_ascii=False, indent=2)

        for item in reversed(list(getattr(result, "new_items", []) or [])):
            if isinstance(item, MessageOutputItem):
                text_output = ItemHelpers.text_message_output(item)
                if text_output:
                    return text_output
            if isinstance(item, ToolCallOutputItem):
                tool_output = getattr(item, "output", None)
                if isinstance(tool_output, str) and tool_output.strip():
                    return tool_output

        if final_output is None:
            raise OutputParseError("Agents SDK text output is empty.")
        return str(final_output)

    def _parse_json_object_output(self, raw_text: str) -> dict[str, Any]:
        text = str(raw_text or "").strip()
        if not text:
            raise OutputParseError("JSON output is empty.")
        for candidate in self._fallback_parser._candidate_json_strings(text):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise OutputParseError(f"Failed to parse JSON object from output: {text[:300]}")

    def _parse_typed_json_output(
        self,
        result: Any,
        output_cls: Any,
        *,
        request: LLMRequest,
        session_id: str | None,
    ) -> dict[str, Any]:
        final_output = getattr(result, "final_output", None)
        if isinstance(final_output, output_cls):
            return final_output.model_dump(exclude_defaults=True)
        if hasattr(final_output, "model_dump"):
            try:
                validated = output_cls.model_validate(final_output.model_dump())
                return validated.model_dump(exclude_defaults=True)
            except Exception:
                pass

        raw_text = self._extract_text_output(result)
        try:
            parsed = self._parse_json_object_output(raw_text)
            validated = output_cls.model_validate(parsed)
            return validated.model_dump(exclude_defaults=True)
        except Exception as exc:
            repaired = self._attempt_repair_json_object_output(
                raw_text,
                request=request,
                session_id=session_id,
                output_cls=output_cls,
                reason=str(exc),
            )
            if repaired is None:
                raise
            return repaired

    def _resolve_output_mode(self, configured_output_mode: str) -> str:
        _ = str(configured_output_mode or "auto").strip().lower()
        # LiteLLM provider 在 structured output / tool calling 的组合上稳定性差异很大，
        # 主控与 specialist 统一退回文本 JSON 协议，由运行时负责解析与修复。
        return "text"

    def _build_runtime_instructions(
        self,
        instructions: str,
        *,
        allow_tools: bool = True,
        recovery_note: str = "",
    ) -> str:
        normalized = str(instructions or "").strip()
        tool_suffix = (
            "\n\n运行时协议要求：\n"
            "1. 最终回答必须包含且只包含一个可解析的 JSON object。\n"
            "2. 不要输出自然语言解释、不要只输出分析结论、不要输出 Markdown 代码块。\n"
            "3. 即使模型会生成 <think> 或推理内容，你也必须在最后给出完整 JSON object。\n"
            "4. build_outline、write_draft、write_section、revise_draft、polish_language、ask_user、finalize 都不是工具名；它们只能出现在最终 JSON 的 action_taken 中，绝不能以 tool/function call 方式调用。\n"
            "5. 不要输出 tool_requests 之类的中间协议字段。\n"
            "6. 如适用，请同时输出 business_completion_declared、completion_mode、decision_rationale、assumptions、major_risks。\n"
        )
        if allow_tools:
            tool_suffix += (
                "7. 当前只允许使用 search、list、read、grep 访问 materials 内材料。\n"
                "8. 优先 search 或 list 找文件，再用 read；只有需要精确定位短语时再用 grep。\n"
                "9. 需要补材料时直接调用可用工具，工具调用结束后继续输出最终 BrainStepResult JSON。\n"
            )
        else:
            tool_suffix += (
                "7. 本轮已禁用全部工具；你不能再调用任何工具，只能直接输出 JSON 决策。\n"
            )
        if recovery_note:
            tool_suffix += f"10. {str(recovery_note).strip()}\n"
        normalized += tool_suffix
        return normalized

    def _build_provider_profile(self) -> dict[str, Any]:
        provider_name = self._infer_provider_name(self.model_name)
        return {
            "workflow": self.runtime_workflow,
            "provider": provider_name,
            "model": self.model_name,
            "has_api_key": bool(self.api_key),
            "has_base_url": bool(self.base_url),
        }

    def _infer_provider_name(self, model_name: str) -> str:
        normalized = str(model_name or "").strip()
        for separator in ("/", ":"):
            if separator in normalized:
                head = normalized.split(separator, 1)[0].strip()
                if head:
                    return head
        return "litellm"

    def _parse_text_payload_with_repair(
        self,
        raw_text: str,
        *,
        request: LLMRequest,
        session_id: str | None,
    ) -> BrainStepResult:
        try:
            return self._fallback_parser.parse(raw_text)
        except OutputParseError as exc:
            repaired = self._attempt_repair_output(
                raw_text,
                request=request,
                session_id=session_id,
                reason=str(exc),
            )
            if repaired is None:
                raise
            return repaired

    def _attempt_repair_output(
        self,
        raw_text: str,
        *,
        request: LLMRequest,
        session_id: str | None,
        reason: str,
    ) -> BrainStepResult | None:
        text = str(raw_text or "").strip()
        if not text:
            return None

        last_error = reason
        for _ in range(self._max_repair_attempts):
            repair_result = self._run_agent(
                agent=self._build_repair_agent(),
                request=self._build_repair_request(request, text, last_error),
                session_id=None if session_id is None else f"{session_id}__repair",
            )
            repaired_text = self._extract_text_output(repair_result)
            try:
                return self._fallback_parser.parse(repaired_text)
            except OutputParseError as exc:
                last_error = str(exc)
                text = repaired_text
                continue
        return None

    def _attempt_repair_json_object_output(
        self,
        raw_text: str,
        *,
        request: LLMRequest,
        session_id: str | None,
        output_cls: Any,
        reason: str,
    ) -> dict[str, Any] | None:
        text = str(raw_text or "").strip()
        if not text:
            return None

        last_error = reason
        schema_name = str(getattr(output_cls, "__name__", "JsonObject") or "JsonObject")
        for _ in range(self._max_repair_attempts):
            repair_result = self._run_agent(
                agent=self._build_json_object_repair_agent(schema_name=schema_name),
                request=self._build_json_object_repair_request(
                    original_request=request,
                    raw_text=text,
                    parse_error=last_error,
                    schema_name=schema_name,
                ),
                session_id=None if session_id is None else f"{session_id}__repair",
            )
            repaired_text = self._extract_text_output(repair_result)
            try:
                parsed = self._parse_json_object_output(repaired_text)
                validated = output_cls.model_validate(parsed)
                return validated.model_dump(exclude_defaults=True)
            except Exception as exc:
                last_error = str(exc)
                text = repaired_text
                continue
        return None

    def _build_repair_agent(self) -> Agent[Any]:
        return Agent(
            name="EditorialBrainJsonRepair",
            instructions=(
                "你是一个 JSON 修复器。你的唯一任务是把上一个模型输出修正为合法的 "
                "BrainStepResult JSON。"
                "你必须只输出一个 JSON object，不得输出解释、不得输出 Markdown、"
                "不得只输出分析。"
                "如果原始输出包含 <think>、自然语言分析、代码块或半截 JSON，"
                "请提取其中真实意图并补成完整 JSON。"
            ),
            model=self._model,
            output_type=None,
            model_settings=ModelSettings(
                temperature=0,
            ),
        )

    def _build_json_object_repair_agent(self, *, schema_name: str) -> Agent[Any]:
        return Agent(
            name=f"{schema_name}JsonRepair",
            instructions=(
                f"你是一个 {schema_name} JSON 修复器。你的唯一任务是把上一个模型输出修正为合法 JSON object。"
                "你必须只输出一个 JSON object，不得输出解释、不得输出 Markdown、不得只输出分析。"
                "如果原始输出包含 <think>、自然语言分析、代码块或半截 JSON，请提取真实意图并修复为单个 JSON object。"
            ),
            model=self._model,
            output_type=None,
            model_settings=ModelSettings(
                temperature=0,
            ),
        )

    def _build_repair_request(
        self,
        original_request: LLMRequest,
        raw_text: str,
        parse_error: str,
    ) -> LLMRequest:
        return LLMRequest(
            model=self.model_name,
            system_prompt="请将给定内容修正为合法 BrainStepResult JSON。",
            user_prompt=(
                "下面是上一轮模型的原始输出。请保留其动作意图，修复为一个合法的 "
                "BrainStepResult JSON。"
            ),
            context_blocks=[
                {
                    "title": "原始请求摘要",
                    "content": json.dumps(
                        {
                            "model": original_request.model,
                            "user_prompt": original_request.user_prompt,
                            "metadata": original_request.metadata,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                {
                    "title": "解析失败原因",
                    "content": parse_error,
                },
                {
                    "title": "待修复原始输出",
                    "content": raw_text,
                },
            ],
            metadata={"runtime_backend": "agents_sdk", "repair_mode": True},
        )

    def _build_json_object_repair_request(
        self,
        *,
        original_request: LLMRequest,
        raw_text: str,
        parse_error: str,
        schema_name: str,
    ) -> LLMRequest:
        return LLMRequest(
            model=self.model_name,
            system_prompt=f"请将给定内容修正为合法 {schema_name} JSON object。",
            user_prompt=(
                f"下面是上一轮模型的原始输出。请保留其意图，修复为一个合法的 {schema_name} JSON object。"
            ),
            context_blocks=[
                {
                    "title": "原始请求摘要",
                    "content": json.dumps(
                        {
                            "model": original_request.model,
                            "user_prompt": original_request.user_prompt,
                            "metadata": original_request.metadata,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                {
                    "title": "解析失败原因",
                    "content": parse_error,
                },
                {
                    "title": "待修复原始输出",
                    "content": raw_text,
                },
            ],
            metadata={"runtime_backend": "agents_sdk", "repair_mode": True, "schema_name": schema_name},
        )


class UnconfiguredAgentsSdkBrainRunner:
    def __init__(self, *, reason: str, model: str = "editorial-brain") -> None:
        self.reason = str(reason or "").strip() or "Agents SDK runtime is not configured."
        self.model_name = str(model or "editorial-brain").strip()
        self.runtime_workflow = "litellm"
        self.output_mode = "text"
        self.configured_output_mode = "auto"

    def run(
        self,
        compiled_context: CompiledBrainContext,
        *,
        session_id: str | None = None,
        working_root: str | Path | None = None,
        app_home: str | Path | None = None,
    ) -> BrainRunResult:
        request = LLMRequest(
            model=self.model_name,
            system_prompt=compiled_context.system_prompt,
            user_prompt=compiled_context.user_prompt,
            context_blocks=[compiled_context.action_playbook_block.to_dict()]
            + [block.to_dict() for block in compiled_context.attached_context_blocks],
            metadata={
                "token_budget_report": compiled_context.token_budget_report.to_dict(),
                "runtime_backend": "agents_sdk",
                "runtime_workflow": self.runtime_workflow,
                "session_id": session_id or "",
            },
        )
        response = LLMResponse(
            content="",
            model=self.model_name,
            raw_payload={
                "sdk": "openai-agents",
                "runtime_backend": "agents_sdk",
                "runtime_workflow": self.runtime_workflow,
                "session_id": session_id or "",
                "error": self.reason,
            },
        )
        raise BrainRunError(
            message=self.reason,
            request=request,
            response=response,
            raw_output="",
        )

    def list_available_tools(self) -> list[dict[str, Any]]:
        return list_material_tool_specs()
