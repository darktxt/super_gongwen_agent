from __future__ import annotations

from .view_models import (
    AskUserViewModel,
    CompletedViewModel,
    FailedViewModel,
    MaxRoundsExceededViewModel,
    ResultViewModel,
)


class ResultAssembler:
    def assemble(self, turn_result: object) -> ResultViewModel:
        status = str(getattr(turn_result, "status", "") or "")
        session_id = str(getattr(turn_result, "session_id", "") or "")
        rounds_used = int(getattr(turn_result, "rounds_used", 0) or 0)
        workspace = getattr(turn_result, "workspace", None)
        last_action = str(
            getattr(workspace, "session_meta", {}).get("last_action", "")
            if workspace is not None
            else ""
        )
        active_skills = getattr(workspace, "active_skills", None)
        primary_skill_id = str(
            getattr(active_skills, "primary_skill_id", "")
            if active_skills is not None
            else ""
        )
        revision_skill_ids = list(
            getattr(active_skills, "revision_skill_ids", [])
            if active_skills is not None
            else []
        )
        pending_questions = list(getattr(workspace, "pending_questions", []) or [])
        final_output_path = str(getattr(turn_result, "final_output_path", "") or "")
        error_message = str(getattr(turn_result, "error_message", "") or "")
        final_text = str(getattr(turn_result, "final_text", "") or "")
        completed_message = (
            f"\u672c\u8f6e\u5df2\u5b8c\u6210\u5b9a\u7a3f\uff0c\u5171\u8fd0\u884c {rounds_used} \u8f6e\u3002"
            + (
                f" \u6700\u7ec8\u7a3f\u5df2\u4fdd\u5b58\u81f3\uff1a{final_output_path}"
                if final_output_path
                else ""
            )
        )
        ask_user_message = "\u5f53\u524d\u8fd8\u9700\u8981\u7528\u6237\u8865\u5145\u4fe1\u606f\u540e\u624d\u80fd\u7ee7\u7eed\u5199\u4f5c\u3002"
        max_rounds_message = (
            f"\u672c\u8f6e\u5df2\u8fbe\u5230\u6700\u5927\u8f6e\u6570 {rounds_used}\uff0c"
            "\u4e3a\u907f\u514d\u6b7b\u5faa\u73af\u5df2\u505c\u6b62\u3002"
        )
        failed_message = "\u672c\u8f6e\u6267\u884c\u5931\u8d25\uff0c\u8bf7\u6839\u636e\u9519\u8bef\u4fe1\u606f\u4e0e\u6700\u540e\u52a8\u4f5c\u7ee7\u7eed\u6392\u67e5\u3002"

        if status == "completed":
            return CompletedViewModel(
                session_id=session_id,
                rounds_used=rounds_used,
                final_text=final_text,
                final_output_path=final_output_path,
                primary_skill_id=primary_skill_id,
                revision_skill_ids=revision_skill_ids,
                last_action=last_action,
                message=completed_message,
            )

        if status == "needs_user_input":
            return AskUserViewModel(
                session_id=session_id,
                rounds_used=rounds_used,
                question_pack=list(getattr(turn_result, "question_pack", []) or []),
                pending_questions=pending_questions,
                last_action=last_action,
                message=ask_user_message,
            )

        if status == "max_rounds_exceeded":
            return MaxRoundsExceededViewModel(
                session_id=session_id,
                rounds_used=rounds_used,
                error_message=error_message,
                last_action=last_action,
                message=max_rounds_message,
            )

        return FailedViewModel(
            session_id=session_id,
            rounds_used=rounds_used,
            error_message=error_message,
            llm_raw_output=str(getattr(turn_result, "llm_raw_output", "") or ""),
            last_action=last_action,
            message=failed_message,
        )

    def render_text(self, view_model: ResultViewModel) -> str:
        if isinstance(view_model, CompletedViewModel):
            lines = [
                "\u72b6\u6001\uff1a\u5df2\u5b8c\u6210\u5b9a\u7a3f",
                f"\u4f1a\u8bdd\uff1a{view_model.session_id}",
                f"\u8f6e\u6570\uff1a{view_model.rounds_used}",
            ]
            if view_model.primary_skill_id:
                lines.append("\u4e3b\u5199\u4f5c skill\uff1a" + view_model.primary_skill_id)
            if view_model.revision_skill_ids:
                lines.append(
                    "\u4fee\u8ba2 skill\uff1a" + ", ".join(view_model.revision_skill_ids)
                )
            if view_model.final_output_path:
                lines.append(f"\u6587\u4ef6\uff1a{view_model.final_output_path}")
            lines.append("")
            lines.append(view_model.final_text)
            return "\n".join(lines)

        if isinstance(view_model, AskUserViewModel):
            lines = [
                "\u72b6\u6001\uff1a\u9700\u8981\u8865\u5145\u4fe1\u606f",
                f"\u4f1a\u8bdd\uff1a{view_model.session_id}",
                f"\u8f6e\u6570\uff1a{view_model.rounds_used}",
            ]
            for index, question in enumerate(
                view_model.question_pack or view_model.pending_questions,
                start=1,
            ):
                lines.append(f"{index}. {question.get('question', '')}")
            return "\n".join(lines)

        if isinstance(view_model, MaxRoundsExceededViewModel):
            return "\n".join(
                [
                    "\u72b6\u6001\uff1a\u8fbe\u5230\u6700\u5927\u8f6e\u6570",
                    f"\u4f1a\u8bdd\uff1a{view_model.session_id}",
                    f"\u8f6e\u6570\uff1a{view_model.rounds_used}",
                    f"\u6700\u540e\u52a8\u4f5c\uff1a{view_model.last_action}",
                    f"\u8bf4\u660e\uff1a{view_model.error_message or view_model.message}",
                ]
            )

        return "\n".join(
            [
                "\u72b6\u6001\uff1a\u6267\u884c\u5931\u8d25",
                f"\u4f1a\u8bdd\uff1a{view_model.session_id}",
                f"\u8f6e\u6570\uff1a{view_model.rounds_used}",
                f"\u6700\u540e\u52a8\u4f5c\uff1a{view_model.last_action}",
                f"\u9519\u8bef\uff1a{view_model.error_message or view_model.message}",
                "",
                "\u004c\u004c\u004d\u539f\u59cb\u8f93\u51fa\uff1a",
                view_model.llm_raw_output or "[empty]",
            ]
        )
