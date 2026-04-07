from __future__ import annotations

from pathlib import Path

from skill_system.catalog import SkillCatalog
from .view_models import (
    AskUserViewModel,
    CompletedViewModel,
    FailedViewModel,
    MaxRoundsExceededViewModel,
    RoundContextViewModel,
    RoundReviewViewModel,
    ResultViewModel,
)


class ResultAssembler:
    ACTION_LABELS = {
        "load_skill": "加载写作类型",
        "read_materials": "读取材料",
        "build_outline": "生成提纲",
        "write_draft": "整稿起草",
        "write_section": "补写章节",
        "revise_draft": "整稿修订",
        "polish_language": "语言润色",
        "ask_user": "补充信息",
        "finalize": "生成终稿",
    }

    def __init__(self, skill_catalog: SkillCatalog | None = None) -> None:
        self.skill_catalog = skill_catalog or self._load_skill_catalog()

    def assemble(self, turn_result: object) -> ResultViewModel:
        status = str(getattr(turn_result, "status", "") or "")
        round_context = self._build_round_context(turn_result)
        workspace = getattr(turn_result, "workspace", None)
        pending_questions = list(getattr(workspace, "pending_questions", []) or [])
        final_output_path = str(getattr(turn_result, "final_output_path", "") or "")
        error_message = str(getattr(turn_result, "error_message", "") or "")
        final_text = str(getattr(turn_result, "final_text", "") or "")

        if status == "completed":
            return CompletedViewModel(
                session_id=round_context.session_id,
                rounds_used=round_context.rounds_used,
                action_taken=round_context.action_taken,
                action_label=round_context.action_label,
                primary_skill_display=round_context.primary_skill_display,
                revision_skill_displays=list(round_context.revision_skill_displays),
                review=round_context.review,
                artifact_title=round_context.artifact_title,
                artifact_text=round_context.artifact_text,
                material_actions=list(round_context.material_actions),
                material_names=list(round_context.material_names),
                next_step_hint=round_context.next_step_hint,
                final_text=final_text,
                final_output_path=final_output_path,
                message="终稿已生成。",
            )

        if status == "needs_user_input":
            return AskUserViewModel(
                session_id=round_context.session_id,
                rounds_used=round_context.rounds_used,
                action_taken=round_context.action_taken,
                action_label=round_context.action_label,
                primary_skill_display=round_context.primary_skill_display,
                revision_skill_displays=list(round_context.revision_skill_displays),
                review=round_context.review,
                artifact_title=round_context.artifact_title,
                artifact_text=round_context.artifact_text,
                material_actions=list(round_context.material_actions),
                material_names=list(round_context.material_names),
                next_step_hint=round_context.next_step_hint,
                question_pack=list(getattr(turn_result, "question_pack", []) or []),
                pending_questions=pending_questions,
                message="为继续写作，请补充以下信息：",
            )

        if status == "max_rounds_exceeded":
            return MaxRoundsExceededViewModel(
                session_id=round_context.session_id,
                rounds_used=round_context.rounds_used,
                action_taken=round_context.action_taken,
                action_label=round_context.action_label,
                primary_skill_display=round_context.primary_skill_display,
                revision_skill_displays=list(round_context.revision_skill_displays),
                review=round_context.review,
                artifact_title=round_context.artifact_title,
                artifact_text=round_context.artifact_text,
                material_actions=list(round_context.material_actions),
                material_names=list(round_context.material_names),
                next_step_hint=round_context.next_step_hint,
                error_message=error_message,
                message="本轮暂未完成。",
            )

        return FailedViewModel(
            session_id=round_context.session_id,
            rounds_used=round_context.rounds_used,
            action_taken=round_context.action_taken,
            action_label=round_context.action_label,
            primary_skill_display=round_context.primary_skill_display,
            revision_skill_displays=list(round_context.revision_skill_displays),
            review=round_context.review,
            artifact_title=round_context.artifact_title,
            artifact_text=round_context.artifact_text,
            material_actions=list(round_context.material_actions),
            material_names=list(round_context.material_names),
            next_step_hint=round_context.next_step_hint,
            error_message=error_message,
            llm_raw_output=str(getattr(turn_result, "llm_raw_output", "") or ""),
            message="本轮暂未完成。",
        )

    def render_text(self, view_model: ResultViewModel) -> str:
        if isinstance(view_model, CompletedViewModel):
            lines = self._render_round_context(view_model, include_artifact=False)
            lines.append(view_model.message)
            if view_model.final_output_path:
                lines.append(f"Word文档：{view_model.final_output_path}")
            if view_model.artifact_title and view_model.artifact_text:
                lines.append("")
                lines.append(view_model.artifact_title)
                lines.append(view_model.artifact_text)
            if view_model.next_step_hint:
                lines.append("")
                lines.append("下一步")
                lines.append(view_model.next_step_hint)
            return "\n".join(lines)

        lines = self._render_round_context(view_model)
        if isinstance(view_model, AskUserViewModel):
            lines.append(view_model.message)
            for index, question in enumerate(
                view_model.question_pack or view_model.pending_questions,
                start=1,
            ):
                lines.append(f"{index}. {question.get('question', '')}")
            if view_model.next_step_hint:
                lines.append("")
                lines.append("下一步")
                lines.append(view_model.next_step_hint)
            return "\n".join(lines)

        if isinstance(view_model, MaxRoundsExceededViewModel):
            lines.append(view_model.message)
            lines.append(
                "说明："
                + (view_model.error_message or "请收敛要求，或补充更明确的写作重点后继续。")
            )
            if view_model.next_step_hint:
                lines.append("")
                lines.append("下一步")
                lines.append(view_model.next_step_hint)
            return "\n".join(lines)

        lines.append(view_model.message)
        lines.append(
            "说明："
            + (view_model.error_message or "请稍后重试，或补充更具体的写作要求。")
        )
        if view_model.next_step_hint:
            lines.append("")
            lines.append("下一步")
            lines.append(view_model.next_step_hint)
        return "\n".join(lines)

    def render_round_progress(self, turn_result: object) -> str:
        round_context = self._build_round_context(turn_result)
        lines = self._render_round_context(round_context)
        if round_context.next_step_hint:
            lines.append("")
            lines.append("下一步")
            lines.append(round_context.next_step_hint)
        return "\n".join(lines)

    def _build_round_context(self, turn_result: object) -> RoundContextViewModel:
        session_id = str(getattr(turn_result, "session_id", "") or "")
        rounds_used = int(getattr(turn_result, "rounds_used", 0) or 0)
        workspace = getattr(turn_result, "workspace", None)
        step = getattr(turn_result, "step", None)
        active_skills = getattr(workspace, "active_skills", None)
        primary_skill_id = str(
            getattr(active_skills, "primary_skill_id", "")
            if active_skills is not None
            else ""
        ).strip()
        revision_skill_ids = [
            str(skill_id).strip()
            for skill_id in list(
                getattr(active_skills, "revision_skill_ids", [])
                if active_skills is not None
                else []
            )
            if str(skill_id).strip()
        ]

        action_taken = str(getattr(step, "action_taken", "") or "").strip()
        action_label = self.ACTION_LABELS.get(action_taken, action_taken)
        review = self._build_review_view_model(step)
        artifact_title, artifact_text = self._build_artifact_view(step)
        material_actions, material_names = self._build_material_readout(step, workspace)
        next_step_hint = self._build_next_step_hint(getattr(turn_result, "status", ""), action_taken)

        return RoundContextViewModel(
            session_id=session_id,
            rounds_used=rounds_used,
            action_taken=action_taken,
            action_label=action_label,
            primary_skill_display=self._format_skill_display(primary_skill_id),
            revision_skill_displays=[
                self._format_skill_display(skill_id) for skill_id in revision_skill_ids
            ],
            review=review,
            artifact_title=artifact_title,
            artifact_text=artifact_text,
            material_actions=material_actions,
            material_names=material_names,
            next_step_hint=next_step_hint,
        )

    def _build_review_view_model(self, step: object | None) -> RoundReviewViewModel:
        review = getattr(step, "self_review", None)
        return RoundReviewViewModel(
            content_status_summary=str(getattr(review, "content_status_summary", "") or "").strip(),
            language_status_summary=str(getattr(review, "language_status_summary", "") or "").strip(),
            dominant_issue=str(getattr(review, "dominant_issue", "") or "").strip(),
            open_gaps=[
                str(item).strip()
                for item in list(getattr(review, "open_gaps", []) or [])
                if str(item).strip()
            ],
            notes=[
                str(item).strip()
                for item in list(getattr(review, "notes", []) or [])
                if str(item).strip()
            ],
        )

    def _build_artifact_view(self, step: object | None) -> tuple[str, str]:
        action_taken = str(getattr(step, "action_taken", "") or "").strip()
        payload = getattr(step, "action_payload", None)
        if action_taken == "build_outline":
            sections = list(getattr(payload, "outline_sections", []) or [])
            lines: list[str] = []
            for index, section in enumerate(sections, start=1):
                if isinstance(section, dict):
                    heading = str(section.get("heading", "") or "").strip()
                    if heading:
                        lines.append(f"{index}. {heading}")
            if lines:
                return "本轮提纲", "\n".join(lines)
            outline_text = str(getattr(payload, "outline_text", "") or "").strip()
            if outline_text:
                return "本轮提纲", outline_text
        if action_taken == "write_draft":
            draft_text = str(getattr(payload, "draft_text", "") or "").strip()
            if draft_text:
                return "本轮正文", draft_text
        if action_taken == "write_section":
            section_id = str(getattr(payload, "section_id", "") or "").strip()
            section_text = str(getattr(payload, "section_text", "") or "").strip()
            if section_text:
                return f"本轮章节：{section_id or '未命名章节'}", section_text
        if action_taken == "revise_draft":
            revised_text = str(getattr(payload, "revised_text", "") or "").strip()
            if revised_text:
                return "修订后正文", revised_text
        if action_taken == "polish_language":
            polished_text = str(getattr(payload, "polished_text", "") or "").strip()
            if polished_text:
                return "润色后正文", polished_text
        if action_taken == "finalize":
            final_text = str(getattr(payload, "final_text", "") or "").strip()
            if final_text:
                return "终稿正文", final_text
        if action_taken == "load_skill":
            return "本轮结果", "已加载当前公文写作类型，准备进入材料整理或起草阶段。"
        return "", ""

    def _build_material_readout(
        self,
        step: object | None,
        workspace: object | None,
    ) -> tuple[list[str], list[str]]:
        if str(getattr(step, "action_taken", "") or "").strip() != "read_materials":
            return [], []

        payload = getattr(step, "action_payload", None)
        tool_requests = list(getattr(payload, "tool_requests", []) or [])
        action_lines: list[str] = []
        material_names: list[str] = []

        for index, request in enumerate(tool_requests, start=1):
            tool_name = str(getattr(request, "tool_name", "") or "").strip()
            arguments = dict(getattr(request, "arguments", {}) or {})
            action_lines.append(f"{index}. {self._format_tool_request(tool_name, arguments)}")
            material_names = self._extend_unique(
                material_names,
                self._extract_material_names_from_arguments(arguments),
            )

        workspace_paths = []
        if workspace is not None:
            material_catalog = getattr(workspace, "material_catalog", None)
            retrieved_materials = getattr(workspace, "retrieved_materials", None)
            workspace_paths.extend(list(getattr(material_catalog, "selected_files", []) or []))
            workspace_paths.extend(list(getattr(retrieved_materials, "recent_source_paths", []) or []))
        material_names = self._extend_unique(
            material_names,
            [self._material_label(path) for path in workspace_paths],
        )
        return action_lines, material_names[:8]

    def _render_round_context(
        self,
        view_model: ResultViewModel,
        *,
        include_artifact: bool = True,
    ) -> list[str]:
        lines = [f"第 {view_model.rounds_used} 轮"]
        if view_model.action_taken:
            if view_model.action_label:
                lines.append(f"动作：{view_model.action_taken}（{view_model.action_label}）")
            else:
                lines.append(f"动作：{view_model.action_taken}")
        if view_model.primary_skill_display:
            lines.append(f"主写作 skill：{view_model.primary_skill_display}")
        if view_model.revision_skill_displays:
            lines.append("修订 skill：" + "、".join(view_model.revision_skill_displays))

        if view_model.review.has_content:
            lines.append("")
            lines.append("本轮评价")
            if view_model.review.content_status_summary:
                lines.append("内容评价：" + view_model.review.content_status_summary)
            if view_model.review.language_status_summary:
                lines.append("语言评价：" + view_model.review.language_status_summary)
            if view_model.review.dominant_issue:
                lines.append("当前主要问题：" + view_model.review.dominant_issue)
            if view_model.review.open_gaps:
                lines.append("待补缺口：")
                for index, item in enumerate(view_model.review.open_gaps, start=1):
                    lines.append(f"{index}. {item}")
            if view_model.review.notes:
                lines.append("补充说明：")
                for index, item in enumerate(view_model.review.notes, start=1):
                    lines.append(f"{index}. {item}")

        if view_model.material_actions:
            lines.append("")
            lines.append("本轮读材")
            lines.extend(view_model.material_actions)
        if view_model.material_names:
            lines.append("")
            lines.append("涉及材料")
            for index, item in enumerate(view_model.material_names, start=1):
                lines.append(f"{index}. {item}")

        if include_artifact and view_model.artifact_title and view_model.artifact_text:
            lines.append("")
            lines.append(view_model.artifact_title)
            lines.append(view_model.artifact_text)
        return lines

    def _build_next_step_hint(self, status: str, action_taken: str) -> str:
        if status == "completed":
            return "你可以继续输入修改意见，进一步补写或润色当前稿件。"
        if status == "needs_user_input":
            return "可直接补充材料、说明要求，或指出要保留/调整的结构。"
        if status in {"failed", "max_rounds_exceeded"}:
            return "建议补充更明确的文种、用途、重点要求，或分步继续修改。"
        if action_taken == "read_materials":
            return "系统将基于以上材料继续生成提纲或正文。"
        if action_taken == "load_skill":
            return "可继续读取材料，或直接进入提纲和正文生成。"
        return ""

    def _load_skill_catalog(self) -> SkillCatalog | None:
        try:
            return SkillCatalog.from_loader()
        except Exception:
            return None

    def _format_skill_display(self, skill_id: str) -> str:
        normalized = str(skill_id or "").strip()
        if not normalized:
            return ""
        if self.skill_catalog is None or not self.skill_catalog.has_skill(normalized):
            return normalized
        spec = self.skill_catalog.get_spec(normalized)
        if spec.name and spec.name != normalized:
            return f"{spec.name}（{normalized}）"
        return normalized

    def _format_tool_request(self, tool_name: str, arguments: dict[str, object]) -> str:
        if tool_name == "search":
            query = str(arguments.get("query", "") or "").strip()
            return f"search：查找“{query}”" if query else "search：查找材料"
        if tool_name == "read":
            path = self._material_label(arguments.get("path", ""))
            return f"read：读取 {path}" if path else "read：读取材料"
        if tool_name == "grep":
            pattern = str(arguments.get("pattern", "") or "").strip()
            targets = self._extract_material_names_from_arguments(arguments)
            if pattern and targets:
                return f"grep：在 {'、'.join(targets[:3])} 中检索“{pattern}”"
            if pattern:
                return f"grep：检索“{pattern}”"
            return "grep：检索材料"
        if tool_name == "list":
            targets = self._extract_material_names_from_arguments(arguments)
            if targets:
                return f"list：列出 {'、'.join(targets[:3])}"
            return "list：列出材料目录"
        return f"{tool_name}：处理材料"

    def _extract_material_names_from_arguments(self, arguments: dict[str, object]) -> list[str]:
        names: list[str] = []
        for key in ("path", "root"):
            value = arguments.get(key)
            if value:
                names = self._extend_unique(names, [self._material_label(value)])
        for key in ("paths", "roots"):
            values = arguments.get(key)
            if isinstance(values, list):
                names = self._extend_unique(
                    names,
                    [self._material_label(item) for item in values],
                )
        return [name for name in names if name]

    def _material_label(self, path_value: object) -> str:
        normalized = str(path_value or "").strip().replace("\\", "/")
        if not normalized:
            return ""
        if normalized.endswith("/materials") or normalized == "materials":
            return "materials/"
        return Path(normalized).name or normalized

    def _extend_unique(self, values: list[str], additions: list[str]) -> list[str]:
        ordered = [item for item in values if item]
        for raw_item in additions:
            item = str(raw_item or "").strip()
            if not item or item in ordered:
                continue
            ordered.append(item)
        return ordered
