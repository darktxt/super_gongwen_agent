from __future__ import annotations

from tool_runtime.content_access import MATERIALS_DIR_NAME, list_materials
from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_list_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    roots = request.arguments.get("roots") or [request.arguments.get("root") or MATERIALS_DIR_NAME]
    limit = int(request.arguments.get("limit", 200))
    items = [
        hit.to_dict()
        for hit in list_materials(roots, working_root=context.working_root, limit=limit)
    ]
    return ToolResult(
        tool_name="list",
        request_id=request.request_id,
        summary=f"Listed {len(items)} files.",
        payload={"items": items, "selected_files": [], "roots": [str(root) for root in roots]},
    )
