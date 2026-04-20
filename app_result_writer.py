from __future__ import annotations

from pathlib import Path
from typing import Any

from session_storage.history import save_final_output, save_named_output
from workspace.models import OutlineArtifact, OutlineSection, SelfReview, WorkspaceState

from runtime_models import CoordinatorResult


def apply_result(
    workspace: WorkspaceState,
    *,
    result: CoordinatorResult,
    delivery_decision: Any,
    session_id: str,
    app_home: Path,
) -> Path | None:
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
            sections=[
                OutlineSection(
                    section_id=f"section_{index}",
                    heading=section.heading,
                    goal=section.goal,
                    required_points=list(section.required_points),
                    notes=list(section.notes),
                )
                for index, section in enumerate(result.outline_sections, start=1)
            ],
            global_objective=workspace.task_brief,
            outline_text="\n".join(section.heading for section in result.outline_sections),
            open_gaps=list(result.major_risks),
            status="drafted",
        )
    text = str(delivery_decision.text or (result.final_text if result.action == "finalize" else result.draft_text)).strip()
    if text:
        workspace.draft_artifact.title = result.outline_title or workspace.draft_artifact.title or "公文草稿"
        workspace.draft_artifact.full_text = text
        workspace.draft_artifact.word_count = len(text)
        workspace.draft_artifact.status = "finalized" if bool(delivery_decision.completed) else "drafted"
    _sync_session_outputs(
        session_id=session_id,
        workspace=workspace,
        delivery_decision=delivery_decision,
        app_home=app_home,
    )
    if bool(delivery_decision.should_export) and text:
        return save_final_output(session_id=session_id, content=text, app_home=app_home)
    return None


def status_for_turn(*, result: CoordinatorResult, delivery_decision: Any) -> str:
    if bool(delivery_decision.completed):
        return "completed"
    if bool(delivery_decision.should_export):
        return "delivered_with_risks"
    if result.action == "ask_user":
        return "needs_user_input"
    return "in_progress"


def _sync_session_outputs(
    *,
    session_id: str,
    workspace: WorkspaceState,
    delivery_decision: Any,
    app_home: Path,
) -> None:
    outline_markdown = _render_outline_output(workspace)
    if outline_markdown:
        save_named_output(
            session_id=session_id,
            filename="outline.md",
            content=outline_markdown,
            app_home=app_home,
        )
    draft_text = str(workspace.draft_artifact.full_text or "").strip()
    if draft_text and not bool(delivery_decision.completed):
        save_named_output(
            session_id=session_id,
            filename="draft.md",
            content=draft_text,
            app_home=app_home,
        )


def _render_outline_output(workspace: WorkspaceState) -> str:
    if not workspace.outline_artifact.sections:
        return ""
    title = workspace.outline_artifact.title or workspace.task_brief or "公文提纲"
    lines = [f"# {title}", ""]
    for section in workspace.outline_artifact.sections:
        heading = str(section.heading or "").strip()
        if not heading:
            continue
        lines.append(heading)
        if str(section.goal or "").strip():
            lines.append(f"目标：{section.goal.strip()}")
        if section.required_points:
            lines.append("要点：")
            lines.extend(f"- {item}" for item in section.required_points if str(item or "").strip())
        if section.notes:
            lines.append("备注：")
            lines.extend(f"- {item}" for item in section.notes if str(item or "").strip())
        lines.append("")
    return "\n".join(line for line in lines).strip()


__all__ = ["apply_result", "status_for_turn"]
