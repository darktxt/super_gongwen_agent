from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any
import json

from tool_runtime.registry import MATERIAL_TOOL_NAMES
from utils.serialization import JsonDataclassMixin
from workspace.snapshot import WorkspaceSnapshot

from .prompt_blueprint_runtime import build_action_playbook, build_editorial_system_prompt


@dataclass(slots=True)
class ContextBlock(JsonDataclassMixin):
    title: str
    content: str
    truncated: bool = False


@dataclass(slots=True)
class TokenBudgetReport(JsonDataclassMixin):
    char_budget: int
    char_used: int = 0
    truncated_block_titles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompiledBrainContext(JsonDataclassMixin):
    system_prompt: str
    user_prompt: str
    action_playbook_block: ContextBlock
    skill_listing_block: ContextBlock
    active_skill_blocks: list[ContextBlock] = field(default_factory=list)
    attached_context_blocks: list[ContextBlock] = field(default_factory=list)
    token_budget_report: TokenBudgetReport = field(
        default_factory=lambda: TokenBudgetReport(char_budget=120000)
    )


TOOL_PURPOSES = {
    "grep": "按关键词在 materials 材料中精确定位相关内容。",
    "list": "查看 materials 目录下的文件清单。",
    "read": "读取 materials 中的指定文件或指定片段。",
    "search": "在 materials 中按主题搜索相关材料。",
}

TOOL_DEFAULT_ARGUMENTS = {
    "grep": {"roots": ["materials"]},
    "list": {"roots": ["materials"]},
    "search": {"roots": ["materials"]},
}

TOOL_RECIPES = {
    "read_materials": [
        "先 search 再 read，适合按主题找参考材料。",
        "先 list 再 read，适合先看材料清单再挑文件。",
        "先 search 或 list，必要时 grep，再 read 关键片段。",
        "如果 search 返回 0 但 list 已发现候选文件，优先直接 read 这些文件，不要继续重复空搜索。",
    ]
}

TOOL_ARGUMENT_SCHEMAS = {
    "grep": {
        "pattern": "string",
        "paths": ["string?"],
        "roots": ["string?"],
        "case_sensitive": "bool?",
        "limit": "int?",
    },
    "list": {
        "root": "string?",
        "roots": ["string?"],
        "limit": "int?",
    },
    "read": {
        "path": "string",
        "start_line": "int?",
        "end_line": "int?",
        "max_chars": "int?",
    },
    "search": {
        "query": "string",
        "root": "string?",
        "roots": ["string?"],
        "limit": "int?",
    },
}


class ContextCompiler:
    def __init__(self, *, char_budget: int = 120000, block_char_limit: int = 2200) -> None:
        self.char_budget = char_budget
        self.block_char_limit = block_char_limit

    def build(self, snapshot: WorkspaceSnapshot) -> CompiledBrainContext:
        system_prompt = build_editorial_system_prompt()
        user_prompt = self._build_user_prompt(snapshot)
        action_playbook = build_action_playbook()
        token_budget = TokenBudgetReport(
            char_budget=self.char_budget,
            char_used=len(system_prompt) + len(user_prompt) + len(action_playbook),
        )
        action_playbook_block = self._make_block(
            title="Action Playbook",
            content=action_playbook,
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=min(self.block_char_limit * 4, self.char_budget),
        )
        priority_block_count = max(1, 1 + len(snapshot.active_skills))
        remaining_for_priority = max(self.char_budget - token_budget.char_used, 0)
        priority_block_limit = max(
            1,
            min(
                self.block_char_limit * 2,
                remaining_for_priority // priority_block_count if remaining_for_priority else 1,
            ),
        )
        skill_listing_block = self._make_block(
            title="Available Skills",
            content=self._format_available_skills(snapshot.available_skills),
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=priority_block_limit,
        )
        active_skill_blocks = [
            self._make_block(
                title=f"Active Skill: {skill.get('name') or skill.get('skill_id') or 'unknown'}",
                content=self._format_active_skill(skill),
                token_budget=token_budget,
                prefer_full=True,
                hard_char_limit=priority_block_limit,
            )
            for skill in snapshot.active_skills
        ]
        attached_blocks = [
            self._build_user_thread_block(snapshot, token_budget=token_budget),
            self._build_decision_snapshot_block(snapshot, token_budget=token_budget),
            self._build_directive_ledger_block(snapshot, token_budget=token_budget),
            self._build_material_catalog_block(snapshot, token_budget=token_budget),
            self._build_retrieved_materials_block(snapshot, token_budget=token_budget),
            self._build_evidence_snapshot_block(snapshot, token_budget=token_budget),
            self._build_current_draft_and_outline_block(snapshot, token_budget=token_budget),
            self._build_current_self_review_block(snapshot, token_budget=token_budget),
            self._build_recent_revision_history_block(snapshot, token_budget=token_budget),
            self._build_recent_brain_trace_block(snapshot, token_budget=token_budget),
            self._build_available_tools_block(snapshot, token_budget=token_budget),
        ]
        return CompiledBrainContext(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            action_playbook_block=action_playbook_block,
            skill_listing_block=skill_listing_block,
            active_skill_blocks=active_skill_blocks,
            attached_context_blocks=attached_blocks,
            token_budget_report=token_budget,
        )

    def _build_user_prompt(self, snapshot: WorkspaceSnapshot) -> str:
        latest_message = snapshot.latest_user_message or snapshot.task_brief or "当前无用户输入。"
        return "\n".join(
            [
                "当前用户输入：",
                latest_message,
                "",
                "请基于全部上下文选择当前最合适的一个 action，并输出一个合法的 BrainStepResult JSON。",
                "优先按这个顺序判断：先看是否需要补证据或读材，再看是否需要 skill，然后决定是提纲、正文、分节、修订、润色还是定稿。",
                "如果一般缺口可合理补齐，不要 ask_user；如果硬约束未满足，不要 finalize。",
            ]
        )

    def _format_available_skills(self, skills: list[dict[str, Any]]) -> str:
        if not skills:
            return "当前没有可选 skill。"
        lines: list[str] = []
        for skill in skills:
            card = self._normalize_json_value(skill)
            lines.append("- " + str(card.get("skill_id", "")) + " | " + str(card.get("name", "")))
            if card.get("summary"):
                lines.append("  用途：" + str(card["summary"]))
            if card.get("when_to_use"):
                lines.append("  常见适用：" + "；".join(str(item) for item in card["when_to_use"][:3]))
            if card.get("not_for"):
                lines.append("  不适用：" + "；".join(str(item) for item in card["not_for"][:2]))
        return "\n".join(lines)

    def _format_active_skill(self, skill: dict[str, Any]) -> str:
        normalized = self._normalize_json_value(skill)
        lines = [
            "这份 skill 是当前已加载的写法参考。",
            "skill_id: " + str(normalized.get("skill_id", "")),
            "名称: " + str(normalized.get("name", "")),
        ]
        if normalized.get("summary"):
            lines.append("用途: " + str(normalized["summary"]))
        if normalized.get("writing_goals"):
            lines.append(
                "写作目标: " + "；".join(str(item) for item in normalized.get("writing_goals", [])[:4])
            )
        if normalized.get("when_to_use"):
            lines.append(
                "适用场景: " + "；".join(str(item) for item in normalized.get("when_to_use", [])[:4])
            )
        if normalized.get("review_rubric"):
            lines.append(
                "审看重点: " + "；".join(str(item) for item in normalized.get("review_rubric", [])[:4])
            )
        if normalized.get("not_for"):
            lines.append(
                "不适用: " + "；".join(str(item) for item in normalized.get("not_for", [])[:3])
            )
        return "\n".join(lines)

    def _build_user_thread_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        return self._make_semantic_block(
            title="User Thread",
            purpose="这部分展示本次任务的核心目标、用户当前输入，以及最近几轮用户追加的信息。",
            field_notes=[
                ("root_task_brief", "本次任务的核心目标。"),
                ("current_user_input", "用户当前这一轮刚刚提出的输入。"),
                ("recent_user_messages", "最近几轮用户消息摘录，用来理解补充口径与最新要求。"),
            ],
            current_value={
                "root_task_brief": snapshot.task_brief,
                "current_user_input": snapshot.latest_user_message or snapshot.task_brief,
                "recent_user_messages": snapshot.recent_user_messages,
            },
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=self.block_char_limit * 2,
        )

    def _build_decision_snapshot_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        current_draft = self._normalize_json_value(snapshot.current_draft)
        current_outline = self._normalize_json_value(snapshot.current_outline)
        current_review = self._normalize_json_value(snapshot.current_self_review)
        retrieved = self._normalize_json_value(snapshot.retrieved_materials)
        evidence = self._normalize_json_value(snapshot.evidence_board)
        directive = self._normalize_json_value(snapshot.directive_ledger)

        current_text = str(current_draft.get("full_text", "") or "")
        placeholder_count = current_text.count("XX")
        must_follow = [str(item).strip() for item in list(directive.get("must_follow", []) or []) if str(item).strip()]
        has_length_limit = any("字" in item and ("不超过" in item or "上限" in item) for item in must_follow)
        draft_word_count = int(current_draft.get("word_count", 0) or 0)
        length_limit_satisfied = (not has_length_limit) or draft_word_count <= 1500
        evidence_strength = self._infer_evidence_strength(snapshot)
        doc_type_guess = self._infer_doc_type_guess(snapshot)

        current_value = {
            "doc_type_guess": doc_type_guess,
            "has_outline": bool(
                str(current_outline.get("outline_text", "") or "").strip()
                or list(current_outline.get("sections", []) or [])
            ),
            "has_full_draft": bool(str(current_draft.get("full_text", "") or "").strip()),
            "has_section_draft": bool(dict(current_draft.get("section_map", {}) or {})),
            "retrieved_excerpt_count": len(list(retrieved.get("excerpts", []) or [])),
            "evidence_strength": evidence_strength,
            "must_follow_checks": {
                "has_constraints": bool(must_follow),
                "length_limit_present": has_length_limit,
                "length_limit_satisfied": length_limit_satisfied,
                "placeholder_count": placeholder_count,
                "open_gap_count": len(list(current_review.get("open_gaps", []) or [])),
            },
            "recommended_next_actions": self._recommend_next_actions(snapshot),
        }
        return self._make_semantic_block(
            title="Decision Snapshot",
            purpose="给当前回合做动作选择的最短决策快照。",
            field_notes=[],
            current_value=current_value,
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=self.block_char_limit * 2,
        )

    def _build_directive_ledger_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        return self._make_semantic_block(
            title="Directive Ledger",
            purpose="这部分展示已经沉淀下来的写作要求、保留项、偏好、禁区、已确认结构与尚未解决的问题。",
            field_notes=[
                ("must_follow", "必须遵守的明确要求。"),
                ("must_preserve", "必须保留的内容或表达。"),
                ("preferences", "风格、语气、详略等偏好。"),
                ("rejected_patterns", "应避免的表达、套路或结构。"),
                ("confirmed_structure", "已经确认可沿用的结构安排。"),
                ("open_issues", "目前仍待解决的问题或缺口。"),
            ],
            current_value=self._normalize_json_value(snapshot.directive_ledger),
            token_budget=token_budget,
        )

    def _build_evidence_snapshot_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        evidence = self._normalize_json_value(snapshot.evidence_board)
        evidence.pop("slot_mapping", None)
        return self._make_semantic_block(
            title="Evidence Snapshot",
            purpose="这部分展示当前可直接支撑写作的事实、数据、案例、问题、措施抓手、可复用表达，以及仍待补齐的证据缺口。",
            field_notes=[
                ("facts", "可直接引用的事实。"),
                ("data_points", "可直接落文的数据点。"),
                ("cases", "可直接引用的案例或典型做法。"),
                ("problem_list", "当前需要回应的问题清单。"),
                ("measure_handles", "可展开为措施表述的抓手。"),
                ("usable_phrases", "可直接复用的表达。"),
                ("gaps", "当前仍缺的证据或材料。"),
            ],
            current_value=evidence,
            token_budget=token_budget,
        )

    def _build_retrieved_materials_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        retrieved = self._normalize_json_value(snapshot.retrieved_materials)
        recent_calls = list(retrieved.get("recent_calls", []) or [])
        current_value = {
            "recent_queries": list(retrieved.get("recent_queries", []) or [])[-5:],
            "recent_source_paths": list(retrieved.get("recent_source_paths", []) or [])[-8:],
            "latest_call_summary": recent_calls[-1] if recent_calls else {},
            "recent_excerpts": self._select_retrieved_excerpts(
                list(retrieved.get("excerpts", []) or [])
            ),
        }
        return self._make_semantic_block(
            title="Retrieved Materials",
            purpose="这部分展示最近几轮检索或读取到的材料片段与线索，帮助你判断当前拿到的是线索、定位结果还是正文依据，而不是把所有命中都当成已读正文。",
            field_notes=[
                ("recent_queries", "最近几次与读材相关的查询或关键词。"),
                ("recent_source_paths", "最近检索命中或正文读取涉及的材料来源路径。"),
                ("latest_call_summary", "最近一次 read_materials 的请求结构、结果增量与证据变化摘要。"),
                ("recent_excerpts", "最近较重要的材料片段与线索，保留 tool_name 以标识其来源。"),
            ],
            current_value=current_value,
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=self.block_char_limit * 3,
        )

    def _build_material_catalog_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        catalog = self._normalize_json_value(snapshot.material_catalog)
        search_history = list(catalog.get("search_history", []) or [])
        items_preview = list(catalog.get("items", []) or [])[:8]
        current_value = {
            "selected_files": list(catalog.get("selected_files", []) or []),
            "allowed_roots": list(catalog.get("allowed_roots", []) or []),
            "recent_search_history": search_history[-5:],
            "items_preview": items_preview,
            "search_fallback_hint": self._build_search_fallback_hint(
                search_history=search_history,
                items_preview=items_preview,
            ),
        }
        return self._make_semantic_block(
            title="Material Catalog",
            purpose="这部分展示当前已经发现的材料、已选材料文件以及最近的检索记录，帮助你判断是否还需要继续读材，或可以直接利用已有材料推进。",
            field_notes=[
                ("selected_files", "当前已选中、可重点参考的材料文件。"),
                ("allowed_roots", "当前允许访问的材料根目录。"),
                ("recent_search_history", "最近几次材料检索的简要记录。"),
                ("items_preview", "当前已发现材料的预览列表。"),
                ("search_fallback_hint", "如果 search 连续无结果但目录里已发现文件，这里会明确提示转向 read。"),
            ],
            current_value=current_value,
            token_budget=token_budget,
        )

    def _select_retrieved_excerpts(self, excerpts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        priority_map = {
            "read": 0,
            "grep": 1,
            "search": 2,
        }
        prioritized: list[tuple[int, int, dict[str, Any]]] = []
        for index, raw_excerpt in enumerate(excerpts):
            if not isinstance(raw_excerpt, dict):
                continue
            normalized = dict(raw_excerpt)
            text = str(normalized.get("text", "") or "").strip()
            if not text:
                continue
            tool_name = str(normalized.get("tool_name", "") or "").strip()
            line_start = int(normalized.get("line_start", 0) or 0)
            line_end = int(normalized.get("line_end", 0) or 0)
            normalized["text"] = self._truncate_center(text, 8000)
            normalized["preview"] = str(normalized.get("preview", "") or "").strip() or self._truncate_center(
                text,
                180,
            )
            normalized["line_span"] = (
                f"{line_start}-{line_end}"
                if line_start and line_end and line_end != line_start
                else str(line_start or line_end or "")
            )
            prioritized.append((priority_map.get(tool_name, 9), -index, normalized))

        prioritized.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in prioritized[:6]]

    def _build_current_draft_and_outline_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        outline = self._normalize_json_value(snapshot.current_outline)
        draft = self._normalize_json_value(snapshot.current_draft)
        current_value = {
            "title": outline.get("title") or draft.get("title", ""),
            "global_objective": outline.get("global_objective", ""),
            "outline_sections": outline.get("sections", []),
            "outline_open_gaps": outline.get("open_gaps", []),
            "draft_word_count": draft.get("word_count", 0),
            "draft_assembly_mode": draft.get("assembly_mode", ""),
            "draft_section_ids": self._collect_draft_section_ids(draft),
            "main_text_kind": self._infer_main_text_kind(snapshot),
            "current_main_text": self._select_current_main_text(snapshot),
        }
        return self._make_semantic_block(
            title="Current Draft and Outline",
            purpose="这部分展示当前提纲与正文的整体进展，让你知道稿件已经走到哪一步、当前主文本是什么。",
            field_notes=[
                ("title", "当前稿件标题。"),
                ("global_objective", "当前稿件的总体写作目标。"),
                ("outline_sections", "当前提纲章节与每节任务。"),
                ("outline_open_gaps", "当前提纲层面仍未解决的缺口。"),
                ("draft_word_count", "当前正文大致字数。"),
                ("draft_assembly_mode", "当前正文是按整稿维护，还是按章节稳定组装。"),
                ("draft_section_ids", "当前 section_map 中已经有内容的章节标识。"),
                ("main_text_kind", "当前主文本属于哪一类，如仅提纲、整稿、终稿。"),
                ("current_main_text", "当前最值得继续推进或继续审看的主文本。"),
            ],
            current_value=current_value,
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=self.block_char_limit * 5,
        )

    def _build_current_self_review_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        return self._make_semantic_block(
            title="Current Self Review",
            purpose="这部分展示当前保留的最近一版整体自审，用来判断稿件目前最主要的问题、缺口与完成度。",
            field_notes=[
                ("responded_directives", "当前稿件已经显式响应了哪些明确要求。"),
                ("dominant_issue", "当前最主要的剩余问题。"),
                ("open_gaps", "目前仍未补齐的缺口。"),
                ("content_status_summary", "内容与结构层面的整体状态。"),
                ("language_status_summary", "语言、文风、语势、文采层面的整体状态。"),
                ("notes", "其他必要观察。"),
            ],
            current_value=self._normalize_json_value(snapshot.current_self_review),
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=self.block_char_limit * 2,
        )

    def _build_recent_revision_history_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        history_items: list[dict[str, Any]] = []
        for item in snapshot.revision_history[-6:]:
            normalized = self._normalize_json_value(item)
            history_items.append(
                {
                    "revision_id": normalized.get("revision_id", ""),
                    "action_taken": normalized.get("action_taken", ""),
                    "summary": normalized.get("summary", ""),
                    "focus": list(normalized.get("focus", []) or []),
                    "target_sections": list(normalized.get("target_sections", []) or []),
                    "before_word_count": normalized.get("before_word_count", 0),
                    "after_word_count": normalized.get("after_word_count", 0),
                    "created_at": normalized.get("created_at", ""),
                }
            )
        return self._make_semantic_block(
            title="Recent Revision History",
            purpose="这部分展示最近几轮已经发生过的实质修改记录，用来避免反复改同一层问题，也帮助你判断这一次应继续深改、转入定稿，还是换一个修改方向。",
            field_notes=[
                ("revision_id", "历史修订记录标识。"),
                ("action_taken", "该次记录对应的动作。"),
                ("summary", "那一轮到底改了什么。"),
                ("focus", "那一轮主要修改焦点。"),
                ("target_sections", "那一轮主要涉及哪些章节。"),
                ("before_word_count", "修改前大致字数。"),
                ("after_word_count", "修改后大致字数。"),
                ("created_at", "记录生成时间。"),
            ],
            current_value=history_items,
            token_budget=token_budget,
            hard_char_limit=self.block_char_limit * 2,
        )

    def _build_recent_brain_trace_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        trace_items: list[dict[str, Any]] = []
        for item in snapshot.recent_brain_trace:
            normalized = self._normalize_json_value(item)
            trace_items.append(
                {
                    "round_no": normalized.get("round_no", 0),
                    "action_taken": normalized.get("action_taken", ""),
                    "output_digest": normalized.get("output_digest", ""),
                    "patch_digest": normalized.get("patch_digest", ""),
                    "dominant_issue": normalized.get("dominant_issue", ""),
                    "open_gaps_top3": list(normalized.get("open_gaps", []) or [])[:3],
                    "tool_names": list(normalized.get("tool_names", []) or []),
                }
            )
        return self._make_semantic_block(
            title="Recent Brain Trace",
            purpose="这部分展示最近几轮已经做过什么、补了什么、还卡在哪里，用来避免重复推进或无序跳转。",
            field_notes=[
                ("round_no", "第几轮。"),
                ("action_taken", "该轮选择的动作。"),
                ("output_digest", "该轮主产物的简短摘要。"),
                ("patch_digest", "该轮附带辅助信息的简短摘要。"),
                ("dominant_issue", "该轮结束时最主要的剩余问题。"),
                ("open_gaps_top3", "该轮最重要的几个缺口。"),
                ("tool_names", "该轮实际使用的工具。"),
            ],
            current_value=trace_items,
            token_budget=token_budget,
            hard_char_limit=self.block_char_limit * 2,
        )

    def _build_available_tools_block(
        self,
        snapshot: WorkspaceSnapshot,
        *,
        token_budget: TokenBudgetReport,
    ) -> ContextBlock:
        tool_index = {
            str(self._normalize_json_value(tool).get("name", "") or ""): self._normalize_json_value(tool)
            for tool in snapshot.available_tools
        }
        tools: list[dict[str, Any]] = []
        for name in MATERIAL_TOOL_NAMES:
            normalized = tool_index.get(name)
            if not normalized:
                continue
            tools.append(
                {
                    "name": name,
                    "purpose": TOOL_PURPOSES.get(name, ""),
                    "arguments_schema": TOOL_ARGUMENT_SCHEMAS.get(name, {}),
                    "default_arguments": TOOL_DEFAULT_ARGUMENTS.get(name, {}),
                    "is_read_only": bool(normalized.get("is_read_only", False)),
                    "requires_user_interaction": bool(
                        normalized.get("requires_user_interaction", False)
                    ),
                }
            )
        return self._make_semantic_block(
            title="Available Tools",
            purpose="可调用工具与推荐触发条件。",
            field_notes=[],
            current_value={
                "tool_use_policy": {
                    "read_materials_allowed_tools": list(MATERIAL_TOOL_NAMES),
                    "materials_access_rule": "search、list、grep 默认只查 materials；read 默认读取 materials 内文件。",
                    "prefer_read_materials_when": [
                        "retrieved_materials 为空且 evidence 很弱",
                        "任务依赖事实、案例、典型做法或原始口径",
                        "当前稿件存在大量 XX 或泛化表述",
                    ],
                    "preferred_read_materials_flow": TOOL_RECIPES["read_materials"],
                    "zero_result_fallback": [
                        "search 结果为 0 不等于 materials 内没有材料",
                        "如果 list 已发现候选文件，下一轮优先 read 这些文件",
                        "避免继续使用近似 query 重复空搜索",
                    ],
                    "avoid_ask_user_when": [
                        "缺口属于一般信息且可合理补齐",
                        "工作区已有可搜索材料",
                    ],
                },
                "tools": tools,
            },
            token_budget=token_budget,
            prefer_full=True,
            hard_char_limit=self.block_char_limit * 2,
        )

    def _make_semantic_block(
        self,
        *,
        title: str,
        purpose: str,
        field_notes: list[tuple[str, str]],
        current_value: Any,
        token_budget: TokenBudgetReport,
        prefer_full: bool = False,
        hard_char_limit: int | None = None,
    ) -> ContextBlock:
        lines = ["Summary:", purpose]
        if field_notes:
            lines.extend(["", "Key Fields:"])
            lines.extend(f"- {field_name}: {description}" for field_name, description in field_notes)
        lines.extend(["", "Current:", self._dump_json(current_value)])
        return self._make_block(
            title=title,
            content="\n".join(lines),
            token_budget=token_budget,
            prefer_full=prefer_full,
            hard_char_limit=hard_char_limit,
        )

    def _infer_main_text_kind(self, snapshot: WorkspaceSnapshot) -> str:
        draft = self._normalize_json_value(snapshot.current_draft)
        outline = self._normalize_json_value(snapshot.current_outline)
        if str(draft.get("status", "") or "") == "finalized":
            return "final_text"
        if str(draft.get("full_text", "") or "").strip():
            return "full_draft"
        if dict(draft.get("section_map", {}) or {}):
            return "section_draft"
        if str(outline.get("outline_text", "") or "").strip() or list(outline.get("sections", []) or []):
            return "outline_only"
        return "empty"

    def _collect_draft_section_ids(self, draft: dict[str, Any]) -> list[str]:
        section_map = dict(draft.get("section_map", {}) or {})
        return [
            str(section_id).strip()
            for section_id, text in section_map.items()
            if str(section_id).strip() and str(text or "").strip()
        ]

    def _select_current_main_text(self, snapshot: WorkspaceSnapshot) -> str:
        draft = self._normalize_json_value(snapshot.current_draft)
        outline = self._normalize_json_value(snapshot.current_outline)
        draft_text = str(draft.get("full_text", "") or "").strip()
        if draft_text:
            return self._truncate_center(draft_text, self.block_char_limit * 2)

        outline_text = str(outline.get("outline_text", "") or "").strip()
        if outline_text:
            return self._truncate_center(outline_text, self.block_char_limit * 2)

        sections = list(outline.get("sections", []) or [])
        if not sections:
            return ""

        lines: list[str] = []
        for index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading", "") or "").strip()
            goal = str(section.get("goal", "") or "").strip()
            line = f"{index}. {heading}" if heading else f"{index}."
            if goal:
                line += " | " + goal
            lines.append(line)
        return self._truncate_center("\n".join(lines), self.block_char_limit * 2)

    def _infer_doc_type_guess(self, snapshot: WorkspaceSnapshot) -> str:
        directive = self._normalize_json_value(snapshot.directive_ledger)
        must_follow = [str(item).strip() for item in list(directive.get("must_follow", []) or [])]
        for item in must_follow:
            if "文种定位" in item:
                return item.split("：", 1)[-1].strip()

        task_brief = str(snapshot.task_brief or snapshot.latest_user_message or "").strip()
        if "发言" in task_brief:
            return "发言稿"
        if "讲话" in task_brief:
            return "讲话稿"
        if "报告" in task_brief:
            return "报告"
        return task_brief[:24]

    def _infer_evidence_strength(self, snapshot: WorkspaceSnapshot) -> str:
        retrieved = self._normalize_json_value(snapshot.retrieved_materials)
        evidence = self._normalize_json_value(snapshot.evidence_board)
        excerpts = list(retrieved.get("excerpts", []) or [])
        latest_call = self._latest_retrieval_call_summary(retrieved)
        evidence_counts = {
            "facts": len(list(evidence.get("facts", []) or [])),
            "data_points": len(list(evidence.get("data_points", []) or [])),
            "cases": len(list(evidence.get("cases", []) or [])),
            "measure_handles": len(list(evidence.get("measure_handles", []) or [])),
        }
        evidence_total = (
            evidence_counts["facts"]
            + evidence_counts["data_points"]
            + evidence_counts["cases"]
            + min(evidence_counts["measure_handles"], 2)
        )
        excerpt_count = len(excerpts)
        read_excerpt_count = self._count_excerpt_tools(excerpts, "read")
        grep_excerpt_count = self._count_excerpt_tools(excerpts, "grep")
        search_excerpt_count = self._count_excerpt_tools(excerpts, "search")
        body_read_count = self._latest_call_count(latest_call, "read", metric_key="result_count")
        evidence_delta_total = self._evidence_delta_total(latest_call.get("evidence_delta"))
        readiness = str(latest_call.get("readiness_after_call", "") or "").strip()

        if evidence_total >= 4:
            return "strong"

        if evidence_total >= 2 and (body_read_count > 0 or read_excerpt_count > 0):
            return "strong"

        if evidence_total >= 1:
            return "medium"

        if body_read_count > 0 or read_excerpt_count > 0:
            return "medium"

        if evidence_delta_total > 0 or readiness in {"grounded", "enriched"}:
            return "medium"

        lead_signal = (
            min(search_excerpt_count, 4)
            + min(grep_excerpt_count, 2)
            + min(excerpt_count, 2)
            + min(int(latest_call.get("new_source_paths", 0) or 0), 2)
            + min(int(latest_call.get("selected_files_added", 0) or 0), 2)
        )
        if lead_signal >= 5:
            return "medium"

        return "weak"

    def _latest_retrieval_call_summary(self, retrieved: dict[str, Any]) -> dict[str, Any]:
        recent_calls = list(retrieved.get("recent_calls", []) or [])
        for item in reversed(recent_calls):
            if isinstance(item, dict):
                return item
        return {}

    def _count_excerpt_tools(self, excerpts: list[dict[str, Any]], tool_name: str) -> int:
        return sum(
            1
            for excerpt in excerpts
            if str(excerpt.get("tool_name", "") or "").strip() == tool_name
        )

    def _latest_call_count(
        self,
        latest_call: dict[str, Any],
        tool_name: str,
        *,
        metric_key: str,
    ) -> int:
        result_breakdown = latest_call.get("result_breakdown", {})
        if not isinstance(result_breakdown, dict):
            return 0
        tool_breakdown = result_breakdown.get(tool_name, {})
        if not isinstance(tool_breakdown, dict):
            return 0
        return int(tool_breakdown.get(metric_key, 0) or 0)

    def _evidence_delta_total(self, payload: Any) -> int:
        if not isinstance(payload, dict):
            return 0
        return sum(max(int(value or 0), 0) for value in payload.values())

    def _recommend_next_actions(self, snapshot: WorkspaceSnapshot) -> list[str]:
        draft = self._normalize_json_value(snapshot.current_draft)
        outline = self._normalize_json_value(snapshot.current_outline)
        review = self._normalize_json_value(snapshot.current_self_review)
        directive = self._normalize_json_value(snapshot.directive_ledger)
        retrieved = self._normalize_json_value(snapshot.retrieved_materials)
        evidence_strength = self._infer_evidence_strength(snapshot)
        current_text = str(draft.get("full_text", "") or "")
        must_follow = [str(item).strip() for item in list(directive.get("must_follow", []) or []) if str(item).strip()]

        recommended: list[str] = []
        if (
            evidence_strength == "weak"
            and not list(retrieved.get("excerpts", []) or [])
            and any(keyword in str(snapshot.task_brief or snapshot.latest_user_message or "") for keyword in ("典型", "经验", "汇报", "报告", "发言"))
        ):
            recommended.append("read_materials")

        if not (
            str(outline.get("outline_text", "") or "").strip()
            or list(outline.get("sections", []) or [])
        ):
            recommended.append("build_outline")
        elif not str(draft.get("full_text", "") or "").strip():
            recommended.append("write_draft")
        elif dict(draft.get("section_map", {}) or {}):
            recommended.append("write_section")

        if current_text:
            if any(item for item in list(review.get("open_gaps", []) or [])):
                recommended.append("revise_draft")
            elif "dominant_issue" in review and str(review.get("dominant_issue", "") or "").strip():
                recommended.append("polish_language")

        has_length_limit = any("字" in item and ("不超过" in item or "上限" in item) for item in must_follow)
        if current_text and ((not has_length_limit) or int(draft.get("word_count", 0) or 0) <= 1500):
            recommended.append("finalize")

        deduped: list[str] = []
        for action in recommended:
            if action and action not in deduped:
                deduped.append(action)
        return deduped[:4]

    def _build_search_fallback_hint(
        self,
        *,
        search_history: list[dict[str, Any]],
        items_preview: list[dict[str, Any]],
    ) -> str:
        if not search_history or not items_preview:
            return ""

        zero_result_searches = [
            item
            for item in search_history[-3:]
            if isinstance(item, dict) and int(item.get("result_count", 0) or 0) == 0
        ]
        if not zero_result_searches:
            return ""
        return "最近 search 出现 0 结果，但当前目录已发现候选文件；不要直接判断 materials 无材料，优先改用 read 读取这些候选。"

    def _make_block(
        self,
        title: str,
        content: str,
        *,
        token_budget: TokenBudgetReport,
        prefer_full: bool = False,
        hard_char_limit: int | None = None,
    ) -> ContextBlock:
        limit = (
            hard_char_limit
            if hard_char_limit is not None
            else self.block_char_limit
            if not prefer_full
            else self.char_budget
        )
        truncated = False
        rendered = content.strip()
        remaining = max(token_budget.char_budget - token_budget.char_used - len(title), 0)
        effective_limit = min(limit, remaining) if remaining else 0

        if rendered and effective_limit <= 0:
            rendered = "[omitted due to budget]"
            truncated = True
        elif len(rendered) > effective_limit > 0:
            rendered = self._truncate_text(rendered, effective_limit)
            truncated = True

        if len(rendered) > limit:
            rendered = self._truncate_text(rendered, limit)
            truncated = True

        if truncated:
            token_budget.truncated_block_titles.append(title)
        token_budget.char_used = min(
            token_budget.char_budget,
            token_budget.char_used + len(title) + len(rendered),
        )
        return ContextBlock(title=title, content=rendered, truncated=truncated)

    def _dump_json(self, value: Any) -> str:
        payload = self._normalize_json_value(value)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _normalize_json_value(self, value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return self._normalize_json_value(value.to_dict())
        if is_dataclass(value):
            return {
                field.name: self._normalize_json_value(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, dict):
            return {
                str(key): self._normalize_json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._normalize_json_value(item) for item in value]
        return value

    def _truncate_text(self, text: str, limit: int) -> str:
        suffix = "\n...[truncated]"
        if limit <= len(suffix):
            return suffix[:limit]
        return text[: limit - len(suffix)].rstrip() + suffix

    def _truncate_center(self, text: str, limit: int) -> str:
        normalized = str(text or "").strip()
        if len(normalized) <= limit:
            return normalized
        marker = "\n...[中间省略]...\n"
        if limit <= len(marker) + 20:
            return self._truncate_text(normalized, limit)
        head_limit = int((limit - len(marker)) * 0.65)
        tail_limit = limit - len(marker) - head_limit
        return (
            normalized[:head_limit].rstrip()
            + marker
            + normalized[-tail_limit:].lstrip()
        )
