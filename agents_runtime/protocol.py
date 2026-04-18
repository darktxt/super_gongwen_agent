from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Mapping

from utils.serialization import JsonDataclassMixin
from workspace.models import SelfReview, WorkspacePatch


VALID_ACTIONS = {
    "build_outline",
    "write_draft",
    "write_section",
    "revise_draft",
    "polish_language",
    "ask_user",
    "finalize",
}
CONTROL_ONLY_ACTIONS = {"ask_user"}
_LIST_FIELDS = {"outline_sections", "question_pack"}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        serialized = value.to_dict()
        if isinstance(serialized, Mapping):
            return dict(serialized)
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clean_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, raw in dict(value).items():
        name = _text(key)
        if not name:
            continue
        if name in _LIST_FIELDS:
            items: list[Any] = []
            for item in _as_list(raw):
                items.append(dict(item) if isinstance(item, Mapping) else item)
            if items:
                cleaned[name] = items
            continue
        if isinstance(raw, Mapping):
            nested = dict(raw)
            if nested:
                cleaned[name] = nested
            continue
        text = _text(raw)
        if text:
            cleaned[name] = text
    return cleaned


@dataclass(slots=True)
class ActionPayload(JsonDataclassMixin):
    data: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name in self.data:
            return self.data[name]
        if name in _LIST_FIELDS:
            return []
        return ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)

    @classmethod
    def from_action(cls, action_taken: str, payload: Any) -> "ActionPayload":
        current = _as_dict(payload)
        nested = current.get(action_taken)
        if isinstance(nested, Mapping):
            current = dict(nested)
        cleaned = _clean_dict(current)
        if action_taken == "ask_user" and isinstance(cleaned.get("question_pack"), dict):
            cleaned["question_pack"] = [dict(cleaned["question_pack"])]
        return cls(data=cleaned)


@dataclass(slots=True)
class BrainStepResult(JsonDataclassMixin):
    action_taken: str
    action_payload: ActionPayload = field(default_factory=ActionPayload)
    workspace_patch: WorkspacePatch = field(default_factory=WorkspacePatch)
    self_review: SelfReview = field(default_factory=SelfReview)

    @property
    def ask_user(self) -> bool:
        return self.action_taken == "ask_user"

    @property
    def done(self) -> bool:
        return self.action_taken == "finalize"

    @property
    def has_self_review(self) -> bool:
        review = self.self_review
        return any(
            [
                list(review.responded_directives or []),
                _text(review.dominant_issue),
                list(review.open_gaps or []),
                _text(review.content_status_summary),
                _text(review.language_status_summary),
                list(review.notes or []),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action_taken": self.action_taken,
            "action_payload": {self.action_taken: self.action_payload.to_dict()},
        }
        if any(self.workspace_patch.to_dict().values()):
            payload["workspace_patch"] = self.workspace_patch.to_dict()
        if self.has_self_review and self.action_taken not in CONTROL_ONLY_ACTIONS:
            payload["self_review"] = self.self_review.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | "BrainStepResult") -> "BrainStepResult":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("BrainStepResult.from_dict expects a mapping payload.")
        normalized = dict(payload)
        action_taken = _text(normalized.get("action_taken"))
        if action_taken not in VALID_ACTIONS:
            raise ValueError(f"Unsupported action_taken: {action_taken}")
        return cls(
            action_taken=action_taken,
            action_payload=ActionPayload.from_action(action_taken, normalized.get("action_payload")),
            workspace_patch=WorkspacePatch.from_dict(normalized.get("workspace_patch", {})),
            self_review=SelfReview.from_dict(normalized.get("self_review", {})),
        )


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
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class BrainRunError(RuntimeError):
    message: str
    request: LLMRequest
    response: LLMResponse
    raw_output: str

    def __str__(self) -> str:
        return self.message


class OutputParseError(ValueError):
    pass


class OutputParser:
    def parse(self, raw_output: str | Mapping[str, Any] | LLMResponse) -> BrainStepResult:
        if isinstance(raw_output, BrainStepResult):
            return raw_output
        if isinstance(raw_output, LLMResponse):
            raw_output = raw_output.content
        if isinstance(raw_output, Mapping):
            return BrainStepResult.from_dict(raw_output)
        if not isinstance(raw_output, str):
            raise OutputParseError("Model output must be str, mapping, or LLMResponse.")

        for candidate in self._candidate_json_strings(raw_output):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, Mapping):
                return BrainStepResult.from_dict(parsed)
        snippet = raw_output[:300].replace("\n", "\\n")
        raise OutputParseError(f"Failed to parse BrainStepResult from output: {snippet}")

    def _candidate_json_strings(self, text: str) -> list[str]:
        stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        candidates = [stripped] if stripped else []
        candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE))
        candidates.extend(self._extract_balanced_objects(text))
        unique: list[str] = []
        for candidate in candidates:
            normalized = _text(candidate)
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    def _extract_balanced_objects(self, text: str) -> list[str]:
        results: list[str] = []
        start: int | None = None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif char == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    results.append(text[start : index + 1])
                    start = None
        return results
