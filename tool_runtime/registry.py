from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .models import ToolExecutionContext, ToolRequest, ToolResult, ToolSpec
from .tools.add_info_tool import run_add_info_tool
from .tools.diff_tool import run_diff_tool
from .tools.grep_tool import run_grep_tool
from .tools.list_tool import run_list_tool
from .tools.read_tool import run_read_tool
from .tools.save_tool import run_save_tool
from .tools.search_tool import run_search_tool


ToolHandler = Callable[[ToolRequest, ToolExecutionContext], ToolResult]
MATERIAL_TOOL_NAMES = ("search", "list", "read", "grep")


@dataclass(slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def get(self, tool_name: str) -> RegisteredTool:
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_name}") from exc

    def list_specs(self, *, tool_names: tuple[str, ...] | None = None) -> list[ToolSpec]:
        names = tool_names or tuple(sorted(self._tools))
        return [self._tools[name].spec for name in names if name in self._tools]

    def list_material_specs(self) -> list[ToolSpec]:
        return self.list_specs(tool_names=MATERIAL_TOOL_NAMES)

    @classmethod
    def build_default(cls) -> "ToolRegistry":
        registry = cls()
        registry.register(
            ToolSpec("add_info", False, False, True, 4000),
            run_add_info_tool,
        )
        registry.register(
            ToolSpec("diff", True, True, False, 4000),
            run_diff_tool,
        )
        registry.register(
            ToolSpec("grep", True, True, False, 4000),
            run_grep_tool,
        )
        registry.register(
            ToolSpec("list", True, True, False, 4000),
            run_list_tool,
        )
        registry.register(
            ToolSpec("read", True, True, False, 4000),
            run_read_tool,
        )
        registry.register(
            ToolSpec("save", False, False, False, 4000),
            run_save_tool,
        )
        registry.register(
            ToolSpec("search", True, True, False, 4000),
            run_search_tool,
        )
        return registry
