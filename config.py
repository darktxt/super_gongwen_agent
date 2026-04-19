from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
DEFAULT_APP_HOME_NAME = ".super_gongwen"
PROJECT_ROOT = Path(__file__).resolve().parent
@dataclass(slots=True, frozen=True)
class AppConfig:
    app_home: Path
    sessions_root: Path
    litellm_api_key: str = ""
    litellm_base_url: str = ""
    litellm_model: str = ""
    litellm_temperature: float | None = None
    openai_agents_enable_tracing: bool = True
def resolve_app_home(base_dir: str | Path | None = None) -> Path:
    env_home = os.getenv("SUPER_GONGWEN_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    root = Path(base_dir).expanduser().resolve() if base_dir is not None else Path.cwd().resolve()
    return root / DEFAULT_APP_HOME_NAME
def load_config(base_dir: str | Path | None = None) -> AppConfig:
    _load_env_file(base_dir=base_dir)
    app_home = resolve_app_home(base_dir=base_dir)
    return AppConfig(
        app_home=app_home,
        sessions_root=app_home / "sessions",
        litellm_api_key=os.getenv("LITELLM_API_KEY", "").strip(),
        litellm_base_url=os.getenv("LITELLM_BASE_URL", "").strip(),
        litellm_model=os.getenv("LITELLM_MODEL", "").strip(),
        litellm_temperature=_read_optional_float("LITELLM_TEMPERATURE"),
        openai_agents_enable_tracing=_read_bool("OPENAI_AGENTS_ENABLE_TRACING", True),
    )
def _load_env_file(base_dir: str | Path | None = None) -> None:
    candidates = []
    if base_dir is not None:
        candidates.append(Path(base_dir).expanduser().resolve() / ".env")
    candidates.append(PROJECT_ROOT / ".env")
    candidates.append(Path.cwd().resolve() / ".env")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or os.getenv(key):
                continue
            os.environ[key] = value.strip().strip("'").strip('"')
        return
def _read_optional_float(name: str) -> float | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    try:
        return float(raw_value)
    except ValueError:
        return None
def _read_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}
