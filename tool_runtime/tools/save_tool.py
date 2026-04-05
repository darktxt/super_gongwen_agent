from __future__ import annotations

from pathlib import Path

from session_storage.history import save_final_output
from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_save_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    content = str(request.arguments.get("content", ""))
    target_kind = str(request.arguments.get("target_kind", "path"))

    if target_kind == "final_output" and context.session_id:
        target = save_final_output(
            context.session_id,
            content,
            app_home=context.app_home,
        )
    else:
        target_path = Path(request.arguments["target_path"])
        if not target_path.is_absolute():
            target_path = (context.working_root / target_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        target = target_path

    return ToolResult(
        tool_name="save",
        request_id=request.request_id,
        summary=f"Saved content to {target}.",
        payload={"target_path": str(target), "content_length": len(content)},
    )
