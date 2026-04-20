from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable


def preview_text(text: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def extract_text_from_output_item(item: Any) -> str:
    if isinstance(item, dict):
        if item.get("type") != "message":
            return ""
        return "".join(
            str(part.get("text", ""))
            for part in list(item.get("content", []) or [])
            if isinstance(part, dict) and part.get("type") == "output_text"
        ).strip()
    if getattr(item, "type", None) != "message":
        return ""
    return "".join(
        str(getattr(part, "text", ""))
        for part in list(getattr(item, "content", []) or [])
        if getattr(part, "type", None) == "output_text"
    ).strip()


def extract_last_response_text(raw_responses: list[Any]) -> str:
    for response in reversed(list(raw_responses or [])):
        for item in reversed(list(getattr(response, "output", []) or [])):
            text = extract_text_from_output_item(item)
            if text:
                return text
    return ""


def extract_last_run_data_text(run_data: Any) -> str:
    text = extract_last_response_text(getattr(run_data, "raw_responses", []))
    if text:
        return text
    for item in reversed(list(getattr(run_data, "new_items", []) or [])):
        text = extract_text_from_output_item(getattr(item, "raw_item", None))
        if text:
            return text
    return ""


def preview_value(value: Any, *, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return preview_text(value, limit=limit)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        text = json.dumps(model_dump(mode="json"), ensure_ascii=False)
        return preview_text(text, limit=limit)
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return preview_text(text, limit=limit)


def detect_tool_activity(items: list[Any]) -> bool:
    for item in list(items or []):
        raw_item = getattr(item, "raw_item", None)
        markers = " ".join(
            part
            for part in [
                str(getattr(item, "type", "") or ""),
                type(item).__name__,
                str((raw_item or {}).get("type", "") if isinstance(raw_item, dict) else getattr(raw_item, "type", "") or ""),
                type(raw_item).__name__ if raw_item is not None else "",
            ]
            if part
        ).lower()
        if any(token in markers for token in ("tool", "function_call", "computer_call", "mcp_call", "handoff")):
            return True
    return False


def summarize_run_data(run_data: Any) -> dict[str, Any]:
    raw_responses = list(getattr(run_data, "raw_responses", []) or [])
    new_items = list(getattr(run_data, "new_items", []) or [])
    last_text = extract_last_run_data_text(run_data)
    item_type_names: list[str] = []
    for item in new_items[-8:]:
        raw_item = getattr(item, "raw_item", None)
        item_type = str(getattr(item, "type", "") or "")
        raw_type = str((raw_item or {}).get("type", "") if isinstance(raw_item, dict) else getattr(raw_item, "type", "") or "")
        name = raw_type or item_type or type(item).__name__
        if name:
            item_type_names.append(name)
    return {
        "raw_response_count": len(raw_responses),
        "new_item_count": len(new_items),
        "new_item_types": item_type_names,
        "has_tool_activity": detect_tool_activity(new_items),
        "last_text_preview": preview_text(last_text, limit=280),
        "last_text_chars": len(last_text),
    }


def classify_model_behavior_error(
    *,
    repairer: "StructuredOutputRepairer",
    profile: "StructuredOutputRepairProfile",
    error_message: str,
    last_text: str,
    run_data_summary: dict[str, Any],
    model_name: str,
    base_url: str,
    repair_result: "RepairResult" | None = None,
) -> dict[str, Any]:
    normalized_message = str(error_message or "").strip()
    has_tool_activity = bool(run_data_summary.get("has_tool_activity"))
    repair = repair_result or repairer.recover(
        profile=profile,
        raw_output=last_text,
        error_message=normalized_message,
        has_tool_activity=has_tool_activity,
    )
    classification = str(repair.classification or ("tool_after_no_final_json" if has_tool_activity else "empty_output"))
    label = str(repair.classification_label or ("工具回合后未收口" if has_tool_activity else "空输出"))
    suspected_cause = ""
    if classification in {
        "schema_validation_failed",
        "noisy_json_output",
        "tool_after_no_final_json",
        "json_syntax_broken",
        "json_syntax_repaired",
        "error_embedded_json",
    }:
        suspected_cause = "高概率与模型或 LiteLLM 网关在工具回合后的结构化 JSON 收口稳定性不足有关。"
        model_hint = str(model_name or "").lower()
        base_url_hint = str(base_url or "").lower()
        if "minimax" in model_hint or "minimax" in base_url_hint:
            suspected_cause += " 当前接入为 minimax 系列模型，经 LiteLLM 转发，需重点关注供应商兼容性。"
    elif classification == "plain_text_output":
        suspected_cause = "高概率是模型忽略了必须输出结构化 JSON 的协议约束，直接输出了正文或说明。"
    elif classification == "empty_output":
        suspected_cause = "模型未返回可提取文本，需结合上游 provider 或 SDK trace 进一步确认。"
    return {
        "error_type": "ModelBehaviorError",
        "message": normalized_message,
        "classification": classification,
        "classification_label": label,
        "has_json_candidate": bool(repair.has_json_candidate),
        "json_candidate_count": int(repair.json_candidate_count),
        "has_tool_activity": has_tool_activity,
        "suspected_cause": suspected_cause,
        "repair_source": str(repair.source or ""),
        "repair_steps": list(repair.repair_steps),
        "recovered_candidate": bool(repair.value is not None),
    }


def build_recovery_summary(
    *,
    repair: "RepairResult",
    result: Any,
    fallback_source: str | None = None,
) -> dict[str, Any]:
    return {
        "fallback_source": str(fallback_source or repair.source or "structured_output_repair"),
        "result_action": str(getattr(result, "action", "") or ""),
        "result_completion_mode": str(getattr(result, "completion_mode", "") or ""),
        "repair_steps": list(repair.repair_steps),
    }


def map_judge_recovery_source(source: str) -> str:
    normalized = str(source or "").strip()
    if normalized == "run_data":
        return "judge_run_data"
    if normalized == "raw_output":
        return "judge_final_output"
    return normalized


def build_judge_run_record(
    *,
    round_no: int,
    judge_result: Any | None = None,
    raw_output: Any = None,
    repair: "RepairResult" | None = None,
    error: Exception | None = None,
    recovered: bool = False,
    default_source: str = "judge_final_output",
) -> dict[str, Any]:
    record = {
        "round": round_no,
        "raw_output_preview": str((repair.raw_preview if repair else "") or preview_value(raw_output, limit=320)),
        "recovered": recovered,
        "recovery_source": map_judge_recovery_source((repair.source if repair else "") or default_source),
        "repair_steps": list(repair.repair_steps) if repair is not None else [],
        "error_classification": str(repair.classification or "") if repair is not None else "",
        "error_classification_label": str(repair.classification_label or "") if repair is not None else "",
    }
    if judge_result is not None:
        record.update(
            {
                "score": str(getattr(judge_result, "score", "") or ""),
                "feedback": str(getattr(judge_result, "feedback", "") or ""),
                "suggested_action": str(getattr(judge_result, "suggested_action", "") or ""),
                "issues": list(getattr(judge_result, "issues", []) or []),
                "absorb_points": list(getattr(judge_result, "absorb_points", []) or []),
                "review_summary": str(getattr(judge_result, "review_summary", "") or ""),
            }
        )
        return record
    record.update(
        {
            "score": "judge_error",
            "feedback": str(error or ""),
            "issues": [],
            "review_summary": "",
        }
    )
    return record


@dataclass(slots=True)
class StructuredOutputRepairProfile:
    name: str
    validator: Callable[[Any], Any]


@dataclass(slots=True)
class RepairResult:
    value: Any | None = None
    recovered: bool = False
    source: str = ""
    raw_preview: str = ""
    classification: str = ""
    classification_label: str = ""
    repair_steps: list[str] = field(default_factory=list)
    has_json_candidate: bool = False
    json_candidate_count: int = 0


class StructuredOutputRepairer:
    def recover(
        self,
        *,
        profile: StructuredOutputRepairProfile,
        raw_output: Any = None,
        run_data: Any = None,
        error_message: str = "",
        has_tool_activity: bool = False,
    ) -> RepairResult:
        sources: list[tuple[str, str, list[str]]] = []
        seen: set[tuple[str, str]] = set()

        def add_source(name: str, text: str, base_steps: list[str] | None = None) -> None:
            normalized = str(text or "").strip()
            if not normalized:
                return
            key = (name, normalized)
            if key in seen:
                return
            seen.add(key)
            sources.append((name, normalized, list(base_steps or [])))

        if isinstance(raw_output, str):
            add_source("raw_output", raw_output)
        if run_data is not None:
            add_source("run_data", extract_last_run_data_text(run_data), ["extract_run_data_text"])
        if error_message:
            add_source("error_message", self._extract_error_payload(str(error_message)), ["extract_error_payload"])

        best_failure = RepairResult(
            recovered=False,
            source="",
            raw_preview="",
            classification=self._default_classification(has_tool_activity=has_tool_activity),
            classification_label=self._default_classification_label(has_tool_activity=has_tool_activity),
            repair_steps=[],
            has_json_candidate=False,
            json_candidate_count=0,
        )
        saw_schema_failure = False
        saw_syntax_failure = False

        for source_name, source_text, base_steps in sources:
            attempt, schema_failed, syntax_failed = self._recover_from_text(
                profile=profile,
                source_name=source_name,
                source_text=source_text,
                base_steps=base_steps,
            )
            if attempt.value is not None:
                attempt.recovered = True
                return attempt
            saw_schema_failure = saw_schema_failure or schema_failed
            saw_syntax_failure = saw_syntax_failure or syntax_failed
            if attempt.has_json_candidate and not best_failure.has_json_candidate:
                best_failure = attempt
            elif not best_failure.source:
                best_failure = attempt

        best_failure.classification = self._finalize_failure_classification(
            current=best_failure.classification,
            has_tool_activity=has_tool_activity,
            saw_schema_failure=saw_schema_failure,
            saw_syntax_failure=saw_syntax_failure,
            has_source=bool(sources),
        )
        best_failure.classification_label = self._classification_label(best_failure.classification)
        return best_failure

    def _recover_from_text(
        self,
        *,
        profile: StructuredOutputRepairProfile,
        source_name: str,
        source_text: str,
        base_steps: list[str],
    ) -> tuple[RepairResult, bool, bool]:
        candidates = self._build_json_candidates(source_text, base_steps=base_steps)
        jsonish_count = sum(1 for _, _, jsonish in candidates if jsonish)
        saw_schema_failure = False
        saw_syntax_failure = False
        last_steps: list[str] = []
        for candidate, steps, jsonish in candidates:
            last_steps = steps
            loaded = self._load_json_object(candidate)
            if loaded is not None:
                try:
                    value = profile.validator(loaded)
                except Exception:
                    saw_schema_failure = True
                else:
                    classification = self._classify_success(source_name=source_name, repair_steps=steps)
                    return (
                        RepairResult(
                            value=value,
                            recovered=True,
                            source=source_name,
                            raw_preview=preview_text(candidate, limit=320),
                            classification=classification,
                            classification_label=self._classification_label(classification),
                            repair_steps=steps,
                            has_json_candidate=True,
                            json_candidate_count=max(jsonish_count, 1),
                        ),
                        saw_schema_failure,
                        saw_syntax_failure,
                    )
            if jsonish:
                saw_syntax_failure = True
                repaired, repair_steps = self._repair_json_candidate(candidate)
                if repaired != candidate:
                    loaded = self._load_json_object(repaired)
                    if loaded is not None:
                        try:
                            value = profile.validator(loaded)
                        except Exception:
                            saw_schema_failure = True
                        else:
                            classification = self._classify_success(source_name=source_name, repair_steps=steps + repair_steps)
                            return (
                                RepairResult(
                                    value=value,
                                    recovered=True,
                                    source=source_name,
                                    raw_preview=preview_text(repaired, limit=320),
                                    classification=classification,
                                    classification_label=self._classification_label(classification),
                                    repair_steps=steps + repair_steps,
                                    has_json_candidate=True,
                                    json_candidate_count=max(jsonish_count, 1),
                                ),
                                saw_schema_failure,
                                saw_syntax_failure,
                            )
        classification = self._classify_failure(
            text=source_text,
            source_name=source_name,
            has_json_candidate=jsonish_count > 0,
            saw_schema_failure=saw_schema_failure,
            saw_syntax_failure=saw_syntax_failure,
        )
        return (
            RepairResult(
                recovered=False,
                source=source_name,
                raw_preview=preview_text(source_text, limit=320),
                classification=classification,
                classification_label=self._classification_label(classification),
                repair_steps=last_steps,
                has_json_candidate=jsonish_count > 0,
                json_candidate_count=jsonish_count,
            ),
            saw_schema_failure,
            saw_syntax_failure,
        )

    def _build_json_candidates(self, text: str, *, base_steps: list[str] | None = None) -> list[tuple[str, list[str], bool]]:
        stripped = str(text or "").strip()
        candidates: list[tuple[str, list[str], bool]] = []
        seen: set[str] = set()

        def add(candidate: str, steps: list[str]) -> None:
            normalized = str(candidate or "").strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append((normalized, [*list(base_steps or []), *steps], self._looks_like_json_candidate(normalized)))

        if stripped:
            add(stripped, ["raw_text"])
        error_payload = self._extract_error_payload(stripped)
        if error_payload and error_payload != stripped:
            add(error_payload, ["extract_error_payload"])
        for match in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE):
            add(match, ["strip_markdown_fence"])
        for match in self._extract_brace_objects(stripped):
            add(match, ["extract_brace_object"])
        return candidates

    def _extract_error_payload(self, text: str) -> str:
        normalized = str(text or "").strip()
        marker = "Invalid JSON when parsing"
        if marker in normalized:
            normalized = normalized.split(marker, 1)[1].strip()
        for token in (
            " for TypeAdapter(",
            "\n    For further information",
            "\r\n    For further information",
            "\n  Input should be ",
            "\r\n  Input should be ",
        ):
            if token in normalized:
                normalized = normalized.split(token, 1)[0].strip()
        return normalized

    def _extract_brace_objects(self, text: str) -> list[str]:
        stripped = str(text or "").strip()
        objects: list[str] = []
        depth = 0
        start: int | None = None
        in_string = False
        escaped = False
        for index, char in enumerate(stripped):
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
                    objects.append(stripped[start : index + 1])
                    start = None
        return objects

    def _repair_json_candidate(self, text: str) -> tuple[str, list[str]]:
        repaired = str(text or "")
        steps: list[str] = []
        escaped_quotes = self._escape_unescaped_inner_quotes(repaired)
        if escaped_quotes != repaired:
            repaired = escaped_quotes
            steps.append("escape_inner_quotes")
        return repaired, steps

    def _escape_unescaped_inner_quotes(self, text: str) -> str:
        chars: list[str] = []
        in_string = False
        escaped = False
        length = len(text)
        for index, char in enumerate(text):
            if not in_string:
                chars.append(char)
                if char == '"':
                    in_string = True
                    escaped = False
                continue
            if escaped:
                chars.append(char)
                escaped = False
                continue
            if char == "\\":
                chars.append(char)
                escaped = True
                continue
            if char == '"':
                next_non_ws = ""
                for next_index in range(index + 1, length):
                    probe = text[next_index]
                    if probe.isspace():
                        continue
                    next_non_ws = probe
                    break
                if next_non_ws in {",", "}", "]", ":"} or not next_non_ws:
                    chars.append(char)
                    in_string = False
                else:
                    chars.append('\\"')
                continue
            chars.append(char)
        return "".join(chars)

    def _load_json_object(self, text: str) -> dict[str, Any] | None:
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None

    def _looks_like_json_candidate(self, text: str) -> bool:
        stripped = str(text or "").strip()
        return bool(stripped) and "{" in stripped and "}" in stripped

    def _classify_success(self, *, source_name: str, repair_steps: list[str]) -> str:
        if "escape_inner_quotes" in repair_steps:
            return "json_syntax_repaired"
        if source_name == "error_message":
            return "error_embedded_json"
        if "strip_markdown_fence" in repair_steps:
            return "fenced_json_wrapped"
        if "extract_brace_object" in repair_steps and "raw_text" not in repair_steps:
            return "noisy_json_output"
        return "structured_output_recovered"

    def _classify_failure(
        self,
        *,
        text: str,
        source_name: str,
        has_json_candidate: bool,
        saw_schema_failure: bool,
        saw_syntax_failure: bool,
    ) -> str:
        stripped = str(text or "").strip()
        if not stripped:
            return "empty_output"
        if saw_schema_failure:
            return "schema_validation_failed"
        if saw_syntax_failure:
            return "json_syntax_broken"
        if source_name == "error_message" and has_json_candidate:
            return "error_embedded_json"
        if "```" in stripped and has_json_candidate:
            return "fenced_json_wrapped"
        if has_json_candidate:
            return "noisy_json_output"
        return "plain_text_output"

    def _finalize_failure_classification(
        self,
        *,
        current: str,
        has_tool_activity: bool,
        saw_schema_failure: bool,
        saw_syntax_failure: bool,
        has_source: bool,
    ) -> str:
        if saw_schema_failure:
            return "schema_validation_failed"
        if saw_syntax_failure:
            return "json_syntax_broken"
        if current and current != "unknown_model_behavior":
            return current
        if not has_source:
            return "tool_after_no_final_json" if has_tool_activity else "empty_output"
        return "tool_after_no_final_json" if has_tool_activity else "plain_text_output"

    def _default_classification(self, *, has_tool_activity: bool) -> str:
        return "tool_after_no_final_json" if has_tool_activity else "empty_output"

    def _default_classification_label(self, *, has_tool_activity: bool) -> str:
        return self._classification_label(self._default_classification(has_tool_activity=has_tool_activity))

    def _classification_label(self, classification: str) -> str:
        labels = {
            "structured_output_recovered": "结构化输出已恢复",
            "error_embedded_json": "异常消息内嵌 JSON",
            "fenced_json_wrapped": "围栏 JSON 包裹",
            "noisy_json_output": "带杂质的 JSON 输出",
            "json_syntax_repaired": "JSON 语法修复",
            "json_syntax_broken": "JSON 语法损坏",
            "schema_validation_failed": "Schema 校验失败",
            "plain_text_output": "正文直出",
            "tool_after_no_final_json": "工具回合后未收口",
            "empty_output": "空输出",
        }
        return labels.get(classification, "未知结构化输出问题")
