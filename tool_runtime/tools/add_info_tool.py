from __future__ import annotations

from tool_runtime.models import ToolExecutionContext, ToolRequest, ToolResult


def run_add_info_tool(request: ToolRequest, context: ToolExecutionContext) -> ToolResult:
    question = {
        "gap_id": str(request.arguments.get("gap_id", "")),
        "question": str(request.arguments.get("question", "")),
        "why_needed": str(request.arguments.get("why_needed", "")),
        "expected_format": str(request.arguments.get("expected_format", "")),
        "target_slot": str(request.arguments.get("target_slot", "")),
        "options": list(request.arguments.get("options", [])),
        "allow_multi_select": bool(request.arguments.get("allow_multi_select", False)),
    }
    return ToolResult(
        tool_name="add_info",
        request_id=request.request_id,
        status="needs_user_input",
        summary=question["question"] or "Need more user information.",
        payload={"questions": [question]},
    )
