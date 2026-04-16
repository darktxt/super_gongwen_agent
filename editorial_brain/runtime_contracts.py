from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.serialization import JsonDataclassMixin

from .contracts_core import BrainStepResult


@dataclass(slots=True)
class LLMRequest(JsonDataclassMixin):
    model: str
    system_prompt: str
    user_prompt: str
    context_blocks: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse(JsonDataclassMixin):
    content: str
    model: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BrainRunResult:
    request: LLMRequest
    response: LLMResponse
    step: BrainStepResult


@dataclass(slots=True)
class BrainRunError(RuntimeError):
    message: str
    request: LLMRequest
    response: LLMResponse
    raw_output: str

    def __str__(self) -> str:
        return self.message
