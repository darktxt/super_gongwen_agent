from __future__ import annotations

from pathlib import Path
from typing import Any

from session_storage.history import write_debug_json
from workspace.models import DebugRoundSummary, WorkspaceState


class RuntimeLogRecorder:
    def __init__(self, *, app_home: str | Path) -> None:
        self.app_home = Path(app_home)

    def build_initial_request_summary(
        self,
        *,
        session_id: str,
        round_no: int,
        user_input: str,
        workspace: WorkspaceState,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "round_no": round_no,
            "user_input_chars": len(user_input.strip()),
            "user_input_preview": self.preview(user_input, limit=180),
            "pending_question_count": len(workspace.pending_questions),
            "selected_file_count": len(workspace.material_catalog.selected_files),
        }

    def write_live_debug(
        self,
        *,
        session_id: str,
        round_no: int,
        status: str,
        request_summary: dict[str, Any],
        workspace: WorkspaceState,
        tool_results: list[dict[str, Any]],
    ) -> None:
        payload = {
            "status": status,
            "session_id": session_id,
            "round_no": round_no,
            "last_event": workspace.debug_state.last_event,
            "request_summary": request_summary,
            "workspace_summary": self.workspace_summary(workspace),
            "tool_results": tool_results,
        }
        write_debug_json(
            session_id=session_id,
            filename="latest_run.json",
            payload=payload,
            app_home=self.app_home,
        )

    def mark_tool_event(
        self,
        *,
        workspace: WorkspaceState,
        event: dict[str, Any],
    ) -> None:
        workspace.debug_state.last_event = "tool_event"
        workspace.debug_state.last_action = f"tool:{event.get('tool_name', '')}"
        workspace.debug_state.last_step = {"tool_event": event}
        workspace.debug_state.last_workspace_summary = self.workspace_summary(workspace)

    def normalize_diagnostics(
        self,
        *,
        session_id: str,
        round_no: int,
        user_input: str,
        outcome: Any,
    ) -> dict[str, Any]:
        diagnostics = dict(outcome.diagnostics or {})
        request_summary = dict(diagnostics.get("request_summary", {}) or {})
        response_summary = dict(diagnostics.get("response_summary", {}) or {})
        error_summary = dict(diagnostics.get("error", {}) or {})
        recovery_summary = dict(diagnostics.get("recovery", {}) or {})
        request_summary.setdefault("session_id", session_id)
        request_summary.setdefault("round_no", round_no)
        request_summary.setdefault("user_input_chars", len(user_input.strip()))
        request_summary.setdefault("user_input_preview", self.preview(user_input, limit=180))
        response_summary.setdefault("structured_output_succeeded", not bool(error_summary))
        response_summary.setdefault("tool_event_count", len(outcome.tool_events))
        response_summary.setdefault("raw_output_type", type(outcome.raw_output).__name__)
        response_summary.setdefault("raw_output_preview", self.preview(outcome.raw_output or "", limit=240))
        response_summary.setdefault("last_text_preview", response_summary.get("raw_output_preview", ""))
        response_summary.setdefault("last_text_chars", len(str(outcome.raw_output or "")))
        diagnostics["status"] = str(diagnostics.get("status") or ("recovered_model_behavior_error" if error_summary else "ok"))
        diagnostics["request_summary"] = request_summary
        diagnostics["response_summary"] = response_summary
        diagnostics.setdefault("delivery_decision", outcome.delivery_decision.to_dict())
        diagnostics["error"] = error_summary
        diagnostics["recovery"] = recovery_summary
        diagnostics.setdefault(
            "result_summary",
            {
                "action": outcome.result.action,
                "completion_mode": outcome.result.completion_mode,
                "question_count": len(outcome.result.question_pack),
                "major_risk_count": len(outcome.result.major_risks),
                "response_preview": self.preview(
                    outcome.result.response_text or outcome.result.decision_rationale,
                    limit=180,
                ),
            },
        )
        return diagnostics

    def build_final_debug_payload(
        self,
        *,
        session_id: str,
        round_no: int,
        workspace: WorkspaceState,
        user_input: str,
        outcome: Any,
        status: str,
        final_output_path: Path | None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        archive_name = f"round_{round_no:04d}.json"
        debug_files = {
            "latest": "latest_run.json",
            "archive": archive_name,
        }
        payload = {
            "status": status,
            "session_id": session_id,
            "round_no": round_no,
            "user_input": user_input.strip(),
            "workspace_summary": self.workspace_summary(workspace),
            "tool_results": outcome.tool_events,
            "result": outcome.result.model_dump(exclude_none=True),
            "diagnostics": outcome.diagnostics,
            "delivery_decision": outcome.delivery_decision.to_dict(),
            "final_output_path": str(final_output_path) if final_output_path else "",
            "debug_files": debug_files,
        }
        return payload, debug_files

    def update_debug_state(
        self,
        workspace: WorkspaceState,
        *,
        result: Any,
        outcome: Any,
        status: str,
        user_input: str,
        round_no: int,
        debug_files: dict[str, str],
    ) -> None:
        request_summary = dict(outcome.diagnostics.get("request_summary", {}) or {})
        response_summary = dict(outcome.diagnostics.get("response_summary", {}) or {})
        delivery_summary = dict(outcome.diagnostics.get("delivery_decision", {}) or {})
        error_summary = dict(outcome.diagnostics.get("error", {}) or {})
        recovery_summary = dict(outcome.diagnostics.get("recovery", {}) or {})
        error_message = str(error_summary.get("message", "") or "")
        classification_label = str(error_summary.get("classification_label", "") or "")
        judge_runs = list(outcome.diagnostics.get("judge_runs", []) or [])
        workspace.debug_state.last_user_input = user_input.strip()
        workspace.debug_state.last_round_no = round_no
        workspace.debug_state.last_action = result.action
        workspace.debug_state.last_event = "turn_completed"
        workspace.debug_state.last_error = error_message
        workspace.debug_state.last_compiled_context_summary = request_summary
        workspace.debug_state.last_llm_request_summary = request_summary
        workspace.debug_state.last_llm_response_summary = {
            **response_summary,
            "action": result.action,
            "completion_mode": result.completion_mode,
            "tool_call_count": len(outcome.tool_events),
            "review_summary": result.review_summary,
            "judge_run_count": len(judge_runs),
            "structured_output_failed": bool(error_summary),
            "error_classification": classification_label,
            "repair_source": str(
                error_summary.get("repair_source", "")
                or recovery_summary.get("fallback_source", "")
                or ""
            ),
            "repair_steps": list(
                error_summary.get("repair_steps", [])
                or recovery_summary.get("repair_steps", [])
                or []
            ),
            "delivery_completed": bool(delivery_summary.get("completed")),
            "delivery_reason": str(delivery_summary.get("reason", "") or ""),
        }
        workspace.debug_state.last_step = {
            "result": result.model_dump(exclude_none=True),
            "diagnostics": outcome.diagnostics,
        }
        workspace.debug_state.last_workspace_summary = self.workspace_summary(workspace)
        quality_review_snapshots = list(workspace.session_meta.get("quality_review_snapshots", []) or [])
        quality_review_snapshots.append(
            {
                "round_no": round_no,
                "review_summary": result.review_summary,
                "judge_runs": judge_runs,
            }
        )
        workspace.session_meta["quality_review_snapshots"] = quality_review_snapshots
        workspace.workflow_state.quality_review_cycles += len(judge_runs)
        workspace.debug_state.upsert_round(
            DebugRoundSummary(
                round_no=round_no,
                action_taken=result.action,
                result_status=status,
                business_completion_declared=bool(delivery_summary.get("completed")),
                completion_mode=result.completion_mode,
                tool_names=[str(event.get("tool_name", "") or "") for event in outcome.tool_events],
                question_count=len(result.question_pack),
                outline_status=workspace.outline_artifact.status,
                outline_section_count=len(workspace.outline_artifact.sections),
                draft_status=workspace.draft_artifact.status,
                draft_word_count=workspace.draft_artifact.word_count,
                dominant_issue=(classification_label or (result.major_risks[0] if result.major_risks else "")),
                open_gaps=list(result.major_risks),
                output_digest=self.preview(result.response_text or result.decision_rationale, limit=180),
                decision_trace_summary=[
                    result.decision_rationale,
                    classification_label,
                    str(delivery_summary.get("reason", "") or ""),
                    str(recovery_summary.get("fallback_source", "") or ""),
                    str(error_summary.get("repair_source", "") or ""),
                ],
                orchestration_summary={
                    "tool_call_count": len(outcome.tool_events),
                    "judge_run_count": len(judge_runs),
                    "structured_output_succeeded": bool(response_summary.get("structured_output_succeeded")),
                    "structured_output_failed": bool(error_summary),
                    "error_classification": classification_label,
                    "repair_source": str(
                        error_summary.get("repair_source", "")
                        or recovery_summary.get("fallback_source", "")
                        or ""
                    ),
                    "repair_steps": list(
                        error_summary.get("repair_steps", [])
                        or recovery_summary.get("repair_steps", [])
                        or []
                    ),
                    "delivery_completed": bool(delivery_summary.get("completed")),
                    "delivery_reason": str(delivery_summary.get("reason", "") or ""),
                    "delivery_auto_delivered": bool(delivery_summary.get("auto_delivered")),
                },
                agent_roles_summary={
                    "coordinator": "CoordinatorAgent",
                    "judge": "JudgeAgent",
                    "judge_loop_enabled": True,
                },
                llm_request_chars=int(request_summary.get("compiled_input_chars", 0) or 0),
                llm_response_chars=int(response_summary.get("last_text_chars", 0) or 0),
                llm_response_preview=str(
                    response_summary.get("raw_output_preview", "")
                    or response_summary.get("last_text_preview", "")
                ),
                debug_files=debug_files,
            )
        )

    def write_final_debug_files(
        self,
        *,
        session_id: str,
        round_no: int,
        payload: dict[str, Any],
    ) -> None:
        write_debug_json(
            session_id=session_id,
            filename="latest_run.json",
            payload=payload,
            app_home=self.app_home,
        )
        write_debug_json(
            session_id=session_id,
            filename=f"round_{round_no:04d}.json",
            payload=payload,
            app_home=self.app_home,
        )

    def workspace_summary(self, workspace: WorkspaceState) -> dict[str, Any]:
        return {
            "outline_status": workspace.outline_artifact.status,
            "draft_status": workspace.draft_artifact.status,
            "draft_word_count": workspace.draft_artifact.word_count,
            "pending_question_count": len(workspace.pending_questions),
            "selected_file_count": len(workspace.material_catalog.selected_files),
            "excerpt_count": len(workspace.retrieved_materials.excerpts),
        }

    def preview(self, text: Any, *, limit: int = 180) -> str:
        normalized = " ".join(str(text or "").strip().split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(limit - 3, 0)].rstrip() + "..."
