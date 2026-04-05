from __future__ import annotations

from pathlib import Path

from tool_runtime.content_access import diff_text, safe_read_text
from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_diff_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    old_text = _resolve_text(request.arguments, "old", context)
    new_text = _resolve_text(request.arguments, "new", context)
    diff_output = diff_text(old_text, new_text)
    change_lines = len(diff_output.splitlines()) if diff_output else 0
    return ToolResult(
        tool_name="diff",
        request_id=request.request_id,
        summary=f"Generated diff with {change_lines} lines.",
        payload={"diff": diff_output, "change_lines": change_lines},
    )


def _resolve_text(
    arguments: dict[str, object],
    prefix: str,
    context: ToolExecutionContext,
) -> str:
    text_key = f"{prefix}_text"
    path_key = f"{prefix}_path"
    if text_key in arguments:
        return str(arguments[text_key])
    path = Path(str(arguments[path_key]))
    if not path.is_absolute():
        path = (context.working_root / path).resolve()
    return safe_read_text(path)
