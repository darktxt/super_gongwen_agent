from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app_result_writer import apply_result, status_for_turn
from config import AppConfig, load_config
from runtime_logging import RuntimeLogRecorder
from runtime_core import CoordinatorResult, LiteLLMAgentsRuntime
from session_storage.history import initialize_session_storage
from workspace.models import WorkspaceState
from workspace.patcher import WorkspacePatcher
from workspace.store import WorkspaceStore


def generate_session_id() -> str:
    return "session-" + uuid4().hex[:12]


@dataclass(slots=True)
class AppBootstrapResult:
    session_id: str | None
    app_home: str
    sessions_root: str
    message: str


@dataclass(slots=True)
class TurnRunResult:
    session_id: str
    status: str
    action: str
    response_text: str = ""
    final_text: str = ""
    final_output_path: str = ""
    question_pack: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    major_risks: list[str] = field(default_factory=list)
    workspace: WorkspaceState | None = None
    error_message: str = ""


class SuperGongwenApp:
    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        runtime: LiteLLMAgentsRuntime | None = None,
        working_root: str | Path | None = None,
        progress_reporter: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config or load_config()
        self.workspace_store = WorkspaceStore(app_home=self.config.app_home)
        self.workspace_patcher = WorkspacePatcher()
        self.log_recorder = RuntimeLogRecorder(app_home=self.config.app_home)
        self.runtime = runtime
        self.working_root = Path(working_root).resolve() if working_root is not None else Path.cwd()
        self.progress_reporter = progress_reporter

    def bootstrap(self, session_id: str | None = None) -> AppBootstrapResult:
        resolved_session_id = session_id or generate_session_id()
        initialize_session_storage(session_id=resolved_session_id, app_home=self.config.app_home)
        self.workspace_store.load_or_create(session_id=resolved_session_id)
        return AppBootstrapResult(
            session_id=resolved_session_id,
            app_home=str(self.config.app_home),
            sessions_root=str(self.config.sessions_root),
            message="super-gongwen-lite bootstrap ready",
        )

    def run_turn(self, session_id: str, user_input: str) -> TurnRunResult:
        workspace = self.workspace_store.load_or_create(session_id=session_id)
        self.workspace_patcher.ingest_user_message(workspace, user_input)
        round_no = max(int(workspace.debug_state.last_round_no or 0) + 1, 1)
        applied_tool_ids: set[str] = set()
        live_tool_results: list[dict[str, Any]] = []
        initial_request_summary = self.log_recorder.build_initial_request_summary(
            session_id=session_id,
            round_no=round_no,
            user_input=user_input,
            workspace=workspace,
        )
        self.log_recorder.write_live_debug(
            session_id=session_id,
            round_no=round_no,
            status="running",
            request_summary=initial_request_summary,
            workspace=workspace,
            tool_results=live_tool_results,
        )

        def _on_tool_event(event: dict[str, Any]) -> None:
            request_id = str(event.get("request_id", "") or "")
            if request_id and request_id in applied_tool_ids:
                return
            if request_id:
                applied_tool_ids.add(request_id)
            live_tool_results.append(event)
            self.workspace_patcher.apply_tool_results(workspace, [event])
            self.log_recorder.mark_tool_event(workspace=workspace, event=event)
            self.workspace_store.save(workspace)
            self.log_recorder.write_live_debug(
                session_id=session_id,
                round_no=round_no,
                status="running",
                request_summary=initial_request_summary,
                workspace=workspace,
                tool_results=live_tool_results,
            )
            self._report(f"工具 {event.get('tool_name', '')}：{event.get('summary', '')}")

        self._report("正在通过 LiteLLM Agents Runtime 处理本轮请求。")
        outcome = self._runtime().run_turn(
            session_id=session_id,
            workspace=workspace,
            user_input=user_input,
            working_root=self.working_root,
            on_tool_event=_on_tool_event,
        )
        outcome.diagnostics = self.log_recorder.normalize_diagnostics(
            session_id=session_id,
            round_no=round_no,
            user_input=user_input,
            outcome=outcome,
        )
        self.workspace_patcher.apply_tool_results(
            workspace,
            [event for event in outcome.tool_events if str(event.get("request_id", "") or "") not in applied_tool_ids],
        )
        result = outcome.result
        delivery_decision = outcome.delivery_decision
        status = status_for_turn(result=result, delivery_decision=delivery_decision)
        final_output_path = apply_result(
            workspace,
            result=result,
            delivery_decision=delivery_decision,
            session_id=session_id,
            app_home=self.config.app_home,
        )
        debug_payload, debug_files = self.log_recorder.build_final_debug_payload(
            session_id=session_id,
            round_no=round_no,
            workspace=workspace,
            user_input=user_input,
            outcome=outcome,
            status=status,
            final_output_path=final_output_path,
        )
        self.log_recorder.update_debug_state(
            workspace,
            result=result,
            outcome=outcome,
            status=status,
            user_input=user_input,
            round_no=round_no,
            debug_files=debug_files,
        )
        self.workspace_store.save(workspace)
        self.log_recorder.write_final_debug_files(
            session_id=session_id,
            round_no=round_no,
            payload=debug_payload,
        )
        return TurnRunResult(
            session_id=session_id,
            status=status,
            action=result.action,
            response_text=result.response_text.strip(),
            final_text=(delivery_decision.text or result.final_text or result.draft_text).strip(),
            final_output_path=str(final_output_path) if final_output_path else "",
            question_pack=[item.model_dump() for item in result.question_pack],
            tool_results=outcome.tool_events,
            assumptions=list(result.assumptions),
            major_risks=list(result.major_risks),
            workspace=workspace,
        )

    def _runtime(self) -> LiteLLMAgentsRuntime:
        if self.runtime is None:
            self.runtime = LiteLLMAgentsRuntime.from_config(self.config)
        return self.runtime

    def _report(self, message: str) -> None:
        if self.progress_reporter is not None:
            self.progress_reporter(message)


def create_app(
    *,
    config: AppConfig | None = None,
    runtime: LiteLLMAgentsRuntime | None = None,
    working_root: str | Path | None = None,
    progress_reporter: Callable[[str], None] | None = None,
) -> SuperGongwenApp:
    return SuperGongwenApp(
        config=config,
        runtime=runtime,
        working_root=working_root,
        progress_reporter=progress_reporter,
    )
