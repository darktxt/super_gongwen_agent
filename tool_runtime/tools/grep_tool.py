from __future__ import annotations

from tool_runtime.content_access import MATERIALS_DIR_NAME, grep_materials
from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_grep_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    pattern = str(request.arguments.get("pattern", ""))
    targets = request.arguments.get("paths") or request.arguments.get("roots") or [MATERIALS_DIR_NAME]
    case_sensitive = bool(request.arguments.get("case_sensitive", False))
    limit = int(request.arguments.get("limit", 50))
    matches = [
        hit.to_dict()
        for hit in grep_materials(
            pattern,
            targets,
            working_root=context.working_root,
            case_sensitive=case_sensitive,
            limit=limit,
        )
    ]
    selected_files: list[str] = []
    for match in matches:
        if match["path"] not in selected_files:
            selected_files.append(match["path"])
    return ToolResult(
        tool_name="grep",
        request_id=request.request_id,
        summary=f"Grep matched {len(matches)} lines for pattern '{pattern}'.",
        payload={
            "pattern": pattern,
            "matches": matches,
            "selected_files": selected_files,
        },
    )
