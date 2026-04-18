from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from agents_runtime.tools import MATERIAL_TOOL_NAMES
from utils.serialization import JsonDataclassMixin
from workspace.snapshot import WorkspaceSnapshot

from .protocol import VALID_ACTIONS


def build_editorial_system_prompt() -> str:
    actions = ", ".join(sorted(VALID_ACTIONS))
    return "\n".join(
        [
            f"你是 super-gongwen-agent 的中文公文写作主控 agent。你的唯一任务是根据上下文选择本轮最合适的 action，并只输出合法 JSON。可选 action：{actions}。",
            "必须基于工作区事实、已读材料、最近自审和历史阻断信息做判断，不要凭空补事实。",
            "如果信息还不足以定稿，就继续补材料、起草或修订；不要为了推进流程而抢跑 finalize。",
            "最终只输出一个 JSON object。",
            "输出顶层只允许 action_taken、action_payload、workspace_patch、self_review。",
            "search、list、read、grep 才是可调用工具；build_outline、write_draft、write_section、revise_draft、polish_language、ask_user、finalize 都不是工具名。",
            "ask_user 不得携带 self_review 或 workspace_patch。",
            "正文、提纲、章节、终稿只允许放进 action_payload，不要写进 workspace_patch。",
            "如果最近自审或历史阻断明确指出 blockers，就优先解决 blockers，不要机械重复上一步。",
        ]
    )


def build_action_playbook() -> str:
    return """Action Policy

- 缺事实、案例、原始口径时，先用工具补材料。
- 结构未成形时可 build_outline。
- 已有结构但正文未成形时可 write_draft 或 write_section。
- 需要实质修订时用 revise_draft；主要是语言与正式度优化时用 polish_language。
- 只有在稿件已经适合正式交付时才 finalize。
- ask_user 只用于关键业务信息缺失且不能自行补齐的情况。

[公文质量]
- 保持正式、准确、克制的中文公文表达。
- 优先写清事实依据、问题回应、措施抓手和责任要求。
"""


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
    attached_context_blocks: list[ContextBlock] = field(default_factory=list)
    token_budget_report: TokenBudgetReport = field(default_factory=lambda: TokenBudgetReport(char_budget=120000))


class ContextCompiler:
    def __init__(self, *, char_budget: int = 120000, block_char_limit: int = 2200) -> None:
        self.char_budget = char_budget
        self.block_char_limit = block_char_limit

    def build(self, snapshot: WorkspaceSnapshot) -> CompiledBrainContext:
        system_prompt = build_editorial_system_prompt()
        user_prompt = self._build_user_prompt(snapshot)
        action_playbook = build_action_playbook()
        budget = TokenBudgetReport(char_budget=self.char_budget, char_used=len(system_prompt) + len(user_prompt) + len(action_playbook))
        return CompiledBrainContext(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            action_playbook_block=self._block("Action Playbook", action_playbook, budget, prefer_full=True),
            attached_context_blocks=[
                self._json_block("User Thread", {
                    "root_task_brief": snapshot.task_brief,
                    "current_user_input": snapshot.latest_user_message or snapshot.task_brief,
                    "recent_user_messages": snapshot.recent_user_messages[-6:],
                }, budget),
                self._json_block("Decision Snapshot", {
                    "has_outline": bool(snapshot.current_outline.outline_text or snapshot.current_outline.sections),
                    "has_full_draft": bool(snapshot.current_draft.full_text),
                    "has_section_draft": bool(snapshot.current_draft.section_map),
                    "retrieved_excerpt_count": len(snapshot.retrieved_materials.excerpts),
                    "evidence_counts": {
                        "facts": len(snapshot.evidence_board.facts),
                        "data_points": len(snapshot.evidence_board.data_points),
                        "cases": len(snapshot.evidence_board.cases),
                        "measure_handles": len(snapshot.evidence_board.measure_handles),
                        "gaps": len(snapshot.evidence_board.gaps),
                    },
                    "must_follow_checks": {
                        "has_constraints": bool(snapshot.directive_ledger.must_follow),
                        "draft_word_count": int(snapshot.current_draft.word_count or 0),
                        "placeholder_count": str(snapshot.current_draft.full_text or "").count("XX"),
                        "open_gap_count": len(snapshot.current_self_review.open_gaps),
                        "dominant_issue": snapshot.current_self_review.dominant_issue,
                    },
                }, budget),
                self._json_block("Writing Brief", {
                    "latest_user_goal": snapshot.latest_user_message or snapshot.task_brief,
                    "current_artifacts": {
                        "outline_status": snapshot.current_outline.status,
                        "draft_status": snapshot.current_draft.status,
                        "draft_word_count": snapshot.current_draft.word_count,
                        "has_outline_text": bool(snapshot.current_outline.outline_text),
                        "has_full_draft": bool(snapshot.current_draft.full_text),
                    },
                    "must_follow_top": snapshot.directive_ledger.must_follow[:8],
                    "must_preserve_top": snapshot.directive_ledger.must_preserve[:6],
                    "style_preferences_top": snapshot.directive_ledger.preferences[:5],
                    "blocked_by": self._blockers(snapshot),
                }, budget),
                self._json_block("Directive Ledger", snapshot.directive_ledger.to_dict(), budget),
                self._json_block("Material Catalog", {
                    "selected_files": snapshot.material_catalog.selected_files,
                    "allowed_roots": snapshot.material_catalog.allowed_roots,
                    "recent_search_history": snapshot.material_catalog.search_history[-5:],
                    "items_preview": [item.to_dict() for item in snapshot.material_catalog.items[:8]],
                }, budget),
                self._json_block("Retrieved Materials", {
                    "recent_queries": snapshot.retrieved_materials.recent_queries[-5:],
                    "recent_source_paths": snapshot.retrieved_materials.recent_source_paths[-8:],
                    "recent_calls": snapshot.retrieved_materials.recent_calls[-3:],
                    "recent_excerpts": [item.to_dict() for item in snapshot.retrieved_materials.excerpts[-6:]],
                }, budget, prefer_full=True),
                self._json_block("Evidence Snapshot", {
                    "facts": snapshot.evidence_board.facts,
                    "data_points": snapshot.evidence_board.data_points,
                    "cases": snapshot.evidence_board.cases,
                    "problem_list": snapshot.evidence_board.problem_list,
                    "measure_handles": snapshot.evidence_board.measure_handles,
                    "usable_phrases": snapshot.evidence_board.usable_phrases,
                    "gaps": snapshot.evidence_board.gaps,
                }, budget, prefer_full=True),
                self._json_block("Quality Signals", {
                    "workflow_state": snapshot.workflow_state.to_dict(),
                    "quality_backlog": snapshot.quality_backlog.to_dict(),
                    "finalization_blockers": snapshot.finalization_blockers,
                    "latest_reviews": snapshot.quality_review_snapshots[-3:],
                }, budget),
                self._json_block("Current Draft and Outline", {
                    "title": snapshot.current_outline.title or snapshot.current_draft.title,
                    "global_objective": snapshot.current_outline.global_objective,
                    "outline_sections": [item.to_dict() for item in snapshot.current_outline.sections],
                    "outline_open_gaps": snapshot.current_outline.open_gaps,
                    "draft_word_count": snapshot.current_draft.word_count,
                    "draft_assembly_mode": snapshot.current_draft.assembly_mode,
                    "draft_section_ids": list(snapshot.current_draft.section_map.keys()),
                    "current_main_text": self._current_main_text(snapshot),
                }, budget, prefer_full=True),
                self._json_block("Current Self Review", snapshot.current_self_review.to_dict(), budget),
                self._json_block("Recent Revision History", [item.to_dict() for item in snapshot.revision_history[-6:]], budget),
                self._json_block("Recent Brain Trace", snapshot.recent_brain_trace[-4:], budget),
                self._json_block("Available Tools", {
                    "available_material_tools": list(MATERIAL_TOOL_NAMES),
                    "tools": [dict(tool) for tool in snapshot.available_tools if isinstance(tool, dict)],
                }, budget),
            ],
            token_budget_report=budget,
        )

    def _build_user_prompt(self, snapshot: WorkspaceSnapshot) -> str:
        latest = snapshot.latest_user_message or snapshot.task_brief or "当前无用户输入。"
        return "\n".join(
            [
                "当前用户输入：",
                latest,
                "",
                "请基于全部上下文选择当前最合适的一个 action，并输出合法的 BrainStepResult JSON。",
                "优先关注：当前稿件事实是否充分、must_follow 是否已覆盖、最近自审或历史阻断是否存在 blockers。",
            ]
        )

    def _blockers(self, snapshot: WorkspaceSnapshot) -> list[str]:
        blockers = [str(item).strip() for item in list(snapshot.finalization_blockers or []) if str(item).strip()]
        if blockers:
            return blockers[:5]
        return [str(item.description).strip() for item in snapshot.quality_backlog.items[:5] if str(item.description).strip()]

    def _current_main_text(self, snapshot: WorkspaceSnapshot) -> str:
        text = str(snapshot.current_draft.full_text or "").strip()
        if text:
            return self._truncate_center(text, self.block_char_limit * 2)
        outline = str(snapshot.current_outline.outline_text or "").strip()
        if outline:
            return self._truncate_center(outline, self.block_char_limit * 2)
        return ""

    def _json_block(self, title: str, payload: Any, budget: TokenBudgetReport, *, prefer_full: bool = False) -> ContextBlock:
        return self._block(title, json.dumps(self._normalize(payload), ensure_ascii=False, indent=2), budget, prefer_full=prefer_full)

    def _block(self, title: str, content: str, budget: TokenBudgetReport, *, prefer_full: bool = False) -> ContextBlock:
        limit = self.char_budget if prefer_full else self.block_char_limit
        remaining = max(budget.char_budget - budget.char_used - len(title), 0)
        effective = min(limit, remaining) if remaining else 0
        truncated = False
        rendered = content.strip()
        if rendered and effective <= 0:
            rendered = "[omitted due to budget]"
            truncated = True
        elif effective and len(rendered) > effective:
            rendered = self._truncate_text(rendered, effective)
            truncated = True
        budget.char_used = min(budget.char_budget, budget.char_used + len(title) + len(rendered))
        if truncated:
            budget.truncated_block_titles.append(title)
        return ContextBlock(title=title, content=rendered, truncated=truncated)

    def _normalize(self, value: Any) -> Any:
        if hasattr(value, "to_dict"):
            return self._normalize(value.to_dict())
        if isinstance(value, dict):
            return {str(key): self._normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._normalize(item) for item in value]
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
        head = int((limit - len(marker)) * 0.65)
        tail = max(limit - len(marker) - head, 0)
        return normalized[:head].rstrip() + marker + normalized[-tail:].lstrip()
