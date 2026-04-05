from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_APP_HOME_NAME = ".super_gongwen"


@dataclass(slots=True, frozen=True)
class AppConfig:
    app_home: Path
    sessions_root: Path
    default_encoding: str = "utf-8"
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    openai_timeout: float = 300.0
    openai_temperature: float | None = None


def default_openai_timeout() -> float:
    return float(AppConfig.__dataclass_fields__["openai_timeout"].default)


def resolve_app_home(base_dir: str | Path | None = None) -> Path:
    env_home = os.getenv("SUPER_GONGWEN_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    root = Path(base_dir) if base_dir is not None else Path.cwd()
    return (root / DEFAULT_APP_HOME_NAME).resolve()


def load_config(base_dir: str | Path | None = None) -> AppConfig:
    app_home = resolve_app_home(base_dir=base_dir)
    sessions_root = app_home / "sessions"
    return AppConfig(
        app_home=app_home,
        sessions_root=sessions_root,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "").strip(),
        openai_timeout=_read_float_env("OPENAI_TIMEOUT", default_openai_timeout()),
        openai_temperature=_read_optional_float_env("OPENAI_TEMPERATURE"),
    )


def _read_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def _read_optional_float_env(name: str) -> float | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    try:
        return float(raw_value)
    except ValueError:
        return None
