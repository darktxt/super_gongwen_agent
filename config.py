from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

try:
    from dotenv import dotenv_values as _dotenv_values
except ImportError:  # pragma: no cover - dependency presence is environment-specific
    _dotenv_values = None


DEFAULT_APP_HOME_NAME = ".super_gongwen"
PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(slots=True, frozen=True)
class AppConfig:
    app_home: Path
    sessions_root: Path
    default_encoding: str = "utf-8"
    openai_agents_enable_tracing: bool = True
    openai_agents_output_mode: str = "auto"
    litellm_api_key: str = ""
    litellm_base_url: str = ""
    litellm_model: str = ""
    litellm_timeout: float = 300.0
    litellm_temperature: float | None = None

    @property
    def openai_api_key(self) -> str:
        return self.litellm_api_key

    @property
    def openai_base_url(self) -> str:
        return self.litellm_base_url

    @property
    def openai_model(self) -> str:
        return self.litellm_model

    @property
    def openai_timeout(self) -> float:
        return self.litellm_timeout

    @property
    def openai_temperature(self) -> float | None:
        return self.litellm_temperature


def default_litellm_timeout() -> float:
    return float(AppConfig.__dataclass_fields__["litellm_timeout"].default)


def resolve_app_home(base_dir: str | Path | None = None) -> Path:
    env_home = os.getenv("SUPER_GONGWEN_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    root = Path(base_dir) if base_dir is not None else Path.cwd()
    return (root / DEFAULT_APP_HOME_NAME).resolve()


def load_config(base_dir: str | Path | None = None) -> AppConfig:
    _load_local_dotenv(base_dir=base_dir)
    app_home = resolve_app_home(base_dir=base_dir)
    sessions_root = app_home / "sessions"
    return AppConfig(
        app_home=app_home,
        sessions_root=sessions_root,
        openai_agents_enable_tracing=_read_bool_env(
            "OPENAI_AGENTS_ENABLE_TRACING",
            default=True,
        ),
        openai_agents_output_mode=_read_agents_output_mode_env(
            "OPENAI_AGENTS_OUTPUT_MODE",
            default="auto",
        ),
        litellm_api_key=_read_first_text_env("LITELLM_API_KEY", "OPENAI_API_KEY"),
        litellm_base_url=os.getenv("LITELLM_BASE_URL", "").strip(),
        litellm_model=_read_first_text_env("LITELLM_MODEL", "OPENAI_MODEL"),
        litellm_timeout=_read_first_float_env(
            ("LITELLM_TIMEOUT", "OPENAI_TIMEOUT"),
            default_litellm_timeout(),
        ),
        litellm_temperature=_read_first_optional_float_env(
            "LITELLM_TEMPERATURE",
            "OPENAI_TEMPERATURE",
        ),
    )


def _load_local_dotenv(base_dir: str | Path | None = None) -> None:
    for dotenv_path in _candidate_dotenv_paths(base_dir=base_dir):
        if dotenv_path.exists():
            _load_dotenv_file(dotenv_path)
            break


def _candidate_dotenv_paths(base_dir: str | Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if base_dir is not None:
        candidates.append(Path(base_dir).expanduser().resolve() / ".env")
    candidates.append(PROJECT_ROOT / ".env")
    candidates.append(Path.cwd().resolve() / ".env")

    deduplicated: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in deduplicated:
            deduplicated.append(resolved)
    return deduplicated


def _load_dotenv_file(dotenv_path: Path) -> None:
    if _dotenv_values is not None:
        for key, value in _dotenv_values(dotenv_path).items():
            normalized_key = str(key or "").strip().lstrip("\ufeff")
            if not normalized_key:
                continue
            current_value = os.environ.get(normalized_key, "")
            if str(current_value).strip():
                continue
            os.environ[normalized_key] = str(value or "").strip()
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        if not key:
            continue
        current_value = os.environ.get(key, "")
        if str(current_value).strip():
            continue
        normalized = value.strip()
        if (
            len(normalized) >= 2
            and normalized[0] == normalized[-1]
            and normalized[0] in {'"', "'"}
        ):
            normalized = normalized[1:-1]
        os.environ[key] = normalized


def _read_first_text_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _read_first_float_env(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw_value = os.getenv(name, "").strip()
        if not raw_value:
            continue
        try:
            return float(raw_value)
        except ValueError:
            continue
    return default


def _read_first_optional_float_env(*names: str) -> float | None:
    for name in names:
        raw_value = os.getenv(name, "").strip()
        if not raw_value:
            continue
        try:
            return float(raw_value)
        except ValueError:
            continue
    return None


def _read_bool_env(name: str, *, default: bool) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    if raw_value in {"1", "true", "yes", "y", "on"}:
        return True
    if raw_value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _read_agents_output_mode_env(name: str, *, default: str) -> str:
    raw_value = os.getenv(name, "").strip().lower()
    if raw_value in {"auto", "structured", "text"}:
        return raw_value
    return default
