from __future__ import annotations

from tool_runtime.content_access import read_material
from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_read_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    target = request.arguments["path"]
    payload = read_material(
        target,
        working_root=context.working_root,
        start_line=request.arguments.get("start_line"),
        end_line=request.arguments.get("end_line"),
        max_chars=request.arguments.get("max_chars"),
    )
    return ToolResult(
        tool_name="read",
        request_id=request.request_id,
        summary=f"Read {payload['path']}.",
        payload=payload,
    )
