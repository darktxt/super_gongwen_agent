from __future__ import annotations

from tool_runtime.content_access import MATERIALS_DIR_NAME, search_materials
from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_search_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    query = str(request.arguments.get("query", "")).strip()
    roots = request.arguments.get("roots") or [request.arguments.get("root") or MATERIALS_DIR_NAME]
    limit = int(request.arguments.get("limit", 20))
    hits = [
        hit.to_dict()
        for hit in search_materials(
            query,
            roots,
            working_root=context.working_root,
            limit=limit,
        )
    ]
    return ToolResult(
        tool_name="search",
        request_id=request.request_id,
        summary=f"Found {len(hits)} candidate files for query '{query}'.",
        payload={
            "query": query,
            "items": hits,
            "selected_files": [hit["path"] for hit in hits[: min(len(hits), 5)]],
        },
    )
