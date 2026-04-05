from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .models import ToolExecutionContext, ToolRequest
from .registry import ToolRegistry
from .result_store import maybe_store_tool_payload


class ToolExecutor:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry.build_default()

    def execute_batch(
        self,
        requests: Iterable[ToolRequest | dict[str, Any]],
        *,
        working_root: str | Path,
        session_id: str | None = None,
        app_home: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        context = ToolExecutionContext(
            working_root=Path(working_root).resolve(),
            session_id=session_id,
            app_home=Path(app_home).resolve() if app_home is not None else None,
        )

        results: list[dict[str, Any]] = []
        for raw_request in requests:
            request = (
                raw_request
                if isinstance(raw_request, ToolRequest)
                else ToolRequest.from_dict(raw_request)
            )
            registered = self.registry.get(request.tool_name)
            result = registered.handler(request, context)
            output_ref = maybe_store_tool_payload(
                session_id=session_id,
                tool_name=result.tool_name,
                request_id=result.request_id,
                payload=result.payload,
                max_result_chars=registered.spec.max_result_chars,
                app_home=context.app_home,
            )
            if output_ref:
                result.output_ref = output_ref
                result.is_truncated = True
            results.append(result.to_dict())
        return results
