from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol
import json

from config import default_openai_timeout
from utils.serialization import JsonDataclassMixin

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency absence is environment-specific
    OpenAI = None  # type: ignore[assignment]


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


class LLMClient(Protocol):
    def invoke(self, request: LLMRequest) -> LLMResponse:
        ...


class UnconfiguredLLMClient:
    def __init__(self, reason: str = "LLM client is not configured for run_turn.") -> None:
        self.reason = reason

    def invoke(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError(self.reason)


class OpenAIClientConfigError(ValueError):
    pass


class OpenAIInvokeError(RuntimeError):
    pass


DEFAULT_OPENAI_TIMEOUT = default_openai_timeout()


class OpenAICloudLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = DEFAULT_OPENAI_TIMEOUT,
        temperature: float | None = None,
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not api_key.strip():
            raise OpenAIClientConfigError("OPENAI_API_KEY is required for cloud LLM calls.")
        if not model.strip():
            raise OpenAIClientConfigError("OPENAI_MODEL is required for cloud LLM calls.")

        if client is None and client_factory is None and OpenAI is None:
            raise OpenAIClientConfigError(
                "openai package is not installed, cannot create cloud LLM client."
            )

        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = (base_url or "").strip()
        self.timeout = timeout
        self.temperature = temperature

        if client is not None:
            self._client = client
        else:
            factory = client_factory or OpenAI
            self._client = factory(
                api_key=self.api_key,
                base_url=self.base_url or None,
                timeout=self.timeout,
            )

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> "OpenAICloudLLMClient":
        configured_timeout = float(
            getattr(config, "openai_timeout", DEFAULT_OPENAI_TIMEOUT) or DEFAULT_OPENAI_TIMEOUT
        )
        return cls(
            api_key=str(getattr(config, "openai_api_key", "") or ""),
            base_url=str(getattr(config, "openai_base_url", "") or ""),
            model=str(getattr(config, "openai_model", "") or ""),
            timeout=configured_timeout,
            temperature=getattr(config, "openai_temperature", None),
            client=client,
            client_factory=client_factory,
        )

    def invoke(self, request: LLMRequest) -> LLMResponse:
        messages = self._build_messages(request)
        call_kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages,
        }
        if self.temperature is not None:
            call_kwargs["temperature"] = self.temperature

        try:
            completion = self._client.chat.completions.create(**call_kwargs)
        except Exception as exc:  # pragma: no cover - SDK/network failure path
            raise OpenAIInvokeError(f"OpenAI cloud invocation failed: {exc}") from exc

        content = self._extract_content(completion)
        return LLMResponse(
            content=content,
            model=str(getattr(completion, "model", "") or call_kwargs["model"]),
            raw_payload=self._model_dump(completion),
        )

    def _build_messages(self, request: LLMRequest) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": self._render_user_content(request)},
        ]

    def _render_user_content(self, request: LLMRequest) -> str:
        lines = [request.user_prompt.strip()]

        if request.context_blocks:
            lines.append("")
            lines.append("\u8865\u5145\u4e0a\u4e0b\u6587\uff1a")
            for index, block in enumerate(request.context_blocks, start=1):
                title = str(block.get("title", "") or f"Context {index}")
                content = str(block.get("content", "") or "").strip()
                lines.append(f"## {title}")
                lines.append(content or "[empty]")

        if request.metadata:
            lines.append("")
            lines.append("\u8fd0\u884c\u65f6\u5143\u6570\u636e\uff1a")
            lines.append(json.dumps(request.metadata, ensure_ascii=False, indent=2))

        return "\n".join(lines).strip()

    def _extract_content(self, completion: Any) -> str:
        choices = list(getattr(completion, "choices", []) or [])
        if not choices:
            raise OpenAIInvokeError("OpenAI cloud response does not contain choices.")

        message = getattr(choices[0], "message", None)
        if message is None:
            raise OpenAIInvokeError("OpenAI cloud response does not contain a message.")

        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content

        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                    continue
                text_value = getattr(item, "text", None)
                if isinstance(text_value, str) and text_value:
                    text_parts.append(text_value)
            if text_parts:
                return "\n".join(text_parts)

        refusal = getattr(message, "refusal", None)
        if isinstance(refusal, str) and refusal.strip():
            return refusal

        raise OpenAIInvokeError("OpenAI cloud response does not contain textual content.")

    def _model_dump(self, completion: Any) -> dict[str, Any]:
        if hasattr(completion, "model_dump"):
            return dict(completion.model_dump())
        if isinstance(completion, dict):
            return dict(completion)
        return {"repr": repr(completion)}


class ScriptedLLMClient:
    def __init__(self, scripted_outputs: Iterable[str | dict[str, Any] | LLMResponse]) -> None:
        self._outputs = list(scripted_outputs)
        self.requests: list[LLMRequest] = []

    def invoke(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._outputs:
            raise RuntimeError("ScriptedLLMClient has no scripted outputs left.")

        output = self._outputs.pop(0)
        if isinstance(output, LLMResponse):
            return output
        if isinstance(output, dict):
            return LLMResponse(
                content=json.dumps(output, ensure_ascii=False, indent=2),
                model=request.model,
                raw_payload={"scripted": True},
            )
        return LLMResponse(content=str(output), model=request.model, raw_payload={"scripted": True})
