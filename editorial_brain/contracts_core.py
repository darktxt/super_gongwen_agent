from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from utils.serialization import JsonDataclassMixin
from workspace.models import SelfReview, WorkspacePatch


VALID_ACTIONS = {
    "load_skill",
    "build_outline",
    "write_draft",
    "write_section",
    "revise_draft",
    "polish_language",
    "ask_user",
    "finalize",
}

DRAFT_ACTIONS = {
    "write_draft",
    "write_section",
    "revise_draft",
    "polish_language",
    "finalize",
}

CONTROL_ONLY_ACTIONS = {
    "load_skill",
    "ask_user",
}


_TEXTISH_KEYS = (
    "content",
    "text",
    "value",
    "label",
    "skill_id",
    "title",
    "heading",
    "goal",
    "description",
    "summary",
    "name",
    "question",
    "pattern",
    "query",
    "path",
)


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        serialized = value.to_dict()
        if isinstance(serialized, Mapping):
            return dict(serialized)
    return {}


def _has_items(value: Any) -> bool:
    return bool(_as_list(value))


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return bool(value)


def _normalize_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text:
            return 0
        digits = "".join(char for char in text if char.isdigit())
        if not digits:
            return 0
        try:
            return int(digits)
        except ValueError:
            return 0


def _extract_textish(
    payload: Mapping[str, Any] | None,
    aliases: tuple[str, ...] = _TEXTISH_KEYS,
) -> str:
    normalized = _as_mapping(payload)
    for key in aliases:
        value = str(normalized.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_string_list(
    payload: Any,
    *,
    item_aliases: tuple[str, ...] = _TEXTISH_KEYS,
) -> list[str]:
    normalized: list[str] = []
    for item in _as_list(payload):
        if item is None:
            continue
        if isinstance(item, Mapping):
            text = _extract_textish(item, item_aliases)
        elif isinstance(item, (list, tuple, set)):
            text = "; ".join(_normalize_string_list(item, item_aliases=item_aliases))
        else:
            text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _coerce_text_blob(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return _extract_textish(
            value,
            (
                "full_text",
                "draft_text",
                "revised_text",
                "polished_text",
                "final_text",
                "section_text",
                "outline_text",
                "text",
                "content",
                "value",
            ),
        )
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_normalize_string_list(value))
    return str(value).strip()


def _extract_action_main_text(payload: Any) -> str:
    normalized = _as_mapping(payload)
    text = _extract_compatible_draft_text(normalized)
    if text:
        return text
    return _coerce_text_blob(payload)


def _normalize_record_list(
    payload: Any,
    item_normalizer: Any,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(payload), start=1):
        record = item_normalizer(item, index)
        if record:
            normalized.append(record)
    return normalized


def _normalize_text_record(item: Any, _: int) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        normalized = _as_mapping(item)
        return normalized or None
    text = _coerce_text_blob(item)
    if not text:
        return None
    return {"content": text}


def _normalize_slot_mapping(payload: Any) -> dict[str, list[str]]:
    if isinstance(payload, Mapping):
        normalized: dict[str, list[str]] = {}
        for key, value in dict(payload).items():
            slot = str(key or "").strip()
            values = _normalize_string_list(value)
            if slot and values:
                normalized[slot] = values
        return normalized

    normalized = {}
    for item in _as_list(payload):
        if not isinstance(item, Mapping):
            continue
        slot = _extract_textish(item, ("slot", "key", "name", "target_slot", "id"))
        values = _normalize_string_list(
            item.get("values", item.get("items", item.get("candidates", item.get("value"))))
        )
        if slot and values:
            normalized[slot] = values
    return normalized


def _normalize_option_list(payload: Any) -> list[Any]:
    normalized: list[Any] = []
    for item in _as_list(payload):
        if isinstance(item, Mapping):
            option = _as_mapping(item)
            if option:
                normalized.append(option)
            continue
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


@dataclass(slots=True)
class LoadSkillActionPayload(JsonDataclassMixin):
    primary_skill_id: str = ""
    revision_skill_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BuildOutlineActionPayload(JsonDataclassMixin):
    outline_text: str = ""
    outline_sections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class WriteDraftActionPayload(JsonDataclassMixin):
    draft_text: str = ""


@dataclass(slots=True)
class WriteSectionActionPayload(JsonDataclassMixin):
    section_id: str = ""
    section_text: str = ""


@dataclass(slots=True)
class ReviseDraftActionPayload(JsonDataclassMixin):
    revised_text: str = ""


@dataclass(slots=True)
class PolishLanguageActionPayload(JsonDataclassMixin):
    polished_text: str = ""


@dataclass(slots=True)
class AskUserActionPayload(JsonDataclassMixin):
    question_pack: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class FinalizeActionPayload(JsonDataclassMixin):
    final_text: str = ""


ACTION_PAYLOAD_TYPES = {
    "load_skill": LoadSkillActionPayload,
    "build_outline": BuildOutlineActionPayload,
    "write_draft": WriteDraftActionPayload,
    "write_section": WriteSectionActionPayload,
    "revise_draft": ReviseDraftActionPayload,
    "polish_language": PolishLanguageActionPayload,
    "ask_user": AskUserActionPayload,
    "finalize": FinalizeActionPayload,
}


def _normalize_outline_section(item: Any, index: int) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        normalized = _as_mapping(item)
        section_id = _extract_textish(
            normalized,
            ("section_id", "id", "section", "slug", "key"),
        ) or f"section_{index:03d}"
        heading = _extract_textish(
            normalized,
            ("heading", "title", "name", "section_title", "text", "content"),
        )
        goal = _extract_textish(
            normalized,
            ("goal", "objective", "purpose", "summary", "description"),
        )
        required_points = _normalize_string_list(
            normalized.get("required_points", normalized.get("points"))
        )
        evidence_refs = _normalize_string_list(
            normalized.get("evidence_refs", normalized.get("evidence", normalized.get("refs")))
        )
        notes = _normalize_string_list(
            normalized.get("notes", normalized.get("remarks"))
        )
        if not (heading or goal or required_points or evidence_refs or notes):
            return None
        return {
            "section_id": section_id,
            "heading": heading,
            "goal": goal,
            "required_points": required_points,
            "evidence_refs": evidence_refs,
            "notes": notes,
        }

    heading = str(item or "").strip()
    if not heading:
        return None
    return {
        "section_id": f"section_{index:03d}",
        "heading": heading,
        "goal": "",
        "required_points": [],
        "evidence_refs": [],
        "notes": [],
    }


def _normalize_outline_sections(payload: Any) -> list[dict[str, Any]]:
    return _normalize_record_list(payload, _normalize_outline_section)


def _normalize_question_item(item: Any, index: int) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        normalized = _as_mapping(item)
        question = _extract_textish(
            normalized,
            (
                "question",
                "prompt",
                "question_text",
                "text",
                "content",
                "primary_question",
            ),
        )
        why_needed = _extract_textish(
            normalized,
            ("why_needed", "why", "reason", "context_needed"),
        )
        expected_format = _extract_textish(
            normalized,
            ("expected_format", "format", "answer_format", "fallback_strategy"),
        )
        target_slot = _extract_textish(
            normalized,
            ("target_slot", "slot", "field"),
        )
        gap_id = _extract_textish(
            normalized,
            ("gap_id", "id"),
        ) or f"gap_{index:03d}"
        options = _normalize_option_list(
            normalized.get("options", normalized.get("choices"))
        )
        allow_multi_select = _normalize_bool(
            normalized.get("allow_multi_select", normalized.get("multi_select", False))
        )
        if not (question or why_needed or expected_format or target_slot):
            return None
        return {
            "gap_id": gap_id,
            "question": question,
            "why_needed": why_needed,
            "expected_format": expected_format,
            "target_slot": target_slot,
            "options": options,
            "allow_multi_select": allow_multi_select,
        }

    question = str(item or "").strip()
    if not question:
        return None
    return {
        "gap_id": f"gap_{index:03d}",
        "question": question,
        "why_needed": "",
        "expected_format": "",
        "target_slot": "",
        "options": [],
        "allow_multi_select": False,
    }


def _normalize_question_pack(payload: Any) -> list[dict[str, Any]]:
    return _normalize_record_list(payload, _normalize_question_item)


def _infer_tool_name_from_mapping(payload: Mapping[str, Any]) -> str:
    explicit = _extract_textish(payload, ("tool_name", "name", "tool", "type"))
    if explicit:
        return explicit
    if any(key in payload for key in ("pattern", "case_sensitive")):
        return "grep"
    if any(key in payload for key in ("path", "file", "start_line", "end_line", "max_chars")):
        return "read"
    if "query" in payload or "keyword" in payload:
        return "search"
    if "question" in payload or "why_needed" in payload or "target_slot" in payload:
        return "add_info"
    if "roots" in payload or "root" in payload:
        return "list"
    return ""


def _build_tool_arguments(tool_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _as_mapping(payload)

    if tool_name == "read":
        arguments: dict[str, Any] = {}
        path = _extract_textish(normalized, ("path", "file", "target"))
        if path:
            arguments["path"] = path
        for key in ("start_line", "end_line", "max_chars"):
            value = normalized.get(key)
            if value not in (None, ""):
                arguments[key] = value
        return arguments

    if tool_name == "grep":
        arguments = {}
        pattern = _extract_textish(normalized, ("pattern", "query", "keyword", "text"))
        if pattern:
            arguments["pattern"] = pattern
        paths = normalized.get(
            "paths",
            normalized.get("roots", normalized.get("root", normalized.get("path"))),
        )
        path_values = _normalize_string_list(paths)
        if "paths" in normalized and path_values:
            arguments["paths"] = path_values
        elif "roots" in normalized and path_values:
            arguments["roots"] = path_values
        elif "root" in normalized and path_values:
            arguments["root"] = path_values[0]
        elif "path" in normalized and path_values:
            arguments["paths"] = path_values
        elif pattern:
            arguments["roots"] = [MATERIALS_DIR_NAME]
        if "case_sensitive" in normalized:
            arguments["case_sensitive"] = _normalize_bool(normalized.get("case_sensitive"))
        if normalized.get("limit") not in (None, ""):
            arguments["limit"] = normalized.get("limit")
        return arguments

    if tool_name == "search":
        arguments = {}
        query = _extract_textish(normalized, ("query", "keyword", "pattern", "text", "content"))
        if query:
            arguments["query"] = query
        roots = normalized.get("roots", normalized.get("root"))
        root_values = _normalize_string_list(roots)
        if "root" in normalized and len(root_values) == 1:
            arguments["root"] = root_values[0]
        elif root_values:
            arguments["roots"] = root_values
        elif query:
            arguments["roots"] = [MATERIALS_DIR_NAME]
        if normalized.get("limit") not in (None, ""):
            arguments["limit"] = normalized.get("limit")
        return arguments

    if tool_name == "list":
        root_values = _normalize_string_list(
            normalized.get("roots", normalized.get("root", normalized.get("paths")))
        )
        arguments = {"roots": root_values or [MATERIALS_DIR_NAME]}
        if normalized.get("limit") not in (None, ""):
            arguments["limit"] = normalized.get("limit")
        return arguments

    if tool_name == "add_info":
        return {
            "gap_id": _extract_textish(normalized, ("gap_id", "id")),
            "question": _extract_textish(
                normalized,
                ("question", "prompt", "question_text", "text", "content"),
            ),
            "why_needed": _extract_textish(normalized, ("why_needed", "why", "reason")),
            "expected_format": _extract_textish(
                normalized,
                ("expected_format", "format", "answer_format"),
            ),
            "target_slot": _extract_textish(normalized, ("target_slot", "slot", "field")),
            "options": _normalize_option_list(
                normalized.get("options", normalized.get("choices"))
            ),
            "allow_multi_select": _normalize_bool(
                normalized.get("allow_multi_select", normalized.get("multi_select", False))
            ),
        }

    return {
        key: value
        for key, value in normalized.items()
        if key not in {"tool_name", "name", "tool", "type", "request_id", "arguments"}
    }


def _apply_tool_argument_defaults(tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(_as_mapping(arguments))
    if tool_name in {"search", "grep"}:
        if "roots" not in normalized and "root" not in normalized and "paths" not in normalized:
            normalized["roots"] = [MATERIALS_DIR_NAME]
    elif tool_name == "list":
        if "roots" not in normalized and "root" not in normalized:
            normalized["roots"] = [MATERIALS_DIR_NAME]
    return normalized


def _infer_tool_request_from_text(text: str) -> tuple[str, dict[str, Any]]:
    stripped = str(text or "").strip()
    if not stripped:
        return "", {}

    if ":" in stripped:
        prefix, remainder = stripped.split(":", 1)
        tool_name = prefix.strip().lower()
        remainder = remainder.strip()
        if tool_name == "read":
            return "read", {"path": remainder}
        if tool_name == "grep":
            return "grep", {"pattern": remainder, "roots": [MATERIALS_DIR_NAME]}
        if tool_name == "list":
            return "list", {"roots": _normalize_string_list(remainder) or [MATERIALS_DIR_NAME]}
        if tool_name == "add_info":
            return "add_info", {"question": remainder}
        if tool_name == "search":
            return "search", {"query": remainder, "roots": [MATERIALS_DIR_NAME]}

    if any(separator in stripped for separator in ("\\", "/")):
        return "read", {"path": stripped}
    if any(
        stripped.lower().endswith(suffix)
        for suffix in (".md", ".txt", ".json", ".yaml", ".yml", ".doc", ".docx")
    ):
        return "read", {"path": stripped}
    return "search", {"query": stripped, "roots": [MATERIALS_DIR_NAME]}


def _normalize_tool_request(item: Any, index: int) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        normalized = _as_mapping(item)
        tool_name = _infer_tool_name_from_mapping(normalized)
        arguments = _as_mapping(normalized.get("arguments"))
        if not arguments:
            arguments = _build_tool_arguments(tool_name, normalized)
        if not tool_name:
            fallback_text = _extract_textish(normalized)
            tool_name, arguments = _infer_tool_request_from_text(fallback_text)
        if not tool_name:
            return None
        arguments = _apply_tool_argument_defaults(tool_name, arguments)
        request = {
            "tool_name": tool_name,
            "arguments": arguments,
        }
        request_id = _extract_textish(normalized, ("request_id",))
        if request_id:
            request["request_id"] = request_id
        return request

    tool_name, arguments = _infer_tool_request_from_text(str(item or "").strip())
    if not tool_name:
        return None
    arguments = _apply_tool_argument_defaults(tool_name, arguments)
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "request_id": f"tool_{index:03d}",
    }


def _normalize_tool_requests(payload: Any) -> list[dict[str, Any]]:
    return _normalize_record_list(payload, _normalize_tool_request)


def _normalize_directive_updates(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        values = _normalize_string_list(payload)
        return {"must_follow": values} if values else {}

    normalized = _as_mapping(payload)
    aliases = {
        "must_follow": ("must_follow", "requirements", "instructions", "hard_rules"),
        "must_preserve": ("must_preserve", "preserve", "keep"),
        "preferences": ("preferences", "preference", "style_preferences"),
        "rejected_patterns": ("rejected_patterns", "avoid", "taboos", "banned_patterns"),
        "confirmed_structure": ("confirmed_structure", "structure", "approved_structure"),
        "open_issues": ("open_issues", "issues", "gaps"),
    }
    result: dict[str, Any] = {}
    for target_key, source_keys in aliases.items():
        source_value = None
        for source_key in source_keys:
            if source_key in normalized:
                source_value = normalized.get(source_key)
                break
        values = _normalize_string_list(source_value)
        if values:
            result[target_key] = values
    return result


def _normalize_evidence_updates(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        facts = _normalize_record_list(payload, _normalize_text_record)
        return {"facts": facts} if facts else {}

    normalized = _as_mapping(payload)
    result: dict[str, Any] = {}
    record_fields = {
        "facts": ("facts", "fact_list"),
        "data_points": ("data_points", "data", "figures", "numbers"),
        "cases": ("cases", "examples"),
        "problem_list": ("problem_list", "problems", "issues"),
        "measure_handles": ("measure_handles", "measures", "handles"),
    }
    for field_name, aliases in record_fields.items():
        source_value = None
        for alias in aliases:
            if alias in normalized:
                source_value = normalized.get(alias)
                break
        records = _normalize_record_list(source_value, _normalize_text_record)
        if records:
            result[field_name] = records

    usable_phrases = _normalize_string_list(
        normalized.get("usable_phrases", normalized.get("phrases"))
    )
    if usable_phrases:
        result["usable_phrases"] = usable_phrases

    slot_mapping = _normalize_slot_mapping(
        normalized.get("slot_mapping", normalized.get("slots"))
    )
    if slot_mapping:
        result["slot_mapping"] = slot_mapping

    gaps = _normalize_string_list(normalized.get("gaps", normalized.get("open_gaps")))
    if gaps:
        result["gaps"] = gaps

    if not result:
        fallback_text = _extract_textish(normalized)
        if fallback_text:
            result["facts"] = [{"content": fallback_text}]
    return result


def _normalize_self_review_payload(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        items = _normalize_string_list(payload)
        if not items:
            return {}
        return {
            "dominant_issue": items[0],
            "notes": items[1:],
        }

    normalized = _as_mapping(payload)
    return {
        "responded_directives": _normalize_string_list(
            normalized.get(
                "responded_directives",
                normalized.get("handled_directives", normalized.get("directives")),
            )
        ),
        "dominant_issue": _extract_textish(
            normalized,
            ("dominant_issue", "largest_risk", "main_issue", "issue"),
        ),
        "open_gaps": _normalize_string_list(
            normalized.get("open_gaps", normalized.get("missing_evidence", normalized.get("gaps")))
        ),
        "content_status_summary": _extract_textish(
            normalized,
            ("content_status_summary", "content_summary", "summary"),
        ),
        "language_status_summary": _extract_textish(
            normalized,
            ("language_status_summary", "language_summary", "style_summary"),
        ),
        "notes": _normalize_string_list(
            normalized.get("notes", normalized.get("observations", normalized.get("remarks")))
        ),
    }


@dataclass(slots=True)
class BrainStepResult(JsonDataclassMixin):
    action_taken: str
    action_payload: JsonDataclassMixin | None = None
    workspace_patch: WorkspacePatch = field(default_factory=WorkspacePatch)
    self_review: SelfReview = field(default_factory=SelfReview)

    def __post_init__(self) -> None:
        if self.action_taken not in VALID_ACTIONS:
            raise ValueError(f"Unsupported action_taken: {self.action_taken}")

        self.action_payload = _coerce_action_payload(self.action_taken, self.action_payload)
        self.workspace_patch = (
            self.workspace_patch
            if isinstance(self.workspace_patch, WorkspacePatch)
            else WorkspacePatch.from_dict(self.workspace_patch)
        )
        if self.action_taken != "build_outline" and self.workspace_patch.outline_update:
            self.workspace_patch.outline_update = {}
        self.self_review = (
            self.self_review
            if isinstance(self.self_review, SelfReview)
            else SelfReview()
            if self.self_review is None
            else SelfReview.from_dict(self.self_review)
        )
        if self.action_taken in CONTROL_ONLY_ACTIONS:
            self.workspace_patch = WorkspacePatch()
            self.self_review = SelfReview()
        self.validate()

    def to_dict(self) -> dict[str, Any]:
        action_payload: dict[str, Any] = {}
        if self.action_payload is not None:
            if hasattr(self.action_payload, "to_dict"):
                serialized_payload = self.action_payload.to_dict()
            elif isinstance(self.action_payload, Mapping):
                serialized_payload = dict(self.action_payload)
            else:
                serialized_payload = {}
            action_payload = {self.action_taken: serialized_payload}
        result = {
            "action_taken": self.action_taken,
            "action_payload": action_payload,
        }
        if _workspace_patch_has_updates(self.workspace_patch):
            result["workspace_patch"] = self.workspace_patch.to_dict()
        if _self_review_has_updates(self.self_review):
            result["self_review"] = self.self_review.to_dict()
        return result

    @property
    def done(self) -> bool:
        return self.action_taken == "finalize"

    @property
    def ask_user(self) -> bool:
        return self.action_taken == "ask_user"

    @property
    def has_self_review(self) -> bool:
        return _self_review_has_updates(self.self_review)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | "BrainStepResult",
    ) -> "BrainStepResult":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("BrainStepResult.from_dict expects a mapping payload.")

        normalized = _normalize_brain_step_payload(payload)
        return cls(
            action_taken=str(normalized.get("action_taken", "")),
            action_payload=normalized.get("action_payload"),
            workspace_patch=normalized.get("workspace_patch", {}),
            self_review=normalized.get("self_review", {}),
        )

    def validate(self) -> None:
        if self.action_taken == "load_skill":
            if not _has_text(getattr(self.action_payload, "primary_skill_id", "")):
                raise ValueError("load_skill requires primary_skill_id.")
            if len(_as_list(getattr(self.action_payload, "revision_skill_ids", []))) > 2:
                raise ValueError("load_skill allows at most 2 revision_skill_ids.")
            return

        if self.action_taken == "ask_user":
            if not _has_items(getattr(self.action_payload, "question_pack", [])):
                raise ValueError("ask_user must produce question_pack.")
            return

        if self.action_taken == "build_outline":
            outline_text = getattr(self.action_payload, "outline_text", "")
            outline_sections = getattr(self.action_payload, "outline_sections", [])
            if not _has_text(outline_text) and not _has_items(outline_sections):
                raise ValueError("build_outline must produce outline_text or outline_sections.")
            return

        if self.workspace_patch.outline_update:
            raise ValueError(f"{self.action_taken} cannot emit outline_update.")

        if self.action_taken == "write_draft":
            if not _has_text(_extract_action_main_text(self.action_payload)):
                raise ValueError("write_draft must produce main text.")
            return

        if self.action_taken == "write_section":
            if not _has_text(getattr(self.action_payload, "section_id", "")):
                raise ValueError("write_section must produce section_id.")
            if not _has_text(getattr(self.action_payload, "section_text", "")):
                raise ValueError("write_section must produce section_text.")
            return

        if self.action_taken == "revise_draft":
            if not _has_text(_extract_action_main_text(self.action_payload)):
                raise ValueError("revise_draft must produce main text.")
            return

        if self.action_taken == "polish_language":
            if not _has_text(_extract_action_main_text(self.action_payload)):
                raise ValueError("polish_language must produce main text.")
            return

        if self.action_taken == "finalize" and not _has_text(
            _extract_action_main_text(self.action_payload)
        ):
            raise ValueError("finalize must produce main text.")


def _coerce_action_payload(
    action_taken: str,
    payload: Any,
) -> JsonDataclassMixin:
    payload_cls = ACTION_PAYLOAD_TYPES[action_taken]
    if payload is None:
        return payload_cls()
    if isinstance(payload, payload_cls):
        return payload
    return payload_cls.from_dict(_normalize_action_payload_value(action_taken, payload))


def _normalize_brain_step_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    action_taken = str(normalized.get("action_taken", "") or "").strip()
    if not action_taken:
        if bool(normalized.get("done", False)):
            action_taken = "finalize"
        elif bool(normalized.get("ask_user", False)):
            action_taken = "ask_user"
    normalized["action_taken"] = action_taken

    raw_workspace_patch = _as_mapping(normalized.get("workspace_patch"))
    workspace_patch = _normalize_workspace_patch_mapping(raw_workspace_patch, action_taken=action_taken)
    action_payload = normalized.get("action_payload")
    if isinstance(action_payload, Mapping):
        action_payload = _unwrap_nested_action_payload(action_taken, action_payload)

    if action_taken == "load_skill" and action_payload is None:
        action_payload = normalized.get("skill_request")
    elif action_taken == "ask_user" and action_payload is None:
        action_payload = normalized.get(
            "question_pack",
            normalized.get("questions"),
        )

    normalized["action_payload"] = _normalize_action_payload_value(
        action_taken,
        action_payload,
        raw_workspace_patch,
    )
    normalized["workspace_patch"] = {} if action_taken in CONTROL_ONLY_ACTIONS else workspace_patch
    normalized["self_review"] = (
        {}
        if action_taken in CONTROL_ONLY_ACTIONS
        else _normalize_self_review_payload(normalized.get("self_review"))
    )
    return normalized


def _unwrap_nested_action_payload(
    action_taken: str,
    payload: Mapping[str, Any],
) -> Any:
    if action_taken and action_taken in payload:
        return payload.get(action_taken)
    return dict(payload)


def _normalize_action_payload_value(
    action_taken: str,
    payload: Any,
    workspace_patch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_workspace_patch = _as_mapping(workspace_patch)

    if action_taken == "load_skill":
        return _normalize_skill_request_payload(payload) or {}
    if action_taken == "ask_user":
        if isinstance(payload, Mapping):
            return _normalize_action_payload_mapping("ask_user", payload)
        return {"question_pack": _normalize_question_pack(payload)}
    if action_taken == "build_outline":
        return _normalize_build_outline_action_payload(payload, raw_workspace_patch)
    if action_taken in DRAFT_ACTIONS:
        return _normalize_draft_action_payload(action_taken, payload, raw_workspace_patch)
    if isinstance(payload, Mapping):
        return _normalize_action_payload_mapping(action_taken, payload)
    return {}


def _normalize_workspace_patch_mapping(
    payload: Any,
    *,
    action_taken: str = "",
) -> dict[str, Any]:
    if action_taken in CONTROL_ONLY_ACTIONS:
        return {}
    raw = _as_mapping(payload)
    normalized = {
        "directive_updates": _normalize_directive_updates(
            raw.get("directive_updates", raw.get("directive_update"))
        ),
        "evidence_updates": _normalize_evidence_updates(
            raw.get("evidence_updates", raw.get("evidence_update"))
        ),
        "outline_update": _normalize_outline_update(
            raw.get("outline_update", raw.get("outline_updates"))
        ),
        "revision_history_updates": _normalize_revision_history_updates(
            raw.get(
                "revision_history_updates",
                raw.get("revision_intent_updates", raw.get("revision_intents")),
            )
        ),
    }
    if action_taken != "build_outline":
        normalized["outline_update"] = {}
    return {
        key: value
        for key, value in normalized.items()
        if value not in ({}, [])
    }


def _normalize_revision_history_updates(payload: Any) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    for item in _as_list(payload):
        if isinstance(item, Mapping):
            normalized = _as_mapping(item)
            revision_id = _extract_textish(
                normalized,
                ("revision_id", "intent_id", "history_id", "id"),
            )
            if revision_id:
                normalized["revision_id"] = revision_id
            if not str(normalized.get("source", "") or "").strip():
                normalized["source"] = "editorial_brain"
            action_taken = _extract_textish(
                normalized,
                ("action_taken", "action", "step_action"),
            )
            if action_taken:
                normalized["action_taken"] = action_taken
            summary = _extract_textish(
                normalized,
                ("summary", "goal", "description", "text", "content"),
            )
            if summary:
                normalized["summary"] = summary
            normalized["focus"] = _normalize_string_list(
                normalized.get(
                    "focus",
                    normalized.get("focuses", normalized.get("focus_points")),
                )
            )
            normalized["target_sections"] = _normalize_string_list(
                normalized.get("target_sections", normalized.get("sections"))
            )
            normalized["before_word_count"] = _normalize_int(
                normalized.get("before_word_count", normalized.get("before_words"))
            )
            normalized["after_word_count"] = _normalize_int(
                normalized.get("after_word_count", normalized.get("after_words"))
            )
            normalized["notes"] = _normalize_string_list(
                normalized.get("notes", normalized.get("remarks"))
            )
            normalized_items.append(normalized)
            continue
        summary = str(item or "").strip()
        if summary:
            normalized_items.append(
                {
                    "source": "editorial_brain",
                    "summary": summary,
                    "focus": [],
                    "target_sections": [],
                    "before_word_count": 0,
                    "after_word_count": 0,
                    "notes": [],
                }
            )
    return normalized_items
def _normalize_outline_update(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        text = payload.strip()
        return {"outline_text": text} if text else {}
    if not isinstance(payload, Mapping):
        return {}
    normalized = _as_mapping(payload)
    if "sections" not in normalized and "outline_sections" in normalized:
        normalized["sections"] = normalized.get("outline_sections")
    if "sections" not in normalized and "section_titles" in normalized:
        normalized["sections"] = normalized.get("section_titles")
    if "outline_text" not in normalized:
        for alias in ("text", "content"):
            if alias in normalized:
                normalized["outline_text"] = str(normalized.get(alias, "") or "")
                break
    if "open_gaps" not in normalized and "open_outline_risks" in normalized:
        normalized["open_gaps"] = normalized.get("open_outline_risks")
    result = {
        "title": str(normalized.get("title", "") or "").strip(),
        "global_objective": str(
            normalized.get("global_objective", normalized.get("objective", "")) or ""
        ).strip(),
        "outline_text": str(normalized.get("outline_text", "") or "").strip(),
        "sections": _normalize_outline_sections(normalized.get("sections")),
        "open_gaps": _normalize_string_list(normalized.get("open_gaps")),
    }
    return {
        key: value
        for key, value in result.items()
        if value not in ("", [], {})
    }


def _normalize_build_outline_action_payload(
    action_payload: Any,
    workspace_patch: Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(action_payload, Mapping):
        return _normalize_action_payload_mapping("build_outline", action_payload)

    outline_text = _coerce_text_blob(action_payload)
    if outline_text:
        return {"outline_text": outline_text, "outline_sections": []}

    outline_sections = _normalize_outline_sections(action_payload)
    if outline_sections:
        return {"outline_text": "", "outline_sections": outline_sections}

    outline_update = _normalize_outline_update(workspace_patch.get("outline_update"))
    if not outline_update:
        return {}
    return {
        "outline_text": str(outline_update.get("outline_text", "") or ""),
        "outline_sections": _normalize_outline_sections(outline_update.get("sections")),
    }


def _normalize_draft_action_payload(
    action_taken: str,
    action_payload: Any,
    workspace_patch: Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(action_payload, Mapping):
        return _normalize_action_payload_mapping(action_taken, action_payload)

    direct_text = _coerce_text_blob(action_payload)
    if direct_text:
        if action_taken == "write_draft":
            return {"draft_text": direct_text}
        if action_taken == "write_section":
            return {"section_id": "section_001", "section_text": direct_text}
        if action_taken == "revise_draft":
            return {"revised_text": direct_text}
        if action_taken == "polish_language":
            return {"polished_text": direct_text}
        if action_taken == "finalize":
            return {"final_text": direct_text}

    compatible_draft = _as_mapping(workspace_patch.get("draft_update"))
    text = _extract_compatible_draft_text(compatible_draft)
    if action_taken == "write_draft":
        return {"draft_text": text}
    if action_taken == "write_section":
        section_map = _as_mapping(compatible_draft.get("section_map"))
        section_id = ""
        section_text = ""
        if len(section_map) == 1:
            section_id, section_text = next(iter(section_map.items()))
        return {"section_id": str(section_id or ""), "section_text": str(section_text or "")}
    if action_taken == "revise_draft":
        return {"revised_text": text}
    if action_taken == "polish_language":
        return {"polished_text": text}
    if action_taken == "finalize":
        return {"final_text": text}
    return {}


def _extract_compatible_draft_text(payload: Mapping[str, Any]) -> str:
    text = _extract_textish(
        payload,
        (
            "full_text",
            "draft_text",
            "revised_text",
            "polished_text",
            "final_text",
            "main_text",
            "body",
            "text",
            "content",
        ),
    )
    if text:
        return text

    for nested_key in ("draft", "draft_artifact", "document", "artifact", "body"):
        nested_payload = _as_mapping(payload.get(nested_key))
        if not nested_payload:
            continue
        nested_text = _extract_textish(
            nested_payload,
            (
                "full_text",
                "draft_text",
                "revised_text",
                "polished_text",
                "final_text",
                "main_text",
                "body",
                "text",
                "content",
            ),
        )
        if nested_text:
            return nested_text
    return ""


def _normalize_action_payload_mapping(
    action_taken: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _as_mapping(payload)
    if action_taken == "load_skill":
        normalized_skill = _normalize_skill_request_payload(normalized) or {}
        return dict(normalized_skill)
    if action_taken == "build_outline":
        if "outline_sections" not in normalized and "sections" in normalized:
            normalized["outline_sections"] = normalized.get("sections")
        if "outline_sections" not in normalized and "section_titles" in normalized:
            normalized["outline_sections"] = normalized.get("section_titles")
        return {
            "outline_text": _extract_textish(
                normalized,
                ("outline_text", "text", "content", "outline"),
            ),
            "outline_sections": _normalize_outline_sections(normalized.get("outline_sections")),
        }
    if action_taken == "write_draft":
        return {"draft_text": _extract_compatible_draft_text(normalized)}
    if action_taken == "write_section":
        section_id = _extract_textish(
            normalized,
            ("section_id", "target_section", "target_section_id", "id"),
        ) or "section_001"
        section_text = _extract_textish(
            normalized,
            ("section_text", "text", "content"),
        )
        return {
            "section_id": section_id,
            "section_text": section_text,
        }
    if action_taken == "revise_draft":
        return {"revised_text": _extract_compatible_draft_text(normalized)}
    if action_taken == "polish_language":
        return {"polished_text": _extract_compatible_draft_text(normalized)}
    if action_taken == "ask_user":
        return {
            "question_pack": _normalize_question_pack(
                normalized.get(
                    "question_pack",
                    normalized.get("questions", normalized.get("pending_questions")),
                )
            )
        }
    if action_taken == "finalize":
        return {"final_text": _extract_compatible_draft_text(normalized)}
    return normalized


def _normalize_skill_request_payload(
    payload: Mapping[str, Any] | list[Any] | str | None,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        normalized = _as_mapping(payload)
        primary_skill_id = _normalize_skill_id_value(
            _extract_textish(
                normalized,
                ("primary_skill_id", "primary_skill", "skill_id", "selected_skill_id"),
            ),
            preferred_kind="primary",
        )
        raw_revision_skill_ids = _normalize_string_list(
            normalized.get("revision_skill_ids", normalized.get("revision_skills")),
            item_aliases=("skill_id", "id", "name", "value", "content", "text"),
        )
        revision_skill_ids = [
            _normalize_skill_id_value(skill_id, preferred_kind="revision")
            for skill_id in raw_revision_skill_ids
        ]
        raw_skill_ids = _normalize_string_list(
            normalized.get("skill_ids", normalized.get("skills")),
            item_aliases=("skill_id", "id", "name", "value", "content", "text"),
        )
        skill_ids = _normalize_skill_id_list(raw_skill_ids)

        if not primary_skill_id:
            for skill_id in skill_ids:
                if skill_id.startswith("primary."):
                    primary_skill_id = skill_id
                    break
            if not primary_skill_id and skill_ids:
                primary_skill_id = skill_ids[0]

        if skill_ids:
            for skill_id in skill_ids:
                if skill_id == primary_skill_id:
                    continue
                if skill_id.startswith("primary.") and not primary_skill_id:
                    primary_skill_id = skill_id
                    continue
                if skill_id not in revision_skill_ids:
                    revision_skill_ids.append(skill_id)

        revision_skill_ids = [
            skill_id
            for skill_id in revision_skill_ids
            if skill_id and skill_id != primary_skill_id
        ][:2]

        return {
            "primary_skill_id": primary_skill_id,
            "revision_skill_ids": revision_skill_ids,
        }
    if isinstance(payload, str):
        skill_id = _normalize_skill_id_value(payload, preferred_kind="primary")
        if not skill_id:
            return None
        return {"primary_skill_id": skill_id, "revision_skill_ids": []}
    skill_ids = _normalize_skill_id_list(
        _normalize_string_list(
            payload,
            item_aliases=("skill_id", "id", "name", "value", "content", "text"),
        )
    )
    if not skill_ids:
        return None
    primary_skill_id = ""
    revision_skill_ids: list[str] = []
    for skill_id in skill_ids:
        if skill_id.startswith("primary.") and not primary_skill_id:
            primary_skill_id = skill_id
            continue
        if skill_id not in revision_skill_ids:
            revision_skill_ids.append(skill_id)
    if not primary_skill_id:
        primary_skill_id = skill_ids[0]
        revision_skill_ids = [item for item in skill_ids[1:] if item != primary_skill_id]
    return {
        "primary_skill_id": primary_skill_id,
        "revision_skill_ids": revision_skill_ids[:2],
    }


def _normalize_skill_id_value(skill_id: Any, *, preferred_kind: str | None = None) -> str:
    normalized = str(skill_id or "").strip()
    if not normalized:
        return ""
    if normalized.startswith(("primary.", "revision.")):
        return normalized
    if preferred_kind in {"primary", "revision"}:
        return preferred_kind + "." + normalized
    return normalized


def _normalize_skill_id_list(skill_ids: list[str]) -> list[str]:
    normalized_skill_ids: list[str] = []
    for index, skill_id in enumerate(skill_ids):
        preferred_kind = None
        if not str(skill_id or "").strip().startswith(("primary.", "revision.")):
            preferred_kind = "primary" if index == 0 else "revision"
        normalized_value = _normalize_skill_id_value(skill_id, preferred_kind=preferred_kind)
        if normalized_value:
            normalized_skill_ids.append(normalized_value)
    return normalized_skill_ids


def _workspace_patch_has_updates(patch: WorkspacePatch) -> bool:
    return any(value not in ({}, []) for value in patch.to_dict().values())


def _self_review_has_updates(review: SelfReview) -> bool:
    return bool(
        review.responded_directives
        or _has_text(review.dominant_issue)
        or review.open_gaps
        or _has_text(review.content_status_summary)
        or _has_text(review.language_status_summary)
        or review.notes
    )
