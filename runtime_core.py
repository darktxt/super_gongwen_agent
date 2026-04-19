from __future__ import annotations
from dataclasses import dataclass, field
import os
from pathlib import Path
import json
import re
from typing import Any, Callable, Literal
import zipfile
from xml.etree import ElementTree as ET
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
from agents import Agent, ModelSettings, RunConfig, RunContextWrapper, Runner, function_tool
from agents.exceptions import ModelBehaviorError
from agents.extensions.models.litellm_model import LitellmModel
from pydantic import BaseModel, ConfigDict, Field, model_validator
from workspace.common import utc_now_iso
from workspace.models import WorkspaceState
try:
    from docx import Document
except ImportError:  # pragma: no cover
    Document = None  # type: ignore[assignment]
try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment]
TEXT_SUFFIXES = {".txt", ".md", ".json"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".docx", ".pdf"}
COORDINATOR_OUTPUT_CONTRACT = "下一条 assistant 消息必须只输出一个合法的 CoordinatorResult JSON 对象；禁止输出正文原文、说明文字、Markdown、代码块或 JSON 之外的任何内容。工具返回仅供决策，最终正文必须写入 draft_text 或 final_text。"
REVIEW_OUTPUT_CONTRACT = "下一条 assistant 消息必须只输出一个合法的 ReviewResult JSON 对象；禁止输出自然语言评语、Markdown、代码块或 JSON 之外的任何内容。"
ACTION_ALIASES = {"起草": "write_draft", "草拟": "write_draft", "撰写": "write_draft", "定稿": "finalize", "完成": "finalize", "修订": "revise_draft", "修改": "revise_draft", "提纲": "build_outline", "追问": "ask_user", "补问": "ask_user"}
@dataclass(slots=True)
class RuntimeContext:
    session_id: str
    working_root: Path
    materials_root: Path
    workspace: WorkspaceState
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    on_tool_event: Callable[[dict[str, Any]], None] | None = None
class PendingQuestion(BaseModel):
    question: str
    reason: str = ""
class OutlineSectionResult(BaseModel):
    heading: str
    goal: str = ""
    required_points: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
class ReviewFinding(BaseModel):
    issue: str
    severity: Literal["high", "medium", "low"] = "medium"
    suggestion: str = ""
class ReviewResult(BaseModel):
    verdict: Literal["ready", "revise", "ask_user"] = "revise"
    summary: str
    suggested_action: Literal["write_draft", "revise_draft", "ask_user", "finalize"] = "revise_draft"
    revision_focus: list[str] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    major_risks: list[str] = Field(default_factory=list)
    output_contract: str = REVIEW_OUTPUT_CONTRACT
class ReviewToolInput(BaseModel):
    draft_text: str
    user_goal: str
    materials_summary: str = ""
    current_risks: list[str] = Field(default_factory=list)
class CoordinatorResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: Literal["build_outline", "write_draft", "revise_draft", "ask_user", "finalize"]
    decision_rationale: str
    completion_mode: Literal["continue", "conservative_delivery", "final"] = "continue"
    assumptions: list[str] = Field(default_factory=list)
    major_risks: list[str] = Field(default_factory=list)
    response_text: str = ""
    outline_title: str = ""
    outline_sections: list[OutlineSectionResult] = Field(default_factory=list)
    draft_text: str = ""
    final_text: str = ""
    question_pack: list[PendingQuestion] = Field(default_factory=list)
    review_summary: str = ""
    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict): return value
        data = dict(value); action = str(data.get("action") or data.get("coordinator_decision") or data.get("decision") or "").strip()
        if action: data["action"] = ACTION_ALIASES.get(action, action)
        elif data.get("question_pack"): data["action"] = "ask_user"
        elif data.get("final_text"): data["action"] = "finalize"
        elif data.get("draft_text"): data["action"] = "write_draft"
        elif data.get("outline_sections"): data["action"] = "build_outline"
        data.setdefault("assumptions", data.get("major_assumptions") or [])
        data.setdefault("review_summary", data.get("reviewer_summary") or "")
        data.setdefault("decision_rationale", data.get("decision_rationale") or data.get("response_text") or data.get("next_action") or "模型已按兼容字段返回，运行时已自动归一化。")
        return data
@dataclass(slots=True)
class RuntimeOutcome:
    result: CoordinatorResult; tool_events: list[dict[str, Any]]; raw_output: Any = None
def resolve_materials_root(working_root: Path) -> Path:
    project_root = Path(__file__).resolve().parent
    project_materials = (project_root / "materials").resolve()
    if project_materials.exists():
        return project_materials
    return (working_root / "materials").resolve()
def _normalize_rel_path(path: Path, *, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return path.name
def _resolve_material_path(raw_path: str, *, materials_root: Path, allow_directory: bool = False) -> Path:
    normalized = Path(str(raw_path or "").strip())
    if not str(normalized):
        raise ValueError("材料路径不能为空。")
    candidate = normalized if normalized.is_absolute() else (materials_root / normalized)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(materials_root)
    except ValueError as exc:
        raise ValueError(f"路径越界：{resolved}") from exc
    if not resolved.exists():
        raise FileNotFoundError(f"材料不存在：{resolved}")
    if resolved.is_dir():
        if allow_directory:
            return resolved
        raise ValueError(f"目标不是文件：{resolved}")
    if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的材料格式：{resolved.suffix or '<none>'}")
    return resolved
def _iter_material_files(materials_root: Path, root: str | None = None) -> list[Path]:
    base_root = _resolve_material_path(root, materials_root=materials_root, allow_directory=True) if root else materials_root
    if base_root.is_file():
        return [base_root]
    if not base_root.exists():
        return []
    return [
        path
        for path in sorted(base_root.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
def _read_docx(path: Path) -> str:
    if Document is not None:
        document = Document(path)
        return "\n".join(
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if str(paragraph.text or "").strip()
        )
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    return "\n".join(
        str(node.text or "").strip()
        for node in root.iter()
        if node.tag.endswith("}t") and str(node.text or "").strip()
    )
def _read_pdf(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("未安装 pypdf，无法读取 PDF 材料。")
    reader = PdfReader(str(path))
    return "\n".join(str(page.extract_text() or "").strip() for page in reader.pages).strip()
def _read_material_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text_file(path)
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    raise ValueError(f"不支持的材料格式：{path.suffix or '<none>'}")
def _slice_lines(text: str, start_line: int | None, end_line: int | None) -> tuple[str, int, int]:
    lines = text.splitlines()
    if not lines:
        return "", 0, 0
    start = max((start_line or 1), 1)
    end = max(end_line or len(lines), start)
    selected = "\n".join(lines[start - 1 : end])
    return selected, start, min(end, len(lines))
def _preview(text: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."
def _build_file_item(path: Path, *, materials_root: Path, discovered_by: str, preview: str = "") -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": _normalize_rel_path(path, root=materials_root),
        "title": path.name,
        "kind": path.suffix.lower().lstrip("."),
        "size": stat.st_size,
        "last_modified": utc_now_iso(),
        "discovered_by": discovered_by,
        "preview": preview,
    }
def _record_tool_event(
    ctx: RuntimeContext,
    *,
    tool_name: str,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("output_contract", COORDINATOR_OUTPUT_CONTRACT)
    event = {
        "tool_name": tool_name,
        "request_id": f"{tool_name}_{len(ctx.tool_events) + 1}",
        "status": "ok",
        "summary": summary,
        "payload": payload,
        "created_at": utc_now_iso(),
    }
    ctx.tool_events.append(event)
    if ctx.on_tool_event is not None:
        try:
            ctx.on_tool_event(event)
        except Exception:
            pass
    return event
def _search_materials_payload(materials_root: Path, query: str, limit: int) -> dict[str, Any]:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query: raise ValueError("搜索关键词不能为空。")
    terms = [normalized_query, *[term for term in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", normalized_query) if len(term) > 1 and term != normalized_query]]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for path in _iter_material_files(materials_root):
        name, score, preview, discovered_by, text = path.name.lower(), 8 * int(normalized_query in path.name.lower()), "", "", ""
        try: text = _read_material_text(path)
        except Exception: text = ""
        text_lower, name_hits = text.lower(), sum(1 for term in terms[1:] if term in name)
        score += name_hits * 3; position = text_lower.find(normalized_query) if text_lower else -1
        if position >= 0: score += 6; preview = _preview(text[max(0, position - 80) : position + 120]); discovered_by = "search_content"
        else:
            content_terms = [term for term in terms[1:] if term in text_lower]; score += len(content_terms) * 2
            if content_terms:
                position = text_lower.find(content_terms[0]); preview = _preview(text[max(0, position - 80) : position + 120]); discovered_by = "search_content"
        if name_hits or normalized_query in name: discovered_by = "search_mixed" if discovered_by else "search_name"
        if score > 0: scored.append((score, name, _build_file_item(path, materials_root=materials_root, discovered_by=discovered_by or "search_name", preview=preview)))
    items = [item for _, _, item in sorted(scored, key=lambda item: (-item[0], item[1]))[: max(limit, 1)]]
    return {"query": query, "items": items, "selected_files": [item["path"] for item in items[:5]]}
@function_tool(name_override="list_materials")
def list_materials(
    ctx: RunContextWrapper[RuntimeContext],
    root: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """列出 materials 目录下可用的材料文件。"""
    files = _iter_material_files(ctx.context.materials_root, root=root)[: max(limit, 1)]
    payload = {
        "items": [
            _build_file_item(path, materials_root=ctx.context.materials_root, discovered_by="list")
            for path in files
        ],
        "selected_files": [],
    }
    return _record_tool_event(ctx.context, tool_name="list", summary=f"列出了 {len(files)} 个材料文件。", payload=payload)
@function_tool(name_override="search_materials")
def search_materials(
    ctx: RunContextWrapper[RuntimeContext],
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    """在 materials 中按文件名和文本内容搜索相关材料。"""
    payload = _search_materials_payload(ctx.context.materials_root, query, limit)
    return _record_tool_event(ctx.context, tool_name="search", summary=f"搜索到 {len(payload['items'])} 个候选材料。", payload=payload)
@function_tool(name_override="read_material")
def read_material(
    ctx: RunContextWrapper[RuntimeContext],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """读取 materials 中指定文件的全文或片段。"""
    resolved = _resolve_material_path(path, materials_root=ctx.context.materials_root)
    text = _read_material_text(resolved)
    sliced, resolved_start, resolved_end = _slice_lines(text, start_line, end_line)
    rel_path = _normalize_rel_path(resolved, root=ctx.context.materials_root)
    payload = {
        "path": rel_path,
        "text": sliced[: max(max_chars, 1)],
        "start_line": resolved_start,
        "end_line": resolved_end,
        "preview": _preview(sliced),
        "selected_files": [rel_path],
    }
    return _record_tool_event(ctx.context, tool_name="read", summary=f"读取了材料 {resolved.name}。", payload=payload)
@function_tool(name_override="grep_materials")
def grep_materials(
    ctx: RunContextWrapper[RuntimeContext],
    pattern: str,
    limit: int = 30,
) -> dict[str, Any]:
    """在文本材料中按关键词定位命中文本行。"""
    normalized = str(pattern or "").strip().lower()
    if not normalized:
        raise ValueError("grep 关键词不能为空。")
    matches: list[dict[str, Any]] = []
    for path in _iter_material_files(ctx.context.materials_root):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        for line_no, line in enumerate(_read_material_text(path).splitlines(), start=1):
            if normalized in line.lower():
                matches.append(
                    {
                        "path": _normalize_rel_path(path, root=ctx.context.materials_root),
                        "line_no": line_no,
                        "line_text": line.strip(),
                    }
                )
                if len(matches) >= max(limit, 1):
                    break
        if len(matches) >= max(limit, 1):
            break
    payload = {
        "pattern": pattern,
        "matches": matches,
        "selected_files": list({match["path"] for match in matches}),
    }
    return _record_tool_event(ctx.context, tool_name="grep", summary=f"定位到 {len(matches)} 条命中。", payload=payload)
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
def _build_review_input(params: ReviewToolInput) -> str:
    parts = [
        f"用户目标：{params.user_goal}",
        f"材料摘要：{params.materials_summary or '暂无'}",
        "当前草稿：",
        params.draft_text,
    ]
    if params.current_risks:
        parts.append("已知风险：" + "；".join(params.current_risks))
    return "\n\n".join(parts)
def _review_instructions() -> str:
    return (
        "你是中文公文写作 review specialist。"
        "请审阅当前草稿是否适合直接交付、是否需要修订、是否必须追问用户。"
        "重点检查：结构完整性、正式公文语体、事实边界、是否存在无依据断言、是否需要风险披露。"
        "即使材料库为空，只要用户目标足以支撑通用稿，也应允许保守交付，并明确主要风险。"
        "最终回答只能是一个合法的 ReviewResult JSON 对象，不得直接输出自然语言评语、Markdown 或代码块。"
    )
def _coordinator_instructions() -> str:
    return (
        "你是中文公文写作 coordinator。"
        "你负责决定本轮应该先提纲、起草、修订、追问还是定稿。"
        "你可以使用材料工具在 materials 边界内取材，也可以调用 review_draft 审稿工具。"
        "当已有较完整草稿且准备 revise 或 finalize 时，优先先调用 review_draft。"
        "不要编造事实。即使 materials 中暂无材料，也不能因此停摆；只要用户给定信息足以支撑通用公文场景，就应优先形成保守可交付结果。"
        "材料不足时要主动使用保守表述、占位提示、assumptions 和 major_risks，而不是拒绝写作。"
        "response_text 要写成给用户看的简短中文说明；draft_text 或 final_text 则写完整公文内容。"
        "最终回答只能是一个合法的 CoordinatorResult JSON 对象，不得直接输出公文正文、说明文字、Markdown 或代码块。"
        "每次工具返回后都必须继续消化工具结果，再回到该 JSON 协议收口；工具结果本身不是最终回答。"
    )
def _build_user_input(workspace: WorkspaceState, user_input: str) -> str:
    material_state = _summarize_materials(workspace)
    question_text = (
        "\n".join(f"- {item.get('question', '')}" for item in workspace.pending_questions[-5:] if item.get("question"))
        if workspace.pending_questions
        else "无"
    )
    return (
        f"当前用户输入：\n{user_input.strip()}\n\n"
        f"任务简介：\n{workspace.task_brief or '暂无'}\n\n"
        f"当前提纲：\n{_summarize_outline(workspace)}\n\n"
        f"当前草稿：\n{_summarize_draft(workspace)}\n\n"
        f"当前材料状态：\n{material_state}\n\n"
        "运行要求：即使当前材料为空或不足，也要在用户已给出的有限信息下尽可能形成合理、审慎、可继续修订的公文稿。\n\n"
        f"待追问问题：\n{question_text}\n"
    )
def _extract_json_candidates(text: str) -> list[str]:
    stripped = str(text or "").strip()
    candidates: list[str] = [stripped] if stripped else []
    candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE))
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(stripped[start : index + 1])
                start = None
    unique: list[str] = []
    for candidate in candidates:
        normalized = candidate.strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique
def _looks_like_question_request(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    markers = ("请补充", "请提供", "还需要", "需补充", "请明确", "请说明")
    return any(marker in normalized for marker in markers)
def _fallback_result_from_text(text: str) -> CoordinatorResult:
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("模型返回为空，无法兜底生成结果。")
    if _looks_like_question_request(normalized) and len(normalized) <= 240:
        return CoordinatorResult(
            action="ask_user",
            decision_rationale="模型未返回结构化结果，已按追问文本兜底消费。",
            completion_mode="continue",
            response_text=normalized,
            question_pack=[PendingQuestion(question=normalized)],
        )
    return CoordinatorResult(
        action="finalize",
        decision_rationale="模型未返回结构化结果，已按保守交付稿兜底消费。",
        completion_mode="conservative_delivery",
        response_text="已在材料有限的情况下先形成一版保守稿，可继续补充事实后再修订。",
        final_text=normalized,
        assumptions=["当前材料库可能为空或不足，本稿依据用户已提供信息作保守生成。"],
        major_risks=["文中涉及的事实、数据、地区或时间信息可能仍需按实际情况补齐核对。"],
    )
def _coerce_coordinator_result(output: Any) -> CoordinatorResult:
    if isinstance(output, CoordinatorResult):
        return output
    if isinstance(output, BaseModel):
        return CoordinatorResult.model_validate(output.model_dump())
    if isinstance(output, dict):
        return CoordinatorResult.model_validate(output)
    if isinstance(output, str):
        for candidate in _extract_json_candidates(output):
            try:
                loaded = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                try:
                    return CoordinatorResult.model_validate(loaded)
                except Exception:
                    continue
        return _fallback_result_from_text(output)
    return CoordinatorResult.model_validate(output)
def _extract_text_from_output_item(item: Any) -> str:
    if isinstance(item, dict):
        return "" if item.get("type") != "message" else "".join(str(part.get("text", "")) for part in list(item.get("content", []) or []) if isinstance(part, dict) and part.get("type") == "output_text").strip()
    return "" if getattr(item, "type", None) != "message" else "".join(str(getattr(part, "text", "")) for part in list(getattr(item, "content", []) or []) if getattr(part, "type", None) == "output_text").strip()
def _extract_last_response_text(raw_responses: list[Any]) -> str:
    for response in reversed(list(raw_responses or [])):
        for item in reversed(list(getattr(response, "output", []) or [])):
            text = _extract_text_from_output_item(item)
            if text:
                return text
    return ""
def _extract_last_run_data_text(run_data: Any) -> str:
    text = _extract_last_response_text(getattr(run_data, "raw_responses", []))
    if text:
        return text
    for item in reversed(list(getattr(run_data, "new_items", []) or [])):
        text = _extract_text_from_output_item(getattr(item, "raw_item", None))
        if text:
            return text
    return ""
def _fallback_result_from_model_error(ctx: RuntimeContext, *, last_text: str = "") -> CoordinatorResult:
    result = _coerce_coordinator_result(last_text) if last_text.strip() else _fallback_result_from_max_turns(ctx)
    result.response_text = result.response_text or "已按最后可解析内容保守收口，并保留中间态。"
    if "结构化输出失败" not in result.response_text:
        result.response_text = "本轮结构化输出失败，" + result.response_text
    if "模型未按 CoordinatorResult JSON 输出" not in result.major_risks:
        result.major_risks.append("模型未按 CoordinatorResult JSON 输出，当前结果由运行时兜底恢复。")
    return result
def _fallback_result_from_max_turns(ctx: RuntimeContext, *, last_text: str = "") -> CoordinatorResult:
    if last_text.strip():
        result = _coerce_coordinator_result(last_text)
        result.response_text = result.response_text or "已按最后可解析内容保守收口，并保留中间态。"
        if "最大回合数" not in result.response_text:
            result.response_text = "本轮已达到最大回合数，" + result.response_text
        if "模型在限制回合内未完成收口" not in result.major_risks:
            result.major_risks.append("模型在限制回合内未完成收口，当前结果可能仍需下一轮补充或校订。")
        return result
    draft_text = str(ctx.workspace.draft_artifact.full_text or "").strip()
    if draft_text:
        return CoordinatorResult(action="revise_draft", decision_rationale="已达到最大回合数，先保留当前草稿并结束本轮空转。", completion_mode="continue", response_text="本轮已达到最大回合数，已保留当前草稿和取材中间态，可继续补充要求后再修订。", draft_text=draft_text, major_risks=["模型在限制回合内未完成结构化收口。"])
    return CoordinatorResult(action="ask_user", decision_rationale="已达到最大回合数，当前轮次持续取材但未完成收口，转为保留中间态并请求更明确输入。", completion_mode="continue", response_text="本轮已达到最大回合数，已保留已获取的材料线索与中间态。请进一步缩小写作范围，或直接给出必须保留的要点。", question_pack=[PendingQuestion(question="请补充必须覆盖的核心要点、篇幅和文种要求。")], major_risks=["模型在限制回合内未完成结构化收口。"])
def _review_tool(model: LitellmModel, temperature: float | None):
    review_agent = Agent(name="ReviewSpecialist", instructions=_review_instructions(), model=model, model_settings=ModelSettings(temperature=temperature), output_type=ReviewResult)
    return review_agent.as_tool(tool_name="review_draft", tool_description="审阅当前草稿的正式性、风险和是否适合直接交付。该工具只返回 ReviewResult，不能替代 coordinator 的最终 CoordinatorResult JSON 收口。", parameters=ReviewToolInput, input_builder=_build_review_input)
class LiteLLMAgentsRuntime:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        base_url: str = "",
        temperature: float | None = None,
        enable_tracing: bool = True,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model_name = str(model_name or "").strip()
        self.base_url = str(base_url or "").strip()
        self.temperature = temperature
        self.enable_tracing = bool(enable_tracing)
        if not self.model_name:
            raise RuntimeError("未配置 LITELLM_MODEL，无法运行 LiteLLM Agents Runtime。")
        self.model = LitellmModel(
            model=self.model_name,
            base_url=self.base_url or None,
            api_key=self.api_key or None,
        )
    @classmethod
    def from_config(cls, config: Any) -> "LiteLLMAgentsRuntime":
        return cls(
            api_key=getattr(config, "litellm_api_key", ""),
            model_name=getattr(config, "litellm_model", ""),
            base_url=getattr(config, "litellm_base_url", ""),
            temperature=getattr(config, "litellm_temperature", None),
            enable_tracing=getattr(config, "openai_agents_enable_tracing", True),
        )
    def run_turn(
        self,
        *,
        session_id: str,
        workspace: WorkspaceState,
        user_input: str,
        working_root: str | Path | None = None,
        on_tool_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> RuntimeOutcome:
        resolved_working_root = Path(working_root).resolve() if working_root is not None else Path.cwd()
        runtime_context = RuntimeContext(
            session_id=session_id,
            working_root=resolved_working_root,
            materials_root=resolve_materials_root(resolved_working_root),
            workspace=workspace,
            on_tool_event=on_tool_event,
        )
        coordinator = Agent(
            name="CoordinatorAgent",
            instructions=_coordinator_instructions(),
            model=self.model,
            model_settings=ModelSettings(temperature=self.temperature),
            output_type=CoordinatorResult,
            tool_use_behavior="run_llm_again",
            tools=[
                list_materials,
                search_materials,
                read_material,
                grep_materials,
                _review_tool(self.model, self.temperature),
            ],
        )
        run_config = RunConfig(
            workflow_name="super-gongwen-lite",
            tracing_disabled=not self.enable_tracing,
            trace_metadata={"session_id": session_id, "runtime": "litellm_agents_sdk"},
        )
        def _handle_max_turns(handler_input: Any) -> dict[str, Any]:
            return {
                "final_output": _fallback_result_from_max_turns(
                    runtime_context,
                    last_text=_extract_last_response_text(getattr(handler_input.run_data, "raw_responses", [])),
                )
            }
        try:
            result = Runner.run_sync(coordinator, _build_user_input(workspace, user_input), context=runtime_context, max_turns=8, run_config=run_config, error_handlers={"max_turns": _handle_max_turns})
            raw_output, output = result.final_output, _coerce_coordinator_result(result.final_output)
        except ModelBehaviorError as exc:
            raw_output = _extract_last_run_data_text(exc.run_data) or str(exc)
            output = _fallback_result_from_model_error(runtime_context, last_text=raw_output)
        return RuntimeOutcome(
            result=output,
            tool_events=list(runtime_context.tool_events),
            raw_output=raw_output,
        )
