from __future__ import annotations

import os
from pathlib import Path

from config import PROJECT_ROOT


GUI_CONFIG_KEYS = (
    "LITELLM_MODEL",
    "LITELLM_API_KEY",
    "LITELLM_BASE_URL",
)


def project_dotenv_path() -> Path:
    return PROJECT_ROOT / ".env"


def read_gui_config_values(dotenv_path: Path | None = None) -> dict[str, str]:
    path = dotenv_path or project_dotenv_path()
    values = {key: "" for key in GUI_CONFIG_KEYS}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip().lstrip("\ufeff")
        if normalized_key not in values:
            continue
        normalized_value = value.strip()
        if (
            len(normalized_value) >= 2
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {'"', "'"}
        ):
            normalized_value = normalized_value[1:-1]
        values[normalized_key] = normalized_value
    return values


def write_gui_config_values(
    values: dict[str, str],
    dotenv_path: Path | None = None,
) -> Path:
    path = dotenv_path or project_dotenv_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    normalized_updates = {
        key: str(values.get(key, "") or "").strip() for key in GUI_CONFIG_KEYS
    }
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated_lines: list[str] = []
    seen_keys: set[str] = set()

    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            updated_lines.append(raw_line)
            continue

        key, _ = raw_line.split("=", 1)
        normalized_key = key.strip().lstrip("\ufeff")
        if normalized_key not in normalized_updates:
            updated_lines.append(raw_line)
            continue

        updated_lines.append(f"{normalized_key}={normalized_updates[normalized_key]}")
        seen_keys.add(normalized_key)

    for key in GUI_CONFIG_KEYS:
        if key in seen_keys:
            continue
        updated_lines.append(f"{key}={normalized_updates[key]}")

    content = "\n".join(updated_lines).rstrip() + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def apply_gui_config_to_environment(values: dict[str, str]) -> None:
    for key in GUI_CONFIG_KEYS:
        normalized_value = str(values.get(key, "") or "").strip()
        if normalized_value:
            os.environ[key] = normalized_value
            continue
        os.environ.pop(key, None)
