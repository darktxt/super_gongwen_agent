from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from agents import (
    Agent,
    AgentOutputSchema,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    SQLiteSession,
    set_default_openai_api,
    set_default_openai_client,
)
from agents.items import ItemHelpers, MessageOutputItem, ToolCallOutputItem
from openai import AsyncOpenAI

from editorial_brain.context_compiler import CompiledBrainContext
from editorial_brain.contracts_core import BrainStepResult
from editorial_brain.output_parser import OutputParseError, OutputParser
from editorial_brain.runtime_contracts import BrainRunError, BrainRunResult, LLMRequest, LLMResponse

from .models import AgentBrainStepOutput


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
        self.app_home = Path(app_home).expanduser().resolve()
        self._session_db_path = self.app_home / "agents_runtime" / "sessions.sqlite3"
        self._session_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_storage_available = True
        self._session_storage_error = ""
        self._fallback_parser = OutputParser()
        self._max_repair_attempts = 1
        self._client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url or None,
            timeout=self.timeout,
        )
        set_default_openai_api("chat_completions")
        set_default_openai_client(self._client, use_for_tracing=self.enable_tracing)
        self._model = OpenAIChatCompletionsModel(
            model=self.model_name,
            openai_client=self._client,
        )

    @classmethod
    def from_config(cls, config: Any) -> "AgentsSdkBrainRunner":
        return cls(
            api_key=str(getattr(config, "openai_api_key", "") or ""),
            model=str(getattr(config, "openai_model", "") or ""),
            app_home=getattr(config, "app_home"),
            base_url=str(getattr(config, "openai_base_url", "") or ""),
            timeout=float(getattr(config, "openai_timeout", 300.0) or 300.0),
            temperature=getattr(config, "openai_temperature", None),
            enable_tracing=bool(getattr(config, "openai_agents_enable_tracing", True)),
            output_mode=str(getattr(config, "openai_agents_output_mode", "auto") or "auto"),
        )

    def run(
        self,
        compiled_context: CompiledBrainContext,
        *,
        session_id: str | None = None,
    ) -> BrainRunResult:
        request = self._build_request(compiled_context)
        response = LLMResponse(content="", model=self.model_name, raw_payload={})
        try:
            result = self._run_agent(
                agent=self._build_agent(compiled_context.system_prompt),
                request=request,
                session_id=session_id,
            )
            payload = self._coerce_run_result_payload(
                result,
                request=request,
                session_id=session_id,
            )
            step = BrainStepResult.from_dict(payload)
            response = LLMResponse(
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                model=self.model_name,
                raw_payload={
                    "sdk": "openai-agents",
                    "runtime_backend": "agents_sdk",
                    "output_mode": self.output_mode,
                    "last_response_id": result.last_response_id,
                    "raw_response_count": len(list(result.raw_responses or [])),
                    "final_output": payload,
                    "session_id": session_id or "",
                    "session_storage_available": self._session_storage_available,
                    "session_storage_error": self._session_storage_error,
                },
            )
            return BrainRunResult(request=request, response=response, step=step)
        except Exception as exc:
            fallback_step = self._try_recover_step_from_exception(
                exc,
                request=request,
                session_id=session_id,
            )
            if fallback_step is not None:
                payload = fallback_step.to_dict()
                response = LLMResponse(
                    content=json.dumps(payload, ensure_ascii=False, indent=2),
                    model=self.model_name,
                    raw_payload={
                        "sdk": "openai-agents",
                        "runtime_backend": "agents_sdk",
                        "output_mode": self.output_mode,
                        "fallback_parser_used": True,
                        "session_id": session_id or "",
                        "error_preview": str(exc)[:1200],
                        "session_storage_available": self._session_storage_available,
                        "session_storage_error": self._session_storage_error,
                    },
                )
                return BrainRunResult(request=request, response=response, step=fallback_step)
            if not response.content:
                response = LLMResponse(
                    content="",
                    model=self.model_name,
                    raw_payload={
                        "sdk": "openai-agents",
                        "runtime_backend": "agents_sdk",
                        "output_mode": self.output_mode,
                        "session_id": session_id or "",
                        "error": str(exc),
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
            context_blocks=[
                compiled_context.action_playbook_block.to_dict(),
                compiled_context.skill_listing_block.to_dict(),
            ]
            + [block.to_dict() for block in compiled_context.active_skill_blocks]
            + [block.to_dict() for block in compiled_context.attached_context_blocks],
            metadata={
                "token_budget_report": compiled_context.token_budget_report.to_dict(),
                "runtime_backend": "agents_sdk",
            },
        )

    def _build_agent(self, instructions: str) -> Agent[Any]:
        output_type: Any | None = None
        if self.output_mode == "structured":
            output_type = AgentOutputSchema(AgentBrainStepOutput, strict_json_schema=False)
        return Agent(
            name="EditorialBrainCoordinator",
            instructions=self._build_runtime_instructions(instructions),
            model=self._model,
            output_type=output_type,
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
            "component": "editorial_brain",
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
    ) -> Any:
        return Runner.run_sync(
            agent,
            self._render_user_content(request),
            max_turns=1,
            session=self._build_session(session_id),
            run_config=self._build_run_config(session_id),
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
        if self.output_mode == "structured":
            final_output = result.final_output_as(
                AgentBrainStepOutput,
                raise_if_incorrect_type=True,
            )
            return final_output.to_brain_step_dict()

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

    def _resolve_output_mode(self, configured_output_mode: str) -> str:
        normalized = str(configured_output_mode or "auto").strip().lower()
        if normalized == "auto":
            # 默认优先结构化输出；若供应商行为不稳定，再通过异常恢复与修复链路兜底。
            return "structured"
        if normalized in {"structured", "text"}:
            return normalized
        return "structured"

    def _build_runtime_instructions(self, instructions: str) -> str:
        normalized = str(instructions or "").strip()
        if self.output_mode != "text":
            return normalized
        compatibility_suffix = (
            "\n\n兼容输出模式补充要求：\n"
            "1. 最终回答必须包含且只包含一个可解析的 JSON object。\n"
            "2. 不要输出自然语言解释、不要只输出分析结论。\n"
            "3. 即使模型会生成 <think> 或推理内容，你也必须在最后给出完整 JSON object。\n"
            "4. 如果当前最合适的是 ask_user、write_draft、revise_draft 或 finalize，"
            "也必须严格按 BrainStepResult JSON 输出。"
        )
        return normalized + compatibility_suffix

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


class UnconfiguredAgentsSdkBrainRunner:
    def __init__(self, *, reason: str, model: str = "editorial-brain") -> None:
        self.reason = str(reason or "").strip() or "Agents SDK runtime is not configured."
        self.model_name = str(model or "editorial-brain").strip()
        self.output_mode = "structured"
        self.configured_output_mode = "auto"

    def run(
        self,
        compiled_context: CompiledBrainContext,
        *,
        session_id: str | None = None,
    ) -> BrainRunResult:
        request = LLMRequest(
            model=self.model_name,
            system_prompt=compiled_context.system_prompt,
            user_prompt=compiled_context.user_prompt,
            context_blocks=[
                compiled_context.action_playbook_block.to_dict(),
                compiled_context.skill_listing_block.to_dict(),
            ]
            + [block.to_dict() for block in compiled_context.active_skill_blocks]
            + [block.to_dict() for block in compiled_context.attached_context_blocks],
            metadata={
                "token_budget_report": compiled_context.token_budget_report.to_dict(),
                "runtime_backend": "agents_sdk",
                "session_id": session_id or "",
            },
        )
        response = LLMResponse(
            content="",
            model=self.model_name,
            raw_payload={
                "sdk": "openai-agents",
                "runtime_backend": "agents_sdk",
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
