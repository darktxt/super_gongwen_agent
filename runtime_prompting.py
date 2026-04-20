from __future__ import annotations

from typing import Any

from workspace.models import WorkspaceState

from runtime_models import CoordinatorResult, JudgeResult
from runtime_judge_flow import _candidate_text


def _summarize_outline(workspace: WorkspaceState) -> str:
    if not workspace.outline_artifact.sections:
        return "暂无提纲。"
    return "\n".join(
        f"{index}. {section.heading}"
        for index, section in enumerate(workspace.outline_artifact.sections[:8], start=1)
    )


def _summarize_draft(workspace: WorkspaceState, limit: int = 1800) -> str:
    text = str(workspace.draft_artifact.full_text or "").strip()
    return text[:limit] if text else "暂无正文草稿。"


def _summarize_materials(workspace: WorkspaceState) -> str:
    parts: list[str] = []
    if workspace.material_catalog.selected_files:
        parts.append("已选材料：" + "；".join(workspace.material_catalog.selected_files[:8]))
    if workspace.retrieved_materials.excerpts:
        parts.append(
            "最近摘录：\n"
            + "\n".join(
                f"- {excerpt.source_path}: {excerpt.preview}"
                for excerpt in workspace.retrieved_materials.excerpts[-3:]
                if str(excerpt.preview or "").strip()
            )
        )
    return "\n\n".join(parts) if parts else "暂无已消费材料。"


def _judge_instructions() -> str:
    return (
        "你是中文公文写作 judge。"
        "你的职责不是改稿，而是严格审阅候选稿是否存在事实边界、正式性、结构完整性、风险披露不足等问题。"
        "如稿件仍有明确、可执行的重要改进点，优先给 needs_improvement，而不是轻易给 pass。"
        "如果稿件已经基本可交付，也可以给 pass，但仍应指出最值得关注的细微问题。"
        "请重点检查是否引用了材料未支持的事实、是否存在表述冒进、是否像正式公文、结构是否完整、是否需要显式披露风险。"
        "最终回答只能是一个合法的 JudgeResult JSON 对象，不得直接输出自然语言评语、Markdown、代码块或 JSON 之外的任何字符。"
        "允许的 score 只有 pass、needs_improvement、fail。"
        "即使你给出 richer payload，也必须保持整个输出是合法 JSON。"
        "JSON 字符串中的双引号必须按 JSON 规则转义。"
    )


def _coordinator_instructions() -> str:
    return (
        "你是中文公文写作 coordinator。"
        "你负责决定本轮应该先提纲、起草、修订、追问还是定稿。"
        "你可以使用材料工具在 materials 边界内取材。"
        "不要编造事实。即使 materials 中暂时无材料，也不能因此停摆；只要用户给定信息足以支撑通用公文场景，就应优先形成保守可交付结果。"
        "材料不足时要主动使用保守表述、占位提示、assumptions 和 major_risks，而不是拒绝写作。"
        "response_text 要写成给用户看的简短中文说明；draft_text 或 final_text 则写完整公文内容。"
        "最终回答只能是一个合法的 CoordinatorResult JSON 对象，不得直接输出公文正文、说明文字、Markdown、代码块或 JSON 之外的任何字符。"
        "action 只允许使用 build_outline、write_draft、revise_draft、ask_user、finalize 这五个值。"
        "当 action = build_outline 时，必须显式输出 outline_follow_up_policy。"
        "如果本轮只应停留在提纲阶段，outline_follow_up_policy 必须为 stop_after_outline。"
        "如果提纲只是中间步骤，且你希望运行时紧接着继续正文起草，outline_follow_up_policy 必须为 auto_continue_to_draft。"
        "如果输入中包含 judge 反馈，你必须认真吸收，并在新的决策或正文中体现处理结果。"
        "若你想继续写正文，必须把正文写入 draft_text 或 final_text 字段，而不是直接输出正文文本。"
        "工具结果本身不是最终回答，任何自然语言前缀、后缀或解释文字都会导致本轮失败。"
        "JSON 字符串中的双引号必须按 JSON 规则转义。"
    )


def _build_judge_feedback_message(judge_result: JudgeResult) -> str:
    issues = "\n".join(f"- {item}" for item in judge_result.issues[:6] if str(item or "").strip()) or "- 暂无细分问题。"
    absorb_points = (
        "\n".join(f"- {item}" for item in judge_result.absorb_points[:6] if str(item or "").strip())
        or "- 请结合整体反馈自行吸收。"
    )
    return (
        "judge 审阅反馈：\n"
        f"- 评分：{judge_result.score}\n"
        f"- 建议动作：{judge_result.suggested_action}\n"
        f"- 总结：{judge_result.review_summary or judge_result.feedback}\n"
        "主要问题：\n"
        f"{issues}\n"
        "需要优先吸收的点：\n"
        f"{absorb_points}\n"
        "请在新的 CoordinatorResult JSON 中体现你如何处理这些反馈；如你选择不采纳某条反馈，也要在 decision_rationale 中说明理由。"
    )


def _build_user_input(
    workspace: WorkspaceState,
    user_input: str,
    *,
    judge_feedback: JudgeResult | None = None,
    prior_candidate: CoordinatorResult | None = None,
) -> str:
    material_state = _summarize_materials(workspace)
    question_text = (
        "\n".join(
            f"- {item.get('question', '')}"
            for item in workspace.pending_questions[-5:]
            if item.get("question")
        )
        if workspace.pending_questions
        else "无"
    )
    parts = [
        f"当前用户输入：\n{user_input.strip()}\n\n"
        f"任务简介：\n{workspace.task_brief or '暂无'}\n\n"
        f"当前提纲：\n{_summarize_outline(workspace)}\n\n"
        f"当前草稿：\n{_summarize_draft(workspace)}\n\n"
        f"当前材料状态：\n{material_state}\n\n"
        "运行要求：即使当前材料为空或不足，也要在用户已给出的有限信息下尽可能形成合理、审慎、可继续修订的公文稿。\n\n"
        f"待追问问题：\n{question_text}\n"
    ]
    if prior_candidate is not None:
        candidate_text = _candidate_text(prior_candidate)
        parts.append(
            "上一版候选结果：\n"
            f"- 动作：{prior_candidate.action}\n"
            f"- 说明：{prior_candidate.response_text or prior_candidate.decision_rationale}\n"
            + (f"- 正文：\n{candidate_text}" if candidate_text else "")
        )
    if judge_feedback is not None:
        parts.append(_build_judge_feedback_message(judge_feedback))
    return "\n\n".join(part for part in parts if str(part or "").strip())


def _build_judge_input(user_input: str, workspace: WorkspaceState, candidate: CoordinatorResult) -> str:
    text = _candidate_text(candidate)
    return (
        f"用户目标：\n{user_input.strip()}\n\n"
        f"材料摘要：\n{_summarize_materials(workspace)}\n\n"
        f"候选动作：{candidate.action}\n"
        f"候选说明：{candidate.response_text or candidate.decision_rationale}\n\n"
        f"候选正文：\n{text}\n\n"
        "请从事实边界、正式公文语体、结构完整性、是否存在表述冒进、是否需要风险披露等维度进行审阅。"
        "最终回答只能是一个合法的 JudgeResult JSON 对象。"
        "不要输出分析过程、说明文字、Markdown 或代码块。"
        "即使需要给出 rich feedback，也必须保持整个输出是合法 JSON。"
        "JSON 字符串中的双引号必须按 JSON 规则转义。"
    )


def _build_outline_to_draft_message(user_input: str) -> str:
    return (
        "上一轮已经形成提纲，且你已在结构化结果中声明需要继续正文起草。\n"
        f"原始用户请求：{user_input.strip()}\n"
        "请严格基于刚才已经给出的提纲和已检索材料，直接继续写作。\n"
        "下一次输出必须优先使用 write_draft、revise_draft 或 finalize。\n"
        "如确实缺少关键事实，才允许输出 ask_user。\n"
        "禁止再次仅返回 build_outline。\n"
        "如果继续写正文，必须把完整正文写入 draft_text 或 final_text。"
    )


__all__ = [
    "_build_judge_feedback_message",
    "_build_judge_input",
    "_build_outline_to_draft_message",
    "_build_user_input",
    "_coordinator_instructions",
    "_judge_instructions",
    "_summarize_draft",
    "_summarize_materials",
    "_summarize_outline",
]
