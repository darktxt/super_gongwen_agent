from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Iterable
from uuid import uuid4

from agents import (
    RunContextWrapper,
    function_tool,
)

from agents_runtime.materials_fs import (
    MATERIALS_DIR_NAME,
    iter_files,
    read_material_text,
    resolve_material_path,
    resolve_material_roots,
    TEXT_MATERIAL_SUFFIXES,
)
from agents_runtime.result_store import maybe_store_tool_payload
from utils.clock import utc_now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POWERSHELL_EXE = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
DEFAULT_SHELL_TIMEOUT_MS = 30000

MATERIAL_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "search",
        "is_read_only": True,
        "is_concurrency_safe": True,
        "requires_user_interaction": False,
        "max_result_chars": 4000,
    },
    {
        "name": "list",
        "is_read_only": True,
        "is_concurrency_safe": True,
        "requires_user_interaction": False,
        "max_result_chars": 4000,
    },
    {
        "name": "read",
        "is_read_only": True,
        "is_concurrency_safe": True,
        "requires_user_interaction": False,
        "max_result_chars": 4000,
    },
    {
        "name": "grep",
        "is_read_only": True,
        "is_concurrency_safe": True,
        "requires_user_interaction": False,
        "max_result_chars": 4000,
    },
)
MATERIAL_TOOL_NAMES = tuple(spec["name"] for spec in MATERIAL_TOOL_SPECS)
_MATERIAL_TOOL_SPEC_INDEX = {spec["name"]: spec for spec in MATERIAL_TOOL_SPECS}


@dataclass(slots=True)
class AgentsToolRuntimeContext:
    working_root: Path
    session_id: str | None = None
    app_home: Path | None = None
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


def list_material_tool_specs() -> list[dict[str, Any]]:
    return [dict(spec) for spec in MATERIAL_TOOL_SPECS]


def _clean_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                continue
            cleaned[key] = normalized
            continue
        if isinstance(value, list):
            normalized_items = []
            for item in value:
                if item is None:
                    continue
                if isinstance(item, str):
                    text = item.strip()
                    if not text:
                        continue
                    normalized_items.append(text)
                    continue
                normalized_items.append(item)
            if not normalized_items:
                continue
            cleaned[key] = normalized_items
            continue
        cleaned[key] = value
    return cleaned


def _merge_root_arguments(
    *,
    root: str | None = None,
    roots: list[str] | None = None,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if root:
        merged["root"] = root
    if roots:
        merged["roots"] = roots
    if paths:
        merged["paths"] = paths
    return merged


def _build_preview(text: str, limit: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _run_subprocess(
    args: list[str],
    *,
    cwd: Path,
    timeout_ms: int,
) -> tuple[str, str, int | None, bool]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=max(timeout_ms, 1) / 1000.0,
            check=False,
        )
        return completed.stdout, completed.stderr, completed.returncode, False
    except subprocess.TimeoutExpired as exc:
        return str(exc.stdout or ""), str(exc.stderr or ""), None, True


def _run_powershell(command: str, *, cwd: Path, timeout_ms: int) -> tuple[str, str, int | None, bool]:
    return _run_subprocess(
        [POWERSHELL_EXE, "-NoProfile", "-Command", command],
        cwd=cwd,
        timeout_ms=timeout_ms,
    )


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def _normalize_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


def _extract_search_terms(normalized_query: str) -> list[str]:
    if not normalized_query:
        return []
    terms: list[str] = []
    for part in normalized_query.split(" "):
        term = part.strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def _is_text_material(path: Path) -> bool:
    return path.suffix.lower() in TEXT_MATERIAL_SUFFIXES


def _build_search_hit(path: Path, *, discovered_by: str, preview: str = "") -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "title": path.name,
        "kind": path.suffix.lower().lstrip(".") or "text",
        "size": stat.st_size,
        "last_modified": str(stat.st_mtime),
        "discovered_by": discovered_by,
        "preview": preview,
    }


def _list_material_paths_via_shell(
    roots: Iterable[str | Path],
    *,
    working_root: Path,
    limit: int,
) -> list[Path]:
    resolved_roots = [Path(root) for root in resolve_material_roots(list(roots), working_root=working_root)]
    if not resolved_roots:
        return []
    if not _rg_available():
        return iter_files(list(roots), working_root=working_root)[:limit]

    discovered: list[Path] = []
    seen: set[Path] = set()
    for root in resolved_roots:
        stdout, _, _, _ = _run_subprocess(
            ["rg", "--files", str(root)],
            cwd=working_root,
            timeout_ms=DEFAULT_SHELL_TIMEOUT_MS,
        )
        for raw_line in stdout.splitlines():
            candidate = Path(raw_line.strip()).expanduser()
            if not candidate.is_absolute():
                candidate = (working_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if (
                candidate.is_file()
                and candidate.suffix.lower() in {".pdf", ".docx", ".md", ".txt", ".json"}
                and candidate not in seen
            ):
                discovered.append(candidate)
                seen.add(candidate)
                if len(discovered) >= limit:
                    return discovered
    return discovered


def _run_rg_json(
    pattern: str,
    targets: list[str | Path],
    *,
    working_root: Path,
    case_sensitive: bool = False,
    fixed_strings: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    resolved_targets: list[Path] = []
    for target in list(targets or []):
        raw = str(target or "").strip()
        if not raw:
            continue
        try:
            resolved_targets.append(resolve_material_path(raw, working_root=working_root))
            continue
        except Exception:
            pass
        for root in resolve_material_roots([raw], working_root=working_root):
            resolved = Path(root)
            if resolved not in resolved_targets:
                resolved_targets.append(resolved)
    if not resolved_targets:
        resolved_targets = [Path(root) for root in resolve_material_roots([MATERIALS_DIR_NAME], working_root=working_root)]

    if not _rg_available():
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern if not fixed_strings else re.escape(pattern), flags=flags)
        fallback_matches: list[dict[str, Any]] = []
        for path in iter_files(resolved_targets, working_root=working_root):
            if not _is_text_material(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    fallback_matches.append(
                        {
                            "path": str(path),
                            "line_no": line_no,
                            "line_text": line.strip(),
                        }
                    )
                    if len(fallback_matches) >= limit:
                        return fallback_matches
        return fallback_matches

    command = ["rg", "--json", "--line-number", "--color", "never"]
    if fixed_strings:
        command.append("-F")
    if not case_sensitive:
        command.append("-i")
    command.append(pattern)
    command.extend(str(target) for target in resolved_targets)
    stdout, _, _, _ = _run_subprocess(
        command,
        cwd=working_root,
        timeout_ms=DEFAULT_SHELL_TIMEOUT_MS,
    )
    matches: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "match":
            continue
        data = payload.get("data", {})
        path_text = str(data.get("path", {}).get("text", "") or "").strip()
        line_text = str(data.get("lines", {}).get("text", "") or "").strip()
        line_no = int(data.get("line_number", 0) or 0)
        if not path_text or not line_text:
            continue
        matches.append(
            {
                "path": path_text,
                "line_no": line_no,
                "line_text": line_text,
            }
        )
        if len(matches) >= limit:
            break
    return matches


def _search_materials_via_shell(
    query: str,
    *,
    roots: list[str],
    working_root: Path,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_query = _normalize_search_query(query)
    if not normalized_query:
        return []

    search_terms = _extract_search_terms(normalized_query)
    files = _list_material_paths_via_shell(roots, working_root=working_root, limit=max(limit * 10, 50))
    hits_by_path: dict[str, dict[str, Any]] = {}
    for path in files:
        name_text = path.name.lower()
        if normalized_query in name_text or any(term in name_text for term in search_terms):
            hits_by_path[str(path)] = _build_search_hit(path, discovered_by="search_name")

    content_matches = _run_rg_json(
        query,
        roots,
        working_root=working_root,
        fixed_strings=True,
        limit=max(limit * 5, 20),
    )
    if not content_matches and len(search_terms) > 1:
        for term in search_terms:
            for match in _run_rg_json(
                term,
                roots,
                working_root=working_root,
                fixed_strings=True,
                limit=max(limit * 3, 10),
            ):
                content_matches.append(match)
                if len(content_matches) >= max(limit * 5, 20):
                    break
            if len(content_matches) >= max(limit * 5, 20):
                break

    for match in content_matches:
        path = Path(str(match.get("path", "") or "")).resolve()
        preview = _build_preview(str(match.get("line_text", "") or ""))
        existing = hits_by_path.get(str(path))
        if existing is None:
            hits_by_path[str(path)] = _build_search_hit(
                path,
                discovered_by="search_content",
                preview=preview,
            )
            continue
        if not existing.get("preview"):
            existing["preview"] = preview

    ranked = list(hits_by_path.values())
    ranked.sort(
        key=lambda item: (
            0 if item.get("discovered_by") == "search_name" else 1,
            str(item.get("title", "")).lower(),
            str(item.get("path", "")).lower(),
        )
    )
    return ranked[:limit]


def _read_material_with_shell(
    target: str | Path,
    *,
    working_root: Path,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    path = resolve_material_path(target, working_root=working_root)
    if not _is_text_material(path):
        text = read_material_text(path)
    else:
        quoted_path = str(path).replace("'", "''")
        if start_line is not None or end_line is not None:
            skip = max((start_line or 1) - 1, 0)
            first_count = ""
            if end_line is not None:
                count = max(end_line - skip, 0)
                first_count = f" -First {count}"
            command = (
                f"$lines = Get-Content -LiteralPath '{quoted_path}' -Encoding UTF8; "
                f"$slice = $lines | Select-Object -Skip {skip}{first_count}; "
                "$slice -join \"`n\""
            )
        else:
            command = f"Get-Content -LiteralPath '{quoted_path}' -Encoding UTF8 -Raw"
        stdout, stderr, _, _ = _run_powershell(
            command,
            cwd=working_root,
            timeout_ms=DEFAULT_SHELL_TIMEOUT_MS,
        )
        text = stdout if stdout or not stderr else path.read_text(encoding="utf-8", errors="ignore")

    lines = text.splitlines()
    resolved_start_line = max((start_line or 1), 1) if lines else 0
    resolved_end_line = end_line if end_line is not None else len(lines)
    if lines:
        resolved_end_line = min(max(resolved_end_line, resolved_start_line), len(lines))
    else:
        resolved_end_line = 0
    if start_line is not None or end_line is not None:
        start_index = max((start_line or 1) - 1, 0)
        end_index = resolved_end_line if resolved_end_line else len(lines)
        text = "\n".join(lines[start_index:end_index])
    if max_chars is not None:
        text = text[:max_chars]
    return {
        "path": str(path),
        "text": text,
        "start_line": resolved_start_line,
        "end_line": resolved_end_line,
        "preview": _build_preview(text),
        "selected_files": [str(path)],
    }


def _new_request_id() -> str:
    return f"tool_{uuid4().hex[:12]}"


def _resolve_search_roots(arguments: dict[str, Any]) -> list[str]:
    roots = arguments.get("roots")
    if isinstance(roots, list) and roots:
        return [str(item) for item in roots]
    root = str(arguments.get("root", "") or "").strip()
    return [root or MATERIALS_DIR_NAME]


def _resolve_grep_targets(arguments: dict[str, Any]) -> list[str]:
    paths = arguments.get("paths")
    if isinstance(paths, list) and paths:
        return [str(item) for item in paths]
    return _resolve_search_roots(arguments)


def _finalize_tool_result(
    runtime_context: AgentsToolRuntimeContext,
    *,
    tool_name: str,
    request_id: str,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "tool_name": tool_name,
        "request_id": request_id,
        "status": "ok",
        "summary": summary,
        "payload": payload,
        "output_ref": None,
        "is_truncated": False,
        "created_at": utc_now_iso(),
    }
    spec = _MATERIAL_TOOL_SPEC_INDEX[tool_name]
    output_ref = maybe_store_tool_payload(
        session_id=runtime_context.session_id,
        tool_name=tool_name,
        request_id=request_id,
        payload=payload,
        max_result_chars=int(spec.get("max_result_chars", 4000) or 4000),
        app_home=runtime_context.app_home,
    )
    if output_ref:
        result["output_ref"] = output_ref
        result["is_truncated"] = True
    runtime_context.tool_results.append(result)
    return result


def _run_material_tool(
    runtime_context: AgentsToolRuntimeContext,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    if tool_name == "search":
        query = str(arguments.get("query", "") or "").strip()
        roots = _resolve_search_roots(arguments)
        limit = int(arguments.get("limit", 20) or 20)
        hits = _search_materials_via_shell(
            query,
            roots=roots,
            working_root=runtime_context.working_root,
            limit=limit,
        )
        return _finalize_tool_result(
            runtime_context,
            tool_name=tool_name,
            request_id=request_id,
            summary=f"Found {len(hits)} candidate files for query '{query}'.",
            payload={
                "query": query,
                "items": hits,
                "selected_files": [hit["path"] for hit in hits[: min(len(hits), 5)]],
            },
        )

    if tool_name == "list":
        roots = _resolve_search_roots(arguments)
        limit = int(arguments.get("limit", 200) or 200)
        items = [
            _build_search_hit(path, discovered_by="list")
            for path in _list_material_paths_via_shell(
                roots,
                working_root=runtime_context.working_root,
                limit=limit,
            )
        ]
        return _finalize_tool_result(
            runtime_context,
            tool_name=tool_name,
            request_id=request_id,
            summary=f"Listed {len(items)} files.",
            payload={
                "items": items,
                "selected_files": [],
                "roots": [str(root) for root in roots],
            },
        )

    if tool_name == "read":
        target = str(arguments.get("path", "") or "").strip()
        payload = _read_material_with_shell(
            target,
            working_root=runtime_context.working_root,
            start_line=arguments.get("start_line"),
            end_line=arguments.get("end_line"),
            max_chars=arguments.get("max_chars"),
        )
        return _finalize_tool_result(
            runtime_context,
            tool_name=tool_name,
            request_id=request_id,
            summary=f"Read {payload['path']}.",
            payload=payload,
        )

    if tool_name == "grep":
        pattern = str(arguments.get("pattern", "") or "").strip()
        targets = _resolve_grep_targets(arguments)
        limit = int(arguments.get("limit", 50) or 50)
        matches = _run_rg_json(
            pattern,
            targets,
            working_root=runtime_context.working_root,
            case_sensitive=bool(arguments.get("case_sensitive", False)),
            limit=limit,
        )
        selected_files: list[str] = []
        for match in matches:
            path = str(match.get("path", "") or "").strip()
            if path and path not in selected_files:
                selected_files.append(path)
        return _finalize_tool_result(
            runtime_context,
            tool_name=tool_name,
            request_id=request_id,
            summary=f"Grep matched {len(matches)} lines for pattern '{pattern}'.",
            payload={
                "pattern": pattern,
                "matches": matches,
                "selected_files": selected_files,
            },
        )

    raise ValueError(f"Unknown material tool: {tool_name}")


def _execute_tool(
    ctx: RunContextWrapper[AgentsToolRuntimeContext],
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    runtime_context = ctx.context
    normalized_arguments = _clean_arguments(arguments)
    request = {
        "tool_name": tool_name,
        "arguments": normalized_arguments,
        "request_id": _new_request_id(),
    }
    runtime_context.tool_requests.append(request)
    return _run_material_tool(
        runtime_context,
        tool_name=tool_name,
        arguments=normalized_arguments,
        request_id=str(request["request_id"]),
    )


@function_tool(name_override="search")
def search_materials(
    ctx: RunContextWrapper[AgentsToolRuntimeContext],
    query: str,
    roots: list[str] | None = None,
    root: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """在 materials 中按主题搜索相关材料。"""
    arguments = {"query": query, "limit": limit}
    arguments.update(_merge_root_arguments(root=root, roots=roots))
    return _execute_tool(ctx, tool_name="search", arguments=arguments)


@function_tool(name_override="list")
def list_materials(
    ctx: RunContextWrapper[AgentsToolRuntimeContext],
    roots: list[str] | None = None,
    root: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """列出 materials 中可用的材料文件。"""
    arguments = {"limit": limit}
    arguments.update(_merge_root_arguments(root=root, roots=roots))
    return _execute_tool(ctx, tool_name="list", arguments=arguments)


@function_tool(name_override="read")
def read_material(
    ctx: RunContextWrapper[AgentsToolRuntimeContext],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """读取 materials 中指定文件或指定片段。"""
    return _execute_tool(
        ctx,
        tool_name="read",
        arguments={
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "max_chars": max_chars,
        },
    )


@function_tool(name_override="grep")
def grep_materials(
    ctx: RunContextWrapper[AgentsToolRuntimeContext],
    pattern: str,
    paths: list[str] | None = None,
    roots: list[str] | None = None,
    root: str | None = None,
    case_sensitive: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """在 materials 中按关键词精确定位文本片段。"""
    arguments = {
        "pattern": pattern,
        "case_sensitive": case_sensitive,
        "limit": limit,
    }
    arguments.update(_merge_root_arguments(root=root, roots=roots, paths=paths))
    return _execute_tool(ctx, tool_name="grep", arguments=arguments)


def build_material_function_tools() -> list[Any]:
    return [
        search_materials,
        list_materials,
        read_material,
        grep_materials,
    ]
