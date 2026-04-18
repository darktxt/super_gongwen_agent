from __future__ import annotations

from agents_runtime.protocol import BrainStepResult
from workspace.models import QualityBacklog, WorkflowState, WorkspaceState

from .models import ACTION_TO_PHASE, WorkflowPhase


class WorkflowCoordinator:
    def initialize_for_user_message(self, workspace: WorkspaceState) -> None:
        if not isinstance(workspace.workflow_state, WorkflowState):
            workflow_payload = (
                workspace.workflow_state.to_dict()
                if hasattr(workspace.workflow_state, "to_dict")
                else workspace.workflow_state
                if isinstance(workspace.workflow_state, dict)
                else {}
            )
            workspace.workflow_state = WorkflowState.from_dict(workflow_payload)
        if not isinstance(workspace.quality_backlog, QualityBacklog):
            backlog_payload = (
                workspace.quality_backlog.to_dict()
                if hasattr(workspace.quality_backlog, "to_dict")
                else workspace.quality_backlog
                if isinstance(workspace.quality_backlog, dict)
                else {}
            )
            workspace.quality_backlog = QualityBacklog.from_dict(backlog_payload)
        if not workspace.workflow_state.phase_history:
            workspace.workflow_state.phase_history.append("intake")
        self._set_phase(
            workspace,
            "intake",
            reason="user_message_ingested",
            next_phase_hint="",
        )

    def mark_quality_review_cycle(self, workspace: WorkspaceState) -> None:
        workspace.workflow_state.quality_review_cycles = int(
            workspace.workflow_state.quality_review_cycles or 0
        ) + 1

    def transition_after_step(
        self,
        workspace: WorkspaceState,
        step: BrainStepResult,
    ) -> None:
        current_phase = self.phase_for_action(step.action_taken)
        self._set_phase(
            workspace,
            current_phase,
            reason=f"action:{step.action_taken}",
            next_phase_hint="",
        )

        if step.action_taken in {"revise_draft", "polish_language"}:
            workspace.workflow_state.revision_cycles = int(
                workspace.workflow_state.revision_cycles or 0
            ) + 1

        if step.ask_user:
            self._set_phase(
                workspace,
                "ask_user",
                reason="awaiting_user_input",
                next_phase_hint="",
            )
            return

        if step.export_requested:
            self._set_phase(
                workspace,
                "completed",
                reason="finalized",
                next_phase_hint="",
            )
            return

    def phase_for_action(self, action_taken: str) -> WorkflowPhase:
        return ACTION_TO_PHASE.get(str(action_taken or "").strip(), "intake")

    def _set_phase(
        self,
        workspace: WorkspaceState,
        phase: WorkflowPhase,
        *,
        reason: str,
        next_phase_hint: str = "",
    ) -> None:
        workflow_state = workspace.workflow_state
        normalized_phase = str(phase or "").strip() or "intake"
        if workflow_state.current_phase and workflow_state.current_phase != normalized_phase:
            workflow_state.last_completed_phase = workflow_state.current_phase
        workflow_state.current_phase = normalized_phase
        workflow_state.last_transition = str(reason or "").strip()
        workflow_state.next_phase_hint = str(next_phase_hint or "").strip()
        if not workflow_state.phase_history or workflow_state.phase_history[-1] != normalized_phase:
            workflow_state.phase_history.append(normalized_phase)
        workflow_state.phase_history = workflow_state.phase_history[-16:]
