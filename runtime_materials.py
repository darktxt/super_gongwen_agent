from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable
import zipfile
from xml.etree import ElementTree as ET

from agents import RunContextWrapper, function_tool
from workspace.common import utc_now_iso

from runtime_models import COORDINATOR_OUTPUT_CONTRACT, RuntimeContext
from runtime_observability import _preview

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


def _search_materials_payload(
    materials_root: Path,
    query: str,
    limit: int,
    *,
    read_text_func: Callable[[Path], str] = _read_material_text,
) -> dict[str, Any]:
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        raise ValueError("搜索关键词不能为空。")
    terms = [
        normalized_query,
        *[
            term
            for term in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", normalized_query)
            if len(term) > 1 and term != normalized_query
        ],
    ]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for path in _iter_material_files(materials_root):
        name = path.name.lower()
        score = 8 * int(normalized_query in name)
        preview = ""
        discovered_by = ""
        try:
            text = read_text_func(path)
        except Exception:
            text = ""
        text_lower = text.lower()
        name_hits = sum(1 for term in terms[1:] if term in name)
        score += name_hits * 3
        position = text_lower.find(normalized_query) if text_lower else -1
        if position >= 0:
            score += 6
            preview = _preview(text[max(0, position - 80) : position + 120])
            discovered_by = "search_content"
        else:
            content_terms = [term for term in terms[1:] if term in text_lower]
            score += len(content_terms) * 2
            if content_terms:
                position = text_lower.find(content_terms[0])
                preview = _preview(text[max(0, position - 80) : position + 120])
                discovered_by = "search_content"
        if name_hits or normalized_query in name:
            discovered_by = "search_mixed" if discovered_by else "search_name"
        if score > 0:
            scored.append(
                (
                    score,
                    name,
                    _build_file_item(
                        path,
                        materials_root=materials_root,
                        discovered_by=discovered_by or "search_name",
                        preview=preview,
                    ),
                )
            )
    items = [item for _, _, item in sorted(scored, key=lambda item: (-item[0], item[1]))[: max(limit, 1)]]
    return {
        "query": query,
        "items": items,
        "selected_files": [item["path"] for item in items[:5]],
    }


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
    return _record_tool_event(
        ctx.context,
        tool_name="list",
        summary=f"列出了 {len(files)} 个材料文件。",
        payload=payload,
    )


@function_tool(name_override="search_materials")
def search_materials(
    ctx: RunContextWrapper[RuntimeContext],
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    """在 materials 中按文件名和文本内容搜索相关材料。"""
    payload = _search_materials_payload(ctx.context.materials_root, query, limit)
    return _record_tool_event(
        ctx.context,
        tool_name="search",
        summary=f"搜索到 {len(payload['items'])} 个候选材料。",
        payload=payload,
    )


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
    return _record_tool_event(
        ctx.context,
        tool_name="read",
        summary=f"读取了材料 {resolved.name}。",
        payload=payload,
    )


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
    return _record_tool_event(
        ctx.context,
        tool_name="grep",
        summary=f"定位到 {len(matches)} 条命中。",
        payload=payload,
    )


__all__ = [
    "TEXT_SUFFIXES",
    "SUPPORTED_SUFFIXES",
    "_iter_material_files",
    "_normalize_rel_path",
    "_read_material_text",
    "_record_tool_event",
    "_resolve_material_path",
    "_search_materials_payload",
    "grep_materials",
    "list_materials",
    "read_material",
    "resolve_materials_root",
    "search_materials",
]
