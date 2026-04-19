from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from config import AppConfig, load_config
from runtime_core import CoordinatorResult, LiteLLMAgentsRuntime, RuntimeOutcome
from session_storage.history import initialize_session_storage, save_final_output, write_debug_json
from workspace.models import OutlineArtifact, OutlineSection, SelfReview, WorkspaceState
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
        applied_tool_ids: set[str] = set()
        live_tool_results: list[dict[str, Any]] = []
        def _write_live_debug(status: str) -> None:
            write_debug_json(
                session_id=session_id,
                filename="latest_run.json",
                payload={"status": status, "last_event": workspace.debug_state.last_event, "workspace_summary": self._workspace_summary(workspace), "tool_results": live_tool_results},
                app_home=self.config.app_home,
            )
        _write_live_debug("running")
        def _on_tool_event(event: dict[str, Any]) -> None:
            request_id = str(event.get("request_id", "") or "")
            if request_id and request_id in applied_tool_ids:
                return
            if request_id:
                applied_tool_ids.add(request_id)
            live_tool_results.append(event)
            self.workspace_patcher.apply_tool_results(workspace, [event])
            workspace.debug_state.last_event = "tool_event"
            workspace.debug_state.last_action = f"tool:{event.get('tool_name', '')}"
            workspace.debug_state.last_step = {"tool_event": event}
            workspace.debug_state.last_workspace_summary = self._workspace_summary(workspace)
            self.workspace_store.save(workspace)
            _write_live_debug("running")
            self._report(f"工具 {event.get('tool_name', '')}：{event.get('summary', '')}")
        self._report("正在通过 LiteLLM Agents Runtime 处理本轮请求。")
        outcome = self._runtime().run_turn(
            session_id=session_id,
            workspace=workspace,
            user_input=user_input,
            working_root=self.working_root,
            on_tool_event=_on_tool_event,
        )
        self.workspace_patcher.apply_tool_results(workspace, [event for event in outcome.tool_events if str(event.get("request_id", "") or "") not in applied_tool_ids])
        result = outcome.result
        final_output_path = self._apply_result(workspace, result=result, session_id=session_id)
        self._update_debug_state(workspace, result=result, outcome=outcome, user_input=user_input)
        self.workspace_store.save(workspace)
        write_debug_json(session_id=session_id, filename="latest_run.json", payload={"action": result.action, "completion_mode": result.completion_mode, "decision_rationale": result.decision_rationale, "assumptions": result.assumptions, "major_risks": result.major_risks, "tool_results": outcome.tool_events}, app_home=self.config.app_home)
        return TurnRunResult(
            session_id=session_id,
            status=self._status_for_action(result.action),
            action=result.action,
            response_text=result.response_text.strip(),
            final_text=(result.final_text or result.draft_text).strip(),
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
    def _apply_result(self, workspace: WorkspaceState, *, result: CoordinatorResult, session_id: str) -> Path | None:
        workspace.session_meta["runtime_workflow"] = "litellm_agents_sdk"
        workspace.session_meta["quality_review_notes"] = [result.review_summary] if result.review_summary else []
        workspace.pending_questions = [item.model_dump() for item in result.question_pack]
        workspace.self_review = SelfReview(
            dominant_issue=(result.major_risks[0] if result.major_risks else result.review_summary[:120]),
            open_gaps=list(result.major_risks),
            content_status_summary=result.review_summary,
            notes=list(result.assumptions),
        )
        if result.outline_sections:
            workspace.outline_artifact = OutlineArtifact(
                title=result.outline_title or workspace.outline_artifact.title,
                sections=[OutlineSection(section_id=f"section_{index}", heading=section.heading, goal=section.goal, required_points=list(section.required_points), notes=list(section.notes)) for index, section in enumerate(result.outline_sections, start=1)],
                global_objective=workspace.task_brief,
                outline_text="\n".join(section.heading for section in result.outline_sections),
                open_gaps=list(result.major_risks),
                status="drafted",
            )
        text = (result.final_text if result.action == "finalize" else result.draft_text).strip()
        if text:
            workspace.draft_artifact.title = result.outline_title or workspace.draft_artifact.title or "公文草稿"
            workspace.draft_artifact.full_text = text
            workspace.draft_artifact.word_count = len(text)
            workspace.draft_artifact.status = "finalized" if result.action == "finalize" else "drafted"
        if result.action == "finalize" and text:
            return save_final_output(session_id=session_id, content=text, app_home=self.config.app_home)
        return None
    def _update_debug_state(
        self,
        workspace: WorkspaceState,
        *,
        result: CoordinatorResult,
        outcome: RuntimeOutcome,
        user_input: str,
    ) -> None:
        workspace.debug_state.last_user_input = user_input.strip()
        workspace.debug_state.last_action = result.action
        workspace.debug_state.last_event = "turn_completed"
        workspace.debug_state.last_error = ""
        workspace.debug_state.last_step = result.model_dump()
        workspace.debug_state.last_workspace_summary = self._workspace_summary(workspace)
        workspace.debug_state.last_llm_response_summary = {"action": result.action, "completion_mode": result.completion_mode, "tool_call_count": len(outcome.tool_events), "review_summary": result.review_summary}
    def _workspace_summary(self, workspace: WorkspaceState) -> dict[str, Any]:
        return {"outline_status": workspace.outline_artifact.status, "draft_status": workspace.draft_artifact.status, "draft_word_count": workspace.draft_artifact.word_count, "pending_question_count": len(workspace.pending_questions), "selected_file_count": len(workspace.material_catalog.selected_files), "excerpt_count": len(workspace.retrieved_materials.excerpts)}
    def _status_for_action(self, action: str) -> str:
        if action == "finalize":
            return "completed"
        if action == "ask_user":
            return "needs_user_input"
        return "in_progress"
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
