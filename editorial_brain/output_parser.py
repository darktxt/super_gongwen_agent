from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .contracts_core import BrainStepResult
from .runtime_contracts import LLMResponse


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
            for normalized_candidate in self._candidate_normalizations(candidate):
                try:
                    parsed = json.loads(normalized_candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, Mapping):
                    return BrainStepResult.from_dict(parsed)

        snippet = raw_output[:300].replace("\n", "\\n")
        raise OutputParseError(f"Failed to parse BrainStepResult from output: {snippet}")

    def _candidate_json_strings(self, text: str) -> list[str]:
        candidates: list[str] = []
        stripped = text.strip()
        if stripped:
            candidates.append(stripped)

        fenced_matches = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        candidates.extend(fenced_matches)

        generic_fenced = re.findall(r"```\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        candidates.extend(generic_fenced)

        candidates.extend(self._extract_balanced_objects(text))

        unique: list[str] = []
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized and normalized not in unique:
                unique.append(normalized)
        return unique

    def _candidate_normalizations(self, candidate: str) -> list[str]:
        options: list[str] = []
        stripped = candidate.strip()
        if stripped:
            options.append(stripped)

        without_think = re.sub(
            r"<think>.*?</think>",
            "",
            stripped,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if without_think and without_think not in options:
            options.append(without_think)

        repaired = self._repair_common_json_issues(without_think or stripped)
        if repaired and repaired not in options:
            options.append(repaired)

        return options

    def _repair_common_json_issues(self, text: str) -> str:
        if not text:
            return text

        # Remove markdown fences if they leaked into the candidate text.
        text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"^```\s*", "", text).strip()
        text = re.sub(r"\s*```$", "", text).strip()

        chars: list[str] = []
        in_string = False
        escaped = False
        length = len(text)
        index = 0

        while index < length:
            char = text[index]
            if not in_string:
                chars.append(char)
                if char == '"':
                    in_string = True
                    escaped = False
                index += 1
                continue

            if escaped:
                chars.append(char)
                escaped = False
                index += 1
                continue

            if char == "\\":
                chars.append(char)
                escaped = True
                index += 1
                continue

            if char == '"':
                next_significant = self._next_significant_char(text, index + 1)
                if next_significant not in {",", "}", "]", ":"}:
                    chars.append('\\"')
                else:
                    chars.append(char)
                    in_string = False
                index += 1
                continue

            chars.append(char)
            index += 1

        return "".join(chars)

    def _next_significant_char(self, text: str, start: int) -> str | None:
        for index in range(start, len(text)):
            char = text[index]
            if not char.isspace():
                return char
        return None

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
                continue

            if char == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    results.append(text[start : index + 1])
                    start = None

        return results
