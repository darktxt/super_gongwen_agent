from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from typing import Any, Callable

from agents_runtime.tools import list_material_tool_specs
from config import AppConfig, load_config
from agents_runtime.context import ContextCompiler
from agents_runtime.protocol import (
    BrainRunError,
    BrainStepResult,
    CONTROL_ONLY_ACTIONS,
    OutputParseError,
)
from orchestration import WorkflowCoordinator
from observability.events import ObservabilityEventWriter
from observability.logger import build_app_logger, log_structured
from observability.metrics import MetricsCollector
from runtime_factory import build_brain_runner
from session_storage.paths import build_session_paths
from session_storage.history import initialize_session_storage, save_final_output
from utils.clock import utc_now_iso
from utils.session_ids import generate_session_id
from workspace.patcher import WorkspacePatcher
from workspace.models import DebugRoundSummary, OutlineSection, WorkspaceState
from workspace.store import WorkspaceStore


@dataclass(slots=True)
class AppBootstrapResult:
    session_id: str | None
    app_home: str
    sessions_root: str
    message: str


MAX_RETRIEVAL_CALL_SUMMARIES = 8


@dataclass(slots=True)
class TurnRunResult:
    session_id: str
    status: str
    rounds_used: int
    final_text: str = ""
    final_output_path: str = ""
    llm_raw_output: str = ""
    question_pack: list[dict[str, Any]] = field(default_factory=list)
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    step: BrainStepResult | None = None
    workspace: WorkspaceState | None = None
    error_message: str = ""


class SuperGongwenApp:
    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        context_compiler: ContextCompiler | None = None,
        brain_runner: Any | None = None,
        workspace_patcher: WorkspacePatcher | None = None,
        logger: logging.Logger | None = None,
        metrics: MetricsCollector | None = None,
        event_writer: ObservabilityEventWriter | None = None,
        working_root: str | Path | None = None,
        progress_reporter: Callable[[str], None] | None = None,
        round_reporter: Callable[[TurnRunResult], None] | None = None,
    ) -> None:
        self.config = config or load_config()
        self.workspace_store = WorkspaceStore(app_home=self.config.app_home)
        self.context_compiler = context_compiler or ContextCompiler()
        self.brain_runner = brain_runner or build_brain_runner(config=self.config)
        self.workspace_patcher = workspace_patcher or WorkspacePatcher()
        self.workflow_coordinator = WorkflowCoordinator()
        self.logger = logger or build_app_logger()
        self.metrics = metrics or MetricsCollector()
        self.event_writer = event_writer or ObservabilityEventWriter(app_home=self.config.app_home)
        self.working_root = Path(working_root).resolve() if working_root is not None else Path.cwd()
        self.progress_reporter = progress_reporter
        self.round_reporter = round_reporter

    def bootstrap(self, session_id: str | None = None) -> AppBootstrapResult:
        self.config.sessions_root.mkdir(parents=True, exist_ok=True)
        resolved_session_id = session_id or generate_session_id()

        initialize_session_storage(session_id=resolved_session_id, app_home=self.config.app_home)
        self.workspace_store.load_or_create(session_id=resolved_session_id)

        return AppBootstrapResult(
            session_id=resolved_session_id,
            app_home=str(self.config.app_home),
            sessions_root=str(self.config.sessions_root),
            message="super-gongwen-agent bootstrap ready",
        )

    def app_home_path(self) -> Path:
        return self.config.app_home

    def _list_available_tools(self) -> list[dict[str, Any]]:
        if hasattr(self.brain_runner, "list_available_tools"):
            value = getattr(self.brain_runner, "list_available_tools")
            if callable(value):
                tools = value()
                if isinstance(tools, list):
                    return [dict(item) for item in tools if isinstance(item, dict)]
        return list_material_tool_specs()

    def run_turn(
        self,
        session_id: str,
        user_input: str,
    ) -> TurnRunResult:
        workspace = self.workspace_store.load_or_create(session_id=session_id)
        self.workspace_patcher.ingest_user_message(workspace, user_input)
        self.workflow_coordinator.initialize_for_user_message(workspace)
        workspace.debug_state.last_user_input = user_input.strip()
        workspace.debug_state.last_event = "turn_started"
        workspace.debug_state.last_error = ""
        workspace.debug_state.last_workspace_summary = self._summarize_workspace(workspace)
        self.workspace_store.save(workspace)
        self._record_runtime_event(
            session_id,
            "turn_started",
            level=logging.INFO,
            has_user_input=bool(user_input.strip()),
            workspace_summary=self._summarize_workspace(workspace),
        )
        self._report_user_progress("开始处理本轮写作需求，请稍候。")

        rounds_used = 0
        last_step: BrainStepResult | None = None
        last_llm_raw_output = ""
        last_tool_requests: list[dict[str, Any]] = []
        last_tool_results: list[dict[str, Any]] = []

        try:
            while True:
                rounds_used += 1
                self._report_user_progress(f"第 {rounds_used} 轮：正在分析需求与材料。")
                round_debug_files: dict[str, str] = {}
                workspace_before_summary = self._summarize_workspace(workspace)
                retrieval_state_before = self._capture_retrieval_state(workspace)
                self._write_round_debug_file(
                    session_id,
                    rounds_used,
                    "workspace_before",
                    workspace.to_dict(),
                    round_debug_files=round_debug_files,
                )
                snapshot = self.workspace_store.snapshot(
                    workspace,
                    available_tools=self._list_available_tools(),
                )
                compiled = self.context_compiler.build(snapshot)
                compiled_summary = self._summarize_compiled_context(compiled)
                context_debug_file = self._write_round_debug_file(
                    session_id,
                    rounds_used,
                    "context",
                    compiled.to_dict(),
                    round_debug_files=round_debug_files,
                )
                self._update_round_debug_state(
                    workspace,
                    round_no=rounds_used,
                    event_name="round_context_compiled",
                    result_status="running",
                    compiled_summary=compiled_summary,
                    debug_files=round_debug_files,
                    workspace_summary=workspace_before_summary,
                )
                self._record_runtime_event(
                    session_id,
                    "round_context_compiled",
                    level=logging.INFO,
                    round_no=rounds_used,
                    compiled_summary=compiled_summary,
                    debug_file=context_debug_file,
                    workspace_summary=workspace_before_summary,
                )
                self._record_runtime_event(
                    session_id,
                    "llm_call_started",
                    level=logging.INFO,
                    round_no=rounds_used,
                )
                try:
                    brain_result = self.brain_runner.run(
                        compiled,
                        session_id=session_id,
                        working_root=self.working_root,
                        app_home=self.config.app_home,
                    )
                except BrainRunError as exc:
                    last_llm_raw_output = exc.raw_output
                    self.metrics.increment("parse_error_count")
                    request_summary = self._summarize_llm_request(exc.request)
                    request_debug_file = self._write_round_debug_file(
                        session_id,
                        rounds_used,
                        "request",
                        exc.request.to_dict(),
                        round_debug_files=round_debug_files,
                    )
                    response_summary = self._summarize_llm_response(exc.response)
                    response_debug_file = self._write_round_debug_file(
                        session_id,
                        rounds_used,
                        "response",
                        exc.response.to_dict(),
                        round_debug_files=round_debug_files,
                    )
                    self._update_round_debug_state(
                        workspace,
                        round_no=rounds_used,
                        event_name="llm_parse_failed",
                        result_status="failed",
                        compiled_summary=compiled_summary,
                        request_summary=request_summary,
                        response_summary=response_summary,
                        debug_files=round_debug_files,
                        workspace_summary=workspace_before_summary,
                        error_message=str(exc),
                    )
                    self._record_runtime_event(
                        session_id,
                        "llm_request_built",
                        level=logging.INFO,
                        round_no=rounds_used,
                        request_summary=request_summary,
                        debug_file=request_debug_file,
                    )
                    self._record_runtime_event(
                        session_id,
                        "llm_raw_output_received",
                        level=logging.INFO,
                        round_no=rounds_used,
                        response_summary=response_summary,
                        debug_file=response_debug_file,
                    )
                    self._record_runtime_event(
                        session_id,
                        "llm_parse_failed",
                        level=logging.ERROR,
                        round_no=rounds_used,
                        error=str(exc),
                        llm_raw_output_preview=self._preview_text(exc.raw_output, 600),
                        request_debug_file=request_debug_file,
                        response_debug_file=response_debug_file,
                    )
                    raise
                except OutputParseError as exc:
                    self.metrics.increment("parse_error_count")
                    self._update_round_debug_state(
                        workspace,
                        round_no=rounds_used,
                        event_name="llm_parse_failed",
                        result_status="failed",
                        compiled_summary=compiled_summary,
                        debug_files=round_debug_files,
                        workspace_summary=workspace_before_summary,
                        error_message=str(exc),
                    )
                    self._record_runtime_event(
                        session_id,
                        "llm_parse_failed",
                        level=logging.ERROR,
                        round_no=rounds_used,
                        error=str(exc),
                    )
                    raise

                last_llm_raw_output = brain_result.response.content
                self.metrics.increment("llm_call_count")
                request_summary = self._summarize_llm_request(brain_result.request)
                request_debug_file = self._write_round_debug_file(
                    session_id,
                    rounds_used,
                    "request",
                    brain_result.request.to_dict(),
                    round_debug_files=round_debug_files,
                )
                response_summary = self._summarize_llm_response(brain_result.response)
                self._record_runtime_profile(workspace, brain_result.response)
                response_debug_file = self._write_round_debug_file(
                    session_id,
                    rounds_used,
                    "response",
                    brain_result.response.to_dict(),
                    round_debug_files=round_debug_files,
                )
                step = brain_result.step
                last_step = step
                tool_requests = [
                    dict(request or {})
                    for request in list(getattr(brain_result, "tool_requests", []) or [])
                    if isinstance(request, dict)
                ]
                tool_results = [
                    dict(result or {})
                    for result in list(getattr(brain_result, "tool_results", []) or [])
                    if isinstance(result, dict)
                ]
                last_tool_requests = list(tool_requests)
                last_tool_results = list(tool_results)
                if tool_results:
                    self.workspace_patcher.apply_tool_results(workspace, tool_results)
                    self._record_read_materials_round(
                        workspace,
                        self._build_read_materials_round_summary(
                            tool_requests,
                            tool_results,
                            retrieval_state_before=retrieval_state_before,
                            workspace=workspace,
                        ),
                    )
                    self.metrics.increment("tool_call_count", len(tool_results))
                    self._record_runtime_event(
                        session_id,
                        "tool_batch_executed",
                        level=logging.INFO,
                        round_no=rounds_used,
                        tool_names=[
                            self._tool_request_name(request)
                            for request in tool_requests
                            if self._tool_request_name(request)
                        ],
                        tool_request_count=len(tool_requests),
                        tool_result_count=len(tool_results),
                    )
                step_summary = self._summarize_step(
                    step,
                    workspace=workspace,
                    tool_requests=tool_requests,
                )
                self._report_round_step_progress(rounds_used, step_summary)
                step_debug_file = self._write_round_debug_file(
                    session_id,
                    rounds_used,
                    "step",
                    step.to_dict(),
                    round_debug_files=round_debug_files,
                )
                self._update_round_debug_state(
                    workspace,
                    round_no=rounds_used,
                    event_name="llm_step_parsed",
                    action_taken=step.action_taken,
                    result_status="running",
                    compiled_summary=compiled_summary,
                    request_summary=request_summary,
                    response_summary=response_summary,
                    step_summary=step_summary,
                    debug_files=round_debug_files,
                    workspace_summary=workspace_before_summary,
                )
                self._record_runtime_event(
                    session_id,
                    "llm_request_built",
                    level=logging.INFO,
                    round_no=rounds_used,
                    request_summary=request_summary,
                    debug_file=request_debug_file,
                )
                self._record_runtime_event(
                    session_id,
                    "llm_raw_output_received",
                    level=logging.INFO,
                    round_no=rounds_used,
                    response_summary=response_summary,
                    debug_file=response_debug_file,
                )
                self._record_runtime_event(
                    session_id,
                    "llm_step_parsed",
                    level=logging.INFO,
                    round_no=rounds_used,
                    step_summary=step_summary,
                    debug_file=step_debug_file,
                )
                self._record_runtime_event(
                    session_id,
                    "llm_call_completed",
                    level=logging.INFO,
                    round_no=rounds_used,
                    action_taken=step.action_taken,
                    response_summary=response_summary,
                )

                self._record_step_meta(workspace, step)
                self._update_workspace_self_review(workspace, step)
                if step.action_taken not in CONTROL_ONLY_ACTIONS:
                    self._record_quality_review_snapshot(
                        workspace,
                        step,
                        source="brain_step",
                        step_summary=step_summary,
                    )

                if step.ask_user:
                    question_pack = list(getattr(step.action_payload, "question_pack", []) or [])
                    workspace.pending_questions = list(question_pack)
                    self.workflow_coordinator.transition_after_step(workspace, step)
                    self._checkpoint_workspace(
                        session_id=session_id,
                        round_no=rounds_used,
                        label="workspace_after_ask_user",
                        reason="after_ask_user",
                        workspace=workspace,
                        event_name="turn_needs_user_input",
                        action_taken=step.action_taken,
                        result_status="needs_user_input",
                        compiled_summary=compiled_summary,
                        request_summary=request_summary,
                        response_summary=response_summary,
                        step_summary=step_summary,
                        debug_files=round_debug_files,
                    )
                    self.metrics.increment("needs_user_input_count")
                    self._record_runtime_event(
                        session_id,
                        "turn_needs_user_input",
                        level=logging.INFO,
                        round_no=rounds_used,
                        question_count=len(question_pack),
                    )
                    return TurnRunResult(
                        session_id=session_id,
                        status="needs_user_input",
                        rounds_used=rounds_used,
                        llm_raw_output=last_llm_raw_output,
                        question_pack=question_pack,
                        tool_requests=tool_requests,
                        tool_results=tool_results,
                        step=step,
                        workspace=workspace,
                    )

                before_word_count = int(getattr(workspace.draft_artifact, "word_count", 0) or 0)
                self.workspace_patcher.apply(workspace, step.workspace_patch)
                self._apply_action_payload_fallbacks(workspace, step)
                revision_history_entries = self._build_revision_history_entries(
                    step,
                    before_word_count=before_word_count,
                    after_word_count=int(getattr(workspace.draft_artifact, "word_count", 0) or 0),
                )
                self.workspace_patcher.append_revision_history_entries(
                    workspace,
                    revision_history_entries,
                )

                self.workflow_coordinator.transition_after_step(workspace, step)

                if step.export_requested:
                    final_text = self._resolve_final_text_for_export(workspace, step)
                    if not final_text:
                        raise ValueError("finalize 动作缺少可导出的终稿文本。")
                    final_output_path = str(
                        save_final_output(
                            session_id,
                            final_text,
                            app_home=self.config.app_home,
                        )
                    )
                    self._checkpoint_workspace(
                        session_id=session_id,
                        round_no=rounds_used,
                        label="workspace_after_finalize",
                        reason="after_finalize",
                        workspace=workspace,
                        event_name="turn_completed",
                        action_taken=step.action_taken,
                        result_status="completed",
                        compiled_summary=compiled_summary,
                        request_summary=request_summary,
                        response_summary=response_summary,
                        step_summary=step_summary,
                        debug_files=round_debug_files,
                    )
                    self.metrics.increment("completed_turn_count")
                    self._record_runtime_event(
                        session_id,
                        "turn_completed",
                        level=logging.INFO,
                        round_no=rounds_used,
                        final_output_path=final_output_path,
                    )
                    return TurnRunResult(
                        session_id=session_id,
                        status="completed",
                        rounds_used=rounds_used,
                        final_text=final_text,
                        final_output_path=final_output_path,
                        llm_raw_output=last_llm_raw_output,
                        tool_requests=tool_requests,
                        tool_results=tool_results,
                        step=step,
                        workspace=workspace,
                    )

                self._checkpoint_workspace(
                    session_id=session_id,
                    round_no=rounds_used,
                    label="workspace_after_action",
                    reason="after_action",
                    workspace=workspace,
                    event_name="workspace_checkpoint_saved",
                    action_taken=step.action_taken,
                    result_status="continued",
                    compiled_summary=compiled_summary,
                    request_summary=request_summary,
                    response_summary=response_summary,
                    step_summary=step_summary,
                    debug_files=round_debug_files,
                    turn_result=TurnRunResult(
                        session_id=session_id,
                        status="continued",
                        rounds_used=rounds_used,
                        llm_raw_output=last_llm_raw_output,
                        tool_requests=tool_requests,
                        tool_results=tool_results,
                        step=step,
                        workspace=workspace,
                    ),
                )
                continue
        except Exception as exc:
            workspace_summary = self._summarize_workspace(workspace)
            error_round_no = rounds_used or 1
            self._update_round_debug_state(
                workspace,
                round_no=error_round_no,
                event_name="turn_failed",
                action_taken=getattr(last_step, "action_taken", ""),
                result_status="failed",
                debug_files={},
                workspace_summary=workspace_summary,
                error_message=str(exc),
            )
            self.workspace_store.save(workspace)
            self.metrics.increment("failed_turn_count")
            self._record_runtime_event(
                session_id,
                "turn_failed",
                level=logging.ERROR,
                rounds_used=rounds_used,
                error=str(exc),
                last_action=getattr(last_step, "action_taken", ""),
                llm_raw_output_preview=self._preview_text(last_llm_raw_output, 600),
                workspace_summary=workspace_summary,
            )
            return TurnRunResult(
                session_id=session_id,
                status="failed",
                rounds_used=rounds_used,
                llm_raw_output=last_llm_raw_output,
                tool_requests=last_tool_requests,
                tool_results=last_tool_results,
                step=last_step,
                workspace=workspace,
                error_message=str(exc),
            )

    def _write_round_debug_file(
        self,
        session_id: str,
        round_no: int,
        label: str,
        payload: Any,
        *,
        round_debug_files: dict[str, str],
    ) -> str:
        safe_label = str(label).strip().replace(" ", "_")
        filename = f"round_{round_no:04d}_{safe_label}.json"
        path = self.event_writer.write_debug_json(
            session_id=session_id,
            filename=filename,
            payload=payload,
        )
        if path is None:
            return ""
        relative_path = self._session_relative_path(session_id, path)
        round_debug_files[safe_label] = relative_path
        return relative_path

    def _session_relative_path(self, session_id: str, path: Path) -> str:
        session_paths = build_session_paths(session_id=session_id, app_home=self.config.app_home)
        try:
            return path.resolve().relative_to(session_paths.session_root.resolve()).as_posix()
        except ValueError:
            return str(path)

    def _preview_text(self, text: str, limit: int = 300) -> str:
        normalized = str(text or "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "...[truncated]"

    def _count_text_units(self, text: str) -> int:
        return len(str(text or "").replace(" ", "").replace("\n", ""))

    def _summarize_workspace(self, workspace: WorkspaceState) -> dict[str, Any]:
        return {
            "workflow_phase": str(workspace.workflow_state.current_phase or ""),
            "workflow_next_phase_hint": str(workspace.workflow_state.next_phase_hint or ""),
            "workflow_phase_history": list(workspace.workflow_state.phase_history[-6:]),
            "quality_backlog_count": len(workspace.quality_backlog.items),
            "selected_material_count": len(workspace.material_catalog.selected_files),
            "material_item_count": len(workspace.material_catalog.items),
            "retrieved_excerpt_count": len(workspace.retrieved_materials.excerpts),
            "retrieved_source_path_count": len(workspace.retrieved_materials.recent_source_paths),
            "evidence_fact_count": len(workspace.evidence_board.facts),
            "pending_question_count": len(workspace.pending_questions),
            "revision_history_count": len(workspace.revision_history),
            "outline_status": workspace.outline_artifact.status,
            "outline_section_count": len(workspace.outline_artifact.sections),
            "outline_has_text": bool(str(workspace.outline_artifact.outline_text or "").strip()),
            "draft_status": workspace.draft_artifact.status,
            "draft_word_count": workspace.draft_artifact.word_count,
            "draft_section_count": len(workspace.draft_artifact.section_map),
            "draft_assembly_mode": str(workspace.draft_artifact.assembly_mode or ""),
            "last_draft_transition": str(
                workspace.session_meta.get("last_draft_transition", "") or ""
            ),
            "draft_has_text": bool(str(workspace.draft_artifact.full_text or "").strip()),
            "dominant_issue": workspace.self_review.dominant_issue,
            "open_gaps": list(workspace.self_review.open_gaps[:5]),
        }

    def _summarize_compiled_context(self, compiled: Any) -> dict[str, Any]:
        context_blocks = [compiled.action_playbook_block, *compiled.attached_context_blocks]
        return {
            "block_count": len(context_blocks),
            "block_titles": [block.title for block in context_blocks],
            "truncated_block_titles": list(compiled.token_budget_report.truncated_block_titles),
            "system_prompt_chars": len(compiled.system_prompt),
            "user_prompt_chars": len(compiled.user_prompt),
            "char_budget": compiled.token_budget_report.char_budget,
            "char_used": compiled.token_budget_report.char_used,
        }

    def _summarize_llm_request(self, request: Any) -> dict[str, Any]:
        context_blocks = list(getattr(request, "context_blocks", []) or [])
        context_titles = [
            str(block.get("title", "") or "")
            for block in context_blocks
            if isinstance(block, dict)
        ]
        context_chars = 0
        for block in context_blocks:
            if not isinstance(block, dict):
                continue
            context_chars += len(str(block.get("title", "") or ""))
            context_chars += len(str(block.get("content", "") or ""))
        return {
            "model": str(getattr(request, "model", "") or ""),
            "system_prompt_chars": len(str(getattr(request, "system_prompt", "") or "")),
            "user_prompt_chars": len(str(getattr(request, "user_prompt", "") or "")),
            "context_block_count": len(context_blocks),
            "context_block_titles": context_titles,
            "context_chars": context_chars,
            "metadata_keys": sorted(
                str(key) for key in dict(getattr(request, "metadata", {}) or {}).keys()
            ),
        }

    def _summarize_llm_response(self, response: Any) -> dict[str, Any]:
        raw_payload = dict(getattr(response, "raw_payload", {}) or {})
        content = str(getattr(response, "content", "") or "")
        decision_trace = [
            dict(item)
            for item in list(raw_payload.get("decision_trace", []) or [])
            if isinstance(item, dict)
        ]
        orchestration_summary = self._normalize_orchestration_summary(
            raw_payload.get("orchestration_summary")
        )
        return {
            "model": str(getattr(response, "model", "") or ""),
            "content_chars": len(content),
            "content_preview": self._preview_text(content, 500),
            "raw_payload_keys": sorted(str(key) for key in raw_payload.keys()),
            "specialist_count": int(raw_payload.get("specialist_count", 0) or 0),
            "decision_trace_count": len(decision_trace),
            "decision_trace_summary": self._summarize_decision_trace(decision_trace),
            "orchestration_summary": orchestration_summary,
            "completion_mode": str(raw_payload.get("completion_mode", "") or "").strip(),
        }

    def _normalize_orchestration_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        summary = dict(value)
        summary["used_specialist"] = bool(summary.get("used_specialist", False))
        for key in ("initial_proposal", "final_decision"):
            nested = summary.get(key)
            summary[key] = dict(nested) if isinstance(nested, dict) else {}
        feedback_items: list[dict[str, Any]] = []
        for item in list(summary.get("specialist_feedback", []) or []):
            if isinstance(item, dict):
                feedback_items.append(dict(item))
        summary["specialist_feedback"] = feedback_items[:4]
        summary["assumptions"] = [
            str(item).strip()
            for item in list(summary.get("assumptions", []) or [])
            if str(item).strip()
        ][:5]
        summary["major_risks"] = [
            str(item).strip()
            for item in list(summary.get("major_risks", []) or [])
            if str(item).strip()
        ][:5]
        return summary

    def _summarize_decision_trace(
        self,
        decision_trace: list[dict[str, Any]] | None,
    ) -> list[str]:
        if not isinstance(decision_trace, list):
            return []
        summary: list[str] = []
        for item in list(decision_trace or []):
            actor = str(item.get("actor", "") or "").strip() or "unknown"
            kind = str(item.get("kind", "") or "").strip()
            if kind == "coordinator":
                stage = str(item.get("stage", "") or "").strip() or "decision"
                action_taken = str(item.get("action_taken", "") or "").strip() or "unknown"
                completion_mode = str(item.get("completion_mode", "") or "").strip()
                line = f"{actor}:{stage}:{action_taken}"
                if completion_mode:
                    line += f":{completion_mode}"
                summary.append(line)
                continue
            if kind == "specialist":
                status = str(item.get("status", "") or "").strip() or "applied"
                verdict = str(item.get("feedback_verdict", "") or "").strip()
                recommended_action = str(item.get("recommended_action", "") or "").strip()
                line = f"{actor}:{status}"
                if verdict:
                    line += f":{verdict}"
                if recommended_action:
                    line += f"->{recommended_action}"
                summary.append(line)
                continue
            summary.append(actor)
        return summary[:8]

    def _summarize_step(
        self,
        step: BrainStepResult,
        *,
        workspace: WorkspaceState | None = None,
        tool_requests: list[Any] | None = None,
    ) -> dict[str, Any]:
        effective_tool_requests = list(tool_requests or [])
        summary = {
            "action_taken": step.action_taken,
            "ask_user": step.ask_user,
            "done": step.export_requested,
            "business_completion_declared": step.business_completion_declared,
            "completion_mode": step.completion_mode,
            "decision_rationale": str(step.decision_rationale or "").strip(),
            "assumptions": [str(item).strip() for item in list(step.assumptions or []) if str(item).strip()][:5],
            "major_risks": [str(item).strip() for item in list(step.major_risks or []) if str(item).strip()][:5],
            "assumption_count": len(step.assumptions),
            "major_risk_count": len(step.major_risks),
            "tool_request_count": len(effective_tool_requests),
            "tool_names": [
                self._tool_request_name(request)
                for request in effective_tool_requests
                if self._tool_request_name(request)
            ],
            "outline_update_keys": sorted(step.workspace_patch.outline_update.keys()),
            "revision_history_update_count": len(step.workspace_patch.revision_history_updates),
            "dominant_issue": step.self_review.dominant_issue,
            "open_gaps": list(step.self_review.open_gaps),
            "output_digest": self._build_output_digest(
                step,
                workspace=workspace,
                tool_requests=effective_tool_requests,
            ),
            "patch_digest": self._build_patch_digest(step),
        }
        if effective_tool_requests and workspace is not None:
            latest_call = self._latest_read_materials_call(workspace)
            if latest_call:
                summary["read_materials_summary"] = latest_call
        return summary

    def _build_output_digest(
        self,
        step: BrainStepResult,
        *,
        workspace: WorkspaceState | None = None,
        tool_requests: list[Any] | None = None,
    ) -> str:
        action = str(step.action_taken or "").strip()
        if action == "build_outline":
            outline_sections = list(getattr(step.action_payload, "outline_sections", []) or [])
            headings: list[str] = []
            for section in outline_sections[:5]:
                if isinstance(section, dict):
                    heading = str(section.get("heading", "") or "").strip()
                    if heading:
                        headings.append(heading)
            outline_text = str(getattr(step.action_payload, "outline_text", "") or "").strip()
            if headings:
                return "已形成提纲骨架，共 " + str(len(outline_sections)) + " 节；主要章节为：" + "；".join(headings) + "。"
            if outline_text:
                return "已形成提纲文本，长度约 " + str(self._count_text_units(outline_text)) + " 字。"
            return "已执行提纲构建。"

        if action == "write_draft":
            draft_text = str(getattr(step.action_payload, "draft_text", "") or "").strip()
            return "已形成整稿基稿，长度约 " + str(self._count_text_units(draft_text)) + " 字。"

        if action == "write_section":
            section_id = str(getattr(step.action_payload, "section_id", "") or "").strip()
            section_text = str(getattr(step.action_payload, "section_text", "") or "").strip()
            return (
                "已补写或替换章节 "
                + (section_id or "未命名章节")
                + "，长度约 "
                + str(self._count_text_units(section_text))
                + " 字。"
            )

        if action == "revise_draft":
            revised_text = str(getattr(step.action_payload, "revised_text", "") or "").strip()
            return "已完成一轮整稿修订，修订后全文长度约 " + str(self._count_text_units(revised_text)) + " 字。"

        if action == "polish_language":
            polished_text = str(getattr(step.action_payload, "polished_text", "") or "").strip()
            return "已完成一轮语言润色，全文长度约 " + str(self._count_text_units(polished_text)) + " 字。"

        if action == "ask_user":
            question_pack = list(getattr(step.action_payload, "question_pack", []) or [])
            return "已向用户发起补充信息请求，共 " + str(len(question_pack)) + " 个问题。"

        if action == "finalize":
            final_text = self._final_text_from_step(step)
            completion_mode = str(step.completion_mode or "").strip()
            prefix = "已形成可交付终稿"
            if completion_mode:
                prefix = "已形成" + completion_mode
            return prefix + "，长度约 " + str(self._count_text_units(final_text)) + " 字。"

        return ""

    def _capture_retrieval_state(self, workspace: WorkspaceState) -> dict[str, Any]:
        return {
            "selected_files": {
                str(path).strip()
                for path in list(workspace.material_catalog.selected_files or [])
                if str(path).strip()
            },
            "recent_source_paths": {
                str(path).strip()
                for path in list(workspace.retrieved_materials.recent_source_paths or [])
                if str(path).strip()
            },
            "excerpt_count": len(list(workspace.retrieved_materials.excerpts or [])),
            "evidence_counts": self._capture_evidence_counts(workspace),
        }

    def _capture_evidence_counts(self, workspace: WorkspaceState) -> dict[str, int]:
        return {
            "facts": len(list(workspace.evidence_board.facts or [])),
            "data_points": len(list(workspace.evidence_board.data_points or [])),
            "cases": len(list(workspace.evidence_board.cases or [])),
            "measure_handles": len(list(workspace.evidence_board.measure_handles or [])),
        }

    def _tool_request_name(self, request: Any) -> str:
        if isinstance(request, dict):
            return str(request.get("tool_name", "") or "").strip()
        return str(getattr(request, "tool_name", "") or "").strip()

    def _build_read_materials_round_summary(
        self,
        tool_requests: list[Any],
        tool_results: list[dict[str, Any]],
        *,
        retrieval_state_before: dict[str, Any],
        workspace: WorkspaceState,
    ) -> dict[str, Any]:
        request_breakdown: dict[str, int] = {}
        result_breakdown: dict[str, dict[str, int]] = {}

        for request in tool_requests:
            tool_name = self._tool_request_name(request)
            if tool_name:
                request_breakdown[tool_name] = request_breakdown.get(tool_name, 0) + 1

        for raw_result in tool_results:
            result = dict(raw_result or {})
            tool_name = str(result.get("tool_name", "") or "").strip()
            if not tool_name:
                continue
            payload = dict(result.get("payload", {}) or {})
            entry = result_breakdown.setdefault(
                tool_name,
                {"result_count": 0, "excerpt_count": 0, "selected_file_count": 0},
            )
            entry["result_count"] += self._tool_result_item_count(tool_name, payload)
            entry["excerpt_count"] += self._tool_result_excerpt_count(tool_name, payload)
            entry["selected_file_count"] += len(list(payload.get("selected_files", []) or []))

        after_selected_files = {
            str(path).strip()
            for path in list(workspace.material_catalog.selected_files or [])
            if str(path).strip()
        }
        after_source_paths = {
            str(path).strip()
            for path in list(workspace.retrieved_materials.recent_source_paths or [])
            if str(path).strip()
        }
        after_evidence = self._capture_evidence_counts(workspace)
        evidence_delta = {
            key: max(after_evidence.get(key, 0) - int(retrieval_state_before["evidence_counts"].get(key, 0) or 0), 0)
            for key in ("facts", "data_points", "cases", "measure_handles")
        }
        new_excerpt_count = max(
            len(list(workspace.retrieved_materials.excerpts or []))
            - int(retrieval_state_before.get("excerpt_count", 0) or 0),
            0,
        )
        read_result_count = int(result_breakdown.get("read", {}).get("result_count", 0) or 0)
        evidence_delta_total = sum(evidence_delta.values())
        readiness_after_call = "no_gain"
        if read_result_count > 0 and evidence_delta_total > 0:
            readiness_after_call = "enriched"
        elif read_result_count > 0:
            readiness_after_call = "grounded"
        elif any(entry.get("result_count", 0) > 0 for entry in result_breakdown.values()) or new_excerpt_count > 0:
            readiness_after_call = "lead_only"

        return {
            "request_breakdown": request_breakdown,
            "result_breakdown": result_breakdown,
            "new_source_paths": max(
                len(after_source_paths - set(retrieval_state_before.get("recent_source_paths", set()))),
                0,
            ),
            "selected_files_added": max(
                len(after_selected_files - set(retrieval_state_before.get("selected_files", set()))),
                0,
            ),
            "new_excerpt_count": new_excerpt_count,
            "evidence_delta": evidence_delta,
            "readiness_after_call": readiness_after_call,
        }

    def _record_read_materials_round(
        self,
        workspace: WorkspaceState,
        summary: dict[str, Any],
    ) -> None:
        if not summary:
            return
        calls = list(workspace.retrieved_materials.recent_calls or [])
        calls.append(summary)
        workspace.retrieved_materials.recent_calls = calls[-MAX_RETRIEVAL_CALL_SUMMARIES:]

    def _tool_result_item_count(self, tool_name: str, payload: dict[str, Any]) -> int:
        if tool_name in {"search", "list"}:
            return len(list(payload.get("items", []) or []))
        if tool_name == "grep":
            return len(list(payload.get("matches", []) or []))
        if tool_name == "read":
            return 1 if str(payload.get("text", "") or "").strip() else 0
        if payload:
            return 1
        return 0

    def _tool_result_excerpt_count(self, tool_name: str, payload: dict[str, Any]) -> int:
        if tool_name == "search":
            return sum(
                1
                for item in list(payload.get("items", []) or [])
                if isinstance(item, dict)
                and str(item.get("path", "") or "").strip()
                and str(item.get("preview", "") or "").strip()
            )
        if tool_name == "grep":
            return sum(
                1
                for item in list(payload.get("matches", []) or [])
                if isinstance(item, dict)
                and str(item.get("path", "") or "").strip()
                and str(item.get("line_text", "") or "").strip()
            )
        if tool_name == "read":
            return 1 if str(payload.get("path", "") or "").strip() and str(payload.get("text", "") or "").strip() else 0
        return 0

    def _latest_read_materials_call(self, workspace: WorkspaceState | None) -> dict[str, Any]:
        if workspace is None:
            return {}
        recent_calls = list(getattr(workspace.retrieved_materials, "recent_calls", []) or [])
        for item in reversed(recent_calls):
            if isinstance(item, dict):
                return item
        return {}

    def _format_read_materials_output_digest(self, latest_call: dict[str, Any]) -> str:
        request_breakdown = dict(latest_call.get("request_breakdown", {}) or {})
        request_parts = [
            f"{tool_name}×{int(count or 0)}"
            for tool_name, count in sorted(request_breakdown.items())
            if str(tool_name).strip() and int(count or 0) > 0
        ]
        if request_parts:
            leading = "已发起读材请求：" + "、".join(request_parts)
        else:
            leading = "已发起读材请求。"
        selected_files_added = int(latest_call.get("selected_files_added", 0) or 0)
        new_source_paths = int(latest_call.get("new_source_paths", 0) or 0)
        new_excerpt_count = int(latest_call.get("new_excerpt_count", 0) or 0)
        read_result_count = int(
            dict(latest_call.get("result_breakdown", {}) or {}).get("read", {}).get("result_count", 0) or 0
        )
        evidence_delta_payload = dict(latest_call.get("evidence_delta", {}) or {})
        evidence_delta_total = sum(max(int(value or 0), 0) for value in evidence_delta_payload.values())
        detail_parts = [
            "命中来源 " + str(new_source_paths) + " 个",
            "新增候选文件 " + str(selected_files_added) + " 个",
            "新增摘录 " + str(new_excerpt_count) + " 条",
            "正文读取 " + str(read_result_count) + " 个",
            "evidence_delta = " + str(evidence_delta_total),
        ]
        return leading + "；" + "，".join(detail_parts) + "。"

    def _build_patch_digest(self, step: BrainStepResult) -> str:
        parts: list[str] = []
        directive_updates = dict(step.workspace_patch.directive_updates or {})
        if directive_updates:
            parts.append("要求补充/调整：" + "、".join(sorted(directive_updates.keys())))

        evidence_updates = dict(step.workspace_patch.evidence_updates or {})
        if evidence_updates:
            parts.append("证据补充：" + "、".join(sorted(evidence_updates.keys())))

        outline_update = dict(step.workspace_patch.outline_update or {})
        if outline_update:
            outline_keys = [key for key in ("title", "global_objective", "open_gaps") if key in outline_update]
            if outline_keys:
                parts.append("提纲辅助信息更新：" + "、".join(outline_keys))

        revision_history = list(step.workspace_patch.revision_history_updates or [])
        if revision_history:
            parts.append("新增历史修订记录 " + str(len(revision_history)) + " 项")

        return "；".join(parts)

    def _update_round_debug_state(
        self,
        workspace: WorkspaceState,
        *,
        round_no: int,
        event_name: str = "",
        action_taken: str = "",
        result_status: str = "",
        compiled_summary: dict[str, Any] | None = None,
        request_summary: dict[str, Any] | None = None,
        response_summary: dict[str, Any] | None = None,
        step_summary: dict[str, Any] | None = None,
        debug_files: dict[str, str] | None = None,
        workspace_summary: dict[str, Any] | None = None,
        error_message: str = "",
    ) -> None:
        debug_state = workspace.debug_state
        existing_round = next(
            (item for item in debug_state.recent_rounds if item.round_no == round_no),
            None,
        )
        merged_files = dict(existing_round.debug_files) if existing_round is not None else {}
        merged_files.update(debug_files or {})

        effective_action = (
            action_taken
            or (step_summary or {}).get("action_taken", "")
            or (existing_round.action_taken if existing_round is not None else "")
        )
        effective_status = (
            result_status
            or (existing_round.result_status if existing_round is not None else "")
        )
        effective_workspace_summary = workspace_summary or self._summarize_workspace(workspace)
        effective_compiled_summary = compiled_summary or debug_state.last_compiled_context_summary
        effective_request_summary = request_summary or debug_state.last_llm_request_summary
        effective_response_summary = response_summary or debug_state.last_llm_response_summary
        effective_step_summary = step_summary or debug_state.last_step

        debug_state.last_round_no = max(int(debug_state.last_round_no or 0), round_no)
        if event_name:
            debug_state.last_event = event_name
        if effective_action:
            debug_state.last_action = str(effective_action)
        if error_message:
            debug_state.last_error = error_message
        if compiled_summary is not None:
            debug_state.last_compiled_context_summary = compiled_summary
        if request_summary is not None:
            debug_state.last_llm_request_summary = request_summary
        if response_summary is not None:
            debug_state.last_llm_response_summary = response_summary
        if step_summary is not None:
            debug_state.last_step = step_summary
        debug_state.last_workspace_summary = effective_workspace_summary

        round_summary = DebugRoundSummary(
            round_no=round_no,
            action_taken=str(effective_action),
            result_status=str(effective_status),
            business_completion_declared=bool(
                effective_step_summary.get("business_completion_declared", False)
            ),
            completion_mode=str(effective_step_summary.get("completion_mode", "") or ""),
            context_block_titles=list(effective_compiled_summary.get("block_titles", []) or []),
            truncated_block_titles=list(
                effective_compiled_summary.get("truncated_block_titles", []) or []
            ),
            tool_names=list(effective_step_summary.get("tool_names", []) or []),
            question_count=int(effective_workspace_summary.get("pending_question_count", 0) or 0),
            outline_status=str(effective_workspace_summary.get("outline_status", "") or ""),
            outline_section_count=int(
                effective_workspace_summary.get("outline_section_count", 0) or 0
            ),
            draft_status=str(effective_workspace_summary.get("draft_status", "") or ""),
            draft_word_count=int(effective_workspace_summary.get("draft_word_count", 0) or 0),
            dominant_issue=str(effective_workspace_summary.get("dominant_issue", "") or ""),
            open_gaps=list(effective_workspace_summary.get("open_gaps", []) or []),
            output_digest=str(effective_step_summary.get("output_digest", "") or ""),
            patch_digest=str(effective_step_summary.get("patch_digest", "") or ""),
            decision_trace_summary=list(
                effective_response_summary.get("decision_trace_summary", []) or []
            )[:8],
            orchestration_summary=self._normalize_orchestration_summary(
                effective_response_summary.get("orchestration_summary")
            ),
            llm_request_chars=int(effective_request_summary.get("user_prompt_chars", 0) or 0)
            + int(effective_request_summary.get("system_prompt_chars", 0) or 0)
            + int(effective_request_summary.get("context_chars", 0) or 0),
            llm_response_chars=int(effective_response_summary.get("content_chars", 0) or 0),
            llm_response_preview=str(
                effective_response_summary.get("content_preview", "") or ""
            ),
            debug_files=merged_files,
        )
        debug_state.upsert_round(round_summary)

    def _record_step_meta(self, workspace: WorkspaceState, step: BrainStepResult) -> None:
        history = workspace.session_meta.setdefault("action_history", [])
        history.append(step.action_taken)
        workspace.session_meta["last_action"] = step.action_taken
        workspace.session_meta["last_business_completion_declared"] = bool(
            step.business_completion_declared
        )
        workspace.session_meta["last_completion_mode"] = str(step.completion_mode or "").strip()
        workspace.session_meta["last_decision_rationale"] = str(
            step.decision_rationale or ""
        ).strip()
        workspace.session_meta["last_assumptions"] = list(step.assumptions or [])
        workspace.session_meta["last_major_risks"] = list(step.major_risks or [])

    def _record_runtime_profile(self, workspace: WorkspaceState, response: Any) -> None:
        raw_payload = dict(getattr(response, "raw_payload", {}) or {})
        runtime_workflow = str(raw_payload.get("runtime_workflow", "") or "").strip()
        if runtime_workflow:
            workspace.session_meta["runtime_workflow"] = runtime_workflow
        provider_profile = raw_payload.get("provider_profile")
        if isinstance(provider_profile, dict):
            workspace.session_meta["provider_profile"] = dict(provider_profile)
        decision_trace_summary = self._summarize_decision_trace(raw_payload.get("decision_trace"))
        if decision_trace_summary:
            workspace.session_meta["last_decision_trace_summary"] = decision_trace_summary
        orchestration_summary = self._normalize_orchestration_summary(
            raw_payload.get("orchestration_summary")
        )
        if orchestration_summary:
            workspace.session_meta["last_orchestration_summary"] = orchestration_summary

    def _record_quality_review_snapshot(
        self,
        workspace: WorkspaceState,
        step: BrainStepResult,
        *,
        source: str,
        step_summary: dict[str, Any] | None = None,
    ) -> None:
        snapshots = list(workspace.session_meta.get("quality_review_snapshots", []) or [])
        snapshot = {
            "created_at": utc_now_iso(),
            "source": str(source or "").strip() or "brain_step",
            "action_taken": step.action_taken,
            "business_completion_declared": bool(step.business_completion_declared),
            "completion_mode": str(step.completion_mode or "").strip(),
            "decision_rationale": str(step.decision_rationale or "").strip(),
            "assumptions": [
                str(item).strip()
                for item in list(step.assumptions or [])
                if str(item).strip()
            ][:5],
            "major_risks": [
                str(item).strip()
                for item in list(step.major_risks or [])
                if str(item).strip()
            ][:5],
            "dominant_issue": str(step.self_review.dominant_issue or "").strip(),
            "open_gaps": [
                str(item).strip()
                for item in list(step.self_review.open_gaps or [])
                if str(item).strip()
            ][:5],
            "responded_directives": [
                str(item).strip()
                for item in list(step.self_review.responded_directives or [])
                if str(item).strip()
            ],
            "content_status_summary": str(step.self_review.content_status_summary or "").strip(),
            "language_status_summary": str(step.self_review.language_status_summary or "").strip(),
            "notes": [
                str(item).strip()
                for item in list(step.self_review.notes or [])
                if str(item).strip()
            ][:5],
            "output_digest": str((step_summary or {}).get("output_digest", "") or ""),
        }
        snapshots.append(snapshot)
        workspace.session_meta["quality_review_snapshots"] = snapshots[-8:]

    def _resolve_final_text_for_export(
        self,
        workspace: WorkspaceState,
        step: BrainStepResult,
    ) -> str:
        _ = workspace
        return self._final_text_from_step(step)

    def _final_text_from_step(self, step: BrainStepResult) -> str:
        payload = getattr(step, "action_payload", None)
        return str(getattr(payload, "final_text", "") or "").strip()

    def _apply_action_payload_fallbacks(
        self,
        workspace: WorkspaceState,
        step: BrainStepResult,
    ) -> None:
        if step.action_taken == "build_outline":
            outline_text = str(getattr(step.action_payload, "outline_text", "") or "").strip()
            outline_sections = list(getattr(step.action_payload, "outline_sections", []))
            if outline_text:
                workspace.outline_artifact.outline_text = outline_text
            if outline_sections:
                workspace.outline_artifact.sections = [
                    section
                    if isinstance(section, OutlineSection)
                    else OutlineSection.from_dict(section)
                    for section in outline_sections
                ]
            if (
                workspace.outline_artifact.title
                or workspace.outline_artifact.outline_text
                or workspace.outline_artifact.sections
            ):
                workspace.outline_artifact.status = "drafted"

        if step.action_taken == "write_draft":
            draft_text = str(getattr(step.action_payload, "draft_text", "") or "").strip()
            if draft_text:
                workspace.draft_artifact.full_text = draft_text
                workspace.draft_artifact.section_map = {}
                workspace.draft_artifact.assembly_mode = "full_text"
                workspace.session_meta["last_draft_transition"] = "reset_full_text"
                workspace.draft_artifact.word_count = len(draft_text.replace(" ", ""))
                workspace.draft_artifact.status = "drafted"

        if step.action_taken == "write_section":
            section_id = str(getattr(step.action_payload, "section_id", "") or "").strip()
            section_text = str(getattr(step.action_payload, "section_text", "") or "").strip()
            if section_id and section_text:
                previous_text = str(workspace.draft_artifact.section_map.get(section_id, "") or "")
                workspace.draft_artifact.section_map[section_id] = section_text
                transition_state = "append_fallback"
                (
                    workspace.draft_artifact.full_text,
                    workspace.draft_artifact.assembly_mode,
                    transition_state,
                ) = self._update_full_text_from_section(
                    workspace,
                    section_id=section_id,
                    previous_text=previous_text,
                    section_text=section_text,
                )
                workspace.session_meta["last_draft_transition"] = transition_state
                workspace.draft_artifact.word_count = len(
                    workspace.draft_artifact.full_text.replace(" ", "")
                )
                workspace.draft_artifact.status = "drafted"

        if step.action_taken == "revise_draft":
            revised_text = str(getattr(step.action_payload, "revised_text", "") or "").strip()
            if revised_text:
                workspace.draft_artifact.full_text = revised_text
                workspace.draft_artifact.section_map = {}
                workspace.draft_artifact.assembly_mode = "full_text"
                workspace.session_meta["last_draft_transition"] = "reset_full_text"
                workspace.draft_artifact.word_count = len(revised_text.replace(" ", ""))
                workspace.draft_artifact.status = "drafted"
            workspace.session_meta["revision_round_count"] = int(
                workspace.session_meta.get("revision_round_count", 0)
            ) + 1

        if step.action_taken == "polish_language":
            polished_text = str(getattr(step.action_payload, "polished_text", "") or "").strip()
            if polished_text:
                workspace.draft_artifact.full_text = polished_text
                workspace.draft_artifact.section_map = {}
                workspace.draft_artifact.assembly_mode = "full_text"
                workspace.session_meta["last_draft_transition"] = "reset_full_text"
                workspace.draft_artifact.word_count = len(polished_text.replace(" ", ""))
                workspace.draft_artifact.status = "drafted"

        if step.action_taken == "finalize":
            final_text = self._final_text_from_step(step)
            if final_text:
                workspace.draft_artifact.full_text = final_text
                workspace.draft_artifact.section_map = {}
                workspace.draft_artifact.assembly_mode = "full_text"
                workspace.session_meta["last_draft_transition"] = "reset_full_text"
                workspace.draft_artifact.word_count = len(final_text.replace(" ", ""))
                workspace.draft_artifact.status = "finalized"

    def _update_workspace_self_review(
        self,
        workspace: WorkspaceState,
        step: BrainStepResult,
    ) -> None:
        if step.action_taken in CONTROL_ONLY_ACTIONS or not step.has_self_review:
            return
        workspace.self_review = step.self_review

    def _build_revision_history_entries(
        self,
        step: BrainStepResult,
        *,
        before_word_count: int,
        after_word_count: int,
    ) -> list[dict[str, Any]]:
        if step.action_taken in CONTROL_ONLY_ACTIONS:
            return []

        raw_entries = list(step.workspace_patch.revision_history_updates or [])
        if not raw_entries:
            return [
                {
                    "source": "runtime",
                    "action_taken": step.action_taken,
                    "summary": self._build_output_digest(step) or "本轮完成一次内容推进。",
                    "focus": self._infer_revision_focus(step),
                    "target_sections": self._infer_revision_target_sections(step),
                    "before_word_count": before_word_count,
                    "after_word_count": after_word_count,
                    "notes": [],
                }
            ]

        normalized_entries: list[dict[str, Any]] = []
        default_summary = self._build_output_digest(step) or "本轮完成一次内容推进。"
        default_focus = self._infer_revision_focus(step)
        default_target_sections = self._infer_revision_target_sections(step)
        for raw in raw_entries:
            entry = dict(raw)
            if not str(entry.get("source", "") or "").strip():
                entry["source"] = "agents_runtime"
            if not str(entry.get("action_taken", "") or "").strip():
                entry["action_taken"] = step.action_taken
            if not str(entry.get("summary", "") or "").strip():
                entry["summary"] = default_summary
            focus = [
                str(item).strip()
                for item in list(entry.get("focus", []) or [])
                if str(item).strip()
            ]
            if not focus:
                focus = default_focus
            entry["focus"] = focus
            target_sections = [
                str(item).strip()
                for item in list(entry.get("target_sections", []) or [])
                if str(item).strip()
            ]
            if not target_sections:
                target_sections = default_target_sections
            entry["target_sections"] = target_sections
            if not int(entry.get("before_word_count", 0) or 0):
                entry["before_word_count"] = before_word_count
            if not int(entry.get("after_word_count", 0) or 0):
                entry["after_word_count"] = after_word_count
            entry["notes"] = [
                str(item).strip()
                for item in list(entry.get("notes", []) or [])
                if str(item).strip()
            ]
            normalized_entries.append(entry)
        return normalized_entries

    def _infer_revision_focus(self, step: BrainStepResult) -> list[str]:
        action = str(step.action_taken or "").strip()
        if action == "build_outline":
            return ["提纲搭建", "结构规划"]
        if action == "write_draft":
            return ["整稿起草", "内容铺开"]
        if action == "write_section":
            return ["局部补写", "章节替换"]
        if action == "revise_draft":
            return ["结构修订", "内容增强"]
        if action == "polish_language":
            return ["语言润色", "文风提升"]
        if action == "finalize":
            return ["终稿收口", "交付定稿"]
        return []

    def _infer_revision_target_sections(self, step: BrainStepResult) -> list[str]:
        if step.action_taken != "write_section":
            return []
        section_id = str(getattr(step.action_payload, "section_id", "") or "").strip()
        return [section_id] if section_id else []

    def _update_full_text_from_section(
        self,
        workspace: WorkspaceState,
        *,
        section_id: str,
        previous_text: str,
        section_text: str,
    ) -> tuple[str, str, str]:
        existing_full_text = str(workspace.draft_artifact.full_text or "").strip()
        baseline_section_map = self._build_baseline_section_map(
            workspace,
            section_id=section_id,
            previous_text=previous_text,
        )
        if self._should_render_from_section_map(
            workspace,
            existing_full_text=existing_full_text,
            baseline_section_map=baseline_section_map,
        ):
            rendered = self._render_full_text_from_section_map(workspace)
            if rendered:
                return rendered, "sectional", "render_from_section_map"
        promoted_section_map = self._try_promote_full_text_to_sectional(
            workspace,
            existing_full_text=existing_full_text,
            baseline_section_map=baseline_section_map,
        )
        if promoted_section_map:
            promoted_section_map.update(
                self._normalize_section_map(workspace.draft_artifact.section_map)
            )
            workspace.draft_artifact.section_map = promoted_section_map
            rendered = self._render_full_text_from_section_map(workspace)
            if rendered:
                return rendered, "sectional", "promoted_from_full_text"
        return (
            self._merge_section_text_append_fallback(
                workspace.draft_artifact.full_text,
                previous_text,
                section_text,
            ),
            "full_text",
            "append_fallback",
        )

    def _build_baseline_section_map(
        self,
        workspace: WorkspaceState,
        *,
        section_id: str,
        previous_text: str,
    ) -> dict[str, str]:
        baseline_section_map = dict(workspace.draft_artifact.section_map)
        if previous_text:
            baseline_section_map[section_id] = previous_text
        else:
            baseline_section_map.pop(section_id, None)
        return self._normalize_section_map(baseline_section_map)

    def _should_render_from_section_map(
        self,
        workspace: WorkspaceState,
        *,
        existing_full_text: str,
        baseline_section_map: dict[str, str],
    ) -> bool:
        assembly_mode = str(workspace.draft_artifact.assembly_mode or "").strip()
        if assembly_mode == "sectional":
            return True
        if not existing_full_text:
            return True
        return self._section_map_matches_full_text(
            workspace,
            full_text=existing_full_text,
            section_map=baseline_section_map,
        )

    def _section_map_matches_full_text(
        self,
        workspace: WorkspaceState,
        *,
        full_text: str,
        section_map: dict[str, str],
    ) -> bool:
        rendered_before = self._render_full_text_from_section_map(
            workspace,
            section_map=section_map,
        )
        return bool(
            rendered_before and self._normalize_text(full_text) == self._normalize_text(rendered_before)
        )

    def _try_promote_full_text_to_sectional(
        self,
        workspace: WorkspaceState,
        *,
        existing_full_text: str,
        baseline_section_map: dict[str, str],
    ) -> dict[str, str] | None:
        full_text = str(existing_full_text or "").strip()
        if not full_text:
            return None

        outline_entries = self._outline_section_entries(workspace)
        if not outline_entries:
            return None

        if len(outline_entries) == 1:
            promoted_section_map = {outline_entries[0][0]: full_text}
        else:
            if any(not heading for _, heading in outline_entries):
                return None

            lines = full_text.splitlines()
            if not lines:
                return None

            matches: list[tuple[str, int]] = []
            cursor = 0
            for outline_section_id, heading in outline_entries:
                line_index = self._find_heading_line_index(
                    lines,
                    heading,
                    start_index=cursor,
                )
                if line_index is None:
                    return None
                matches.append((outline_section_id, line_index))
                cursor = line_index + 1

            promoted_section_map = {}
            for index, (outline_section_id, line_index) in enumerate(matches):
                start_index = 0 if index == 0 else line_index
                end_index = matches[index + 1][1] if index + 1 < len(matches) else len(lines)
                candidate_text = "\n".join(lines[start_index:end_index]).strip()
                if not candidate_text:
                    return None
                promoted_section_map[outline_section_id] = candidate_text

        promoted_section_map.update(self._normalize_section_map(baseline_section_map))
        normalized_section_map = self._normalize_section_map(promoted_section_map)
        if not self._section_map_matches_full_text(
            workspace,
            full_text=full_text,
            section_map=normalized_section_map,
        ):
            return None
        return normalized_section_map

    def _outline_section_entries(self, workspace: WorkspaceState) -> list[tuple[str, str]]:
        ordered_entries: list[tuple[str, str]] = []
        for section in list(workspace.outline_artifact.sections or []):
            if isinstance(section, OutlineSection):
                section_id = str(section.section_id or "").strip()
                heading = str(section.heading or "").strip()
            elif isinstance(section, dict):
                section_id = str(section.get("section_id", "") or "").strip()
                heading = str(section.get("heading", "") or "").strip()
            else:
                section_id = ""
                heading = ""
            if section_id:
                ordered_entries.append((section_id, heading))
        return ordered_entries

    def _find_heading_line_index(
        self,
        lines: list[str],
        heading: str,
        *,
        start_index: int,
    ) -> int | None:
        for index in range(max(start_index, 0), len(lines)):
            if self._line_matches_outline_heading(lines[index], heading):
                return index
        return None

    def _line_matches_outline_heading(self, line: str, heading: str) -> bool:
        line_keys = self._heading_match_keys(line)
        heading_keys = self._heading_match_keys(heading)
        if not line_keys or not heading_keys:
            return False

        for line_key in line_keys:
            for heading_key in heading_keys:
                if line_key == heading_key:
                    return True
                if line_key.startswith(heading_key):
                    suffix = line_key[len(heading_key) :]
                    if suffix and suffix[0] in "：:（(":
                        return True
        return False

    def _heading_match_keys(self, text: str) -> list[str]:
        candidates = [
            self._normalize_heading_key(text),
            self._normalize_heading_key(self._strip_heading_prefix(text)),
        ]
        normalized: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized

    def _normalize_heading_key(self, text: str) -> str:
        normalized = re.sub(r"\s+", "", str(text or "").strip())
        return normalized.strip("：:；;，,。.!！？?、")

    def _strip_heading_prefix(self, text: str) -> str:
        normalized = str(text or "").strip()
        patterns = (
            r"^第[一二三四五六七八九十百零0-9]+[章节部分]\s*",
            r"^[（(][一二三四五六七八九十百零0-9]+[)）]\s*",
            r"^[一二三四五六七八九十百零0-9]+[、.．]\s*",
        )
        for pattern in patterns:
            normalized = re.sub(pattern, "", normalized, count=1)
        return normalized.strip()

    def _render_full_text_from_section_map(
        self,
        workspace: WorkspaceState,
        *,
        section_map: dict[str, str] | None = None,
    ) -> str:
        normalized_section_map = self._normalize_section_map(
            section_map if section_map is not None else workspace.draft_artifact.section_map
        )
        if not normalized_section_map:
            return ""

        ordered_section_ids = self._ordered_outline_section_ids(workspace)
        rendered_parts: list[str] = []
        used_ids: set[str] = set()

        if ordered_section_ids:
            for section_id in ordered_section_ids:
                text = normalized_section_map.get(section_id, "")
                if not text:
                    continue
                rendered_parts.append(text)
                used_ids.add(section_id)
        else:
            for section_id, text in normalized_section_map.items():
                if text:
                    rendered_parts.append(text)
                    used_ids.add(section_id)

        for section_id, text in normalized_section_map.items():
            if section_id in used_ids or not text:
                continue
            rendered_parts.append(text)

        return "\n\n".join(part for part in rendered_parts if part).strip()

    def _ordered_outline_section_ids(self, workspace: WorkspaceState) -> list[str]:
        ordered: list[str] = []
        for section in list(workspace.outline_artifact.sections or []):
            if isinstance(section, OutlineSection):
                section_id = str(section.section_id or "").strip()
            elif isinstance(section, dict):
                section_id = str(section.get("section_id", "") or "").strip()
            else:
                section_id = ""
            if section_id and section_id not in ordered:
                ordered.append(section_id)
        return ordered

    def _normalize_section_map(self, section_map: dict[str, str] | None) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in dict(section_map or {}).items():
            section_id = str(key or "").strip()
            text = str(value or "").strip()
            if section_id and text:
                normalized[section_id] = text
        return normalized

    def _normalize_text(self, text: str) -> str:
        return " ".join(str(text or "").split())

    def _merge_section_text_append_fallback(
        self,
        full_text: str,
        previous_text: str,
        section_text: str,
    ) -> str:
        existing_full_text = str(full_text or "").strip()
        old_section = str(previous_text or "").strip()
        new_section = str(section_text or "").strip()
        if not new_section:
            return existing_full_text
        if not existing_full_text:
            return new_section
        if old_section and old_section in existing_full_text:
            return existing_full_text.replace(old_section, new_section, 1)
        return existing_full_text.rstrip() + "\n\n" + new_section

    def _record_runtime_event(
        self,
        session_id: str,
        event_type: str,
        *,
        level: int,
        **payload: Any,
    ) -> None:
        log_structured(self.logger, level, event_type, session_id=session_id, **payload)
        self.event_writer.record(session_id, event_type, payload)

    def _report_user_progress(self, message: str) -> None:
        if self.progress_reporter is None:
            return
        normalized = str(message or "").strip()
        if not normalized:
            return
        self.progress_reporter(normalized)

    def _report_round_step_progress(
        self,
        round_no: int,
        step_summary: dict[str, Any] | None,
    ) -> None:
        if self.round_reporter is not None:
            return
        summary = dict(step_summary or {})
        digest = str(summary.get("output_digest", "") or "").strip()
        if digest:
            self._report_user_progress(f"第 {round_no} 轮：{digest}")
            return
        action_taken = str(summary.get("action_taken", "") or "").strip()
        if action_taken:
            self._report_user_progress(f"第 {round_no} 轮：已执行 {action_taken}。")

    def _report_round_result(self, turn_result: TurnRunResult) -> None:
        if self.round_reporter is None:
            return
        self.round_reporter(turn_result)

    def _checkpoint_workspace(
        self,
        *,
        session_id: str,
        round_no: int,
        label: str,
        reason: str,
        workspace: WorkspaceState,
        event_name: str,
        action_taken: str,
        result_status: str,
        compiled_summary: dict[str, Any] | None = None,
        request_summary: dict[str, Any] | None = None,
        response_summary: dict[str, Any] | None = None,
        step_summary: dict[str, Any] | None = None,
        debug_files: dict[str, str] | None = None,
        turn_result: TurnRunResult | None = None,
    ) -> None:
        workspace_summary = self._summarize_workspace(workspace)
        checkpoint_debug_file = self._write_round_debug_file(
            session_id,
            round_no,
            label,
            workspace.to_dict(),
            round_debug_files=debug_files if debug_files is not None else {},
        )
        self._update_round_debug_state(
            workspace,
            round_no=round_no,
            event_name=event_name,
            action_taken=action_taken,
            result_status=result_status,
            compiled_summary=compiled_summary,
            request_summary=request_summary,
            response_summary=response_summary,
            step_summary=step_summary,
            debug_files=debug_files,
            workspace_summary=workspace_summary,
        )
        self.workspace_store.save(workspace)
        if turn_result is not None:
            self._report_round_result(turn_result)
        self._record_runtime_event(
            session_id,
            "workspace_checkpoint_saved",
            level=logging.INFO,
            round_no=round_no,
            reason=reason,
            workspace_summary=workspace_summary,
            debug_file=checkpoint_debug_file,
        )


def create_app(
    config: AppConfig | None = None,
    **kwargs: Any,
) -> SuperGongwenApp:
    return SuperGongwenApp(config=config, **kwargs)

