from __future__ import annotations

from typing import Any

from agents_runtime import AgentsSdkBrainRunner, UnconfiguredAgentsSdkBrainRunner
from config import AppConfig


def build_brain_runner(
    *,
    config: AppConfig,
) -> Any:
    missing: list[str] = []
    if not config.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if not config.openai_model:
        missing.append("OPENAI_MODEL")
    if missing:
        return UnconfiguredAgentsSdkBrainRunner(
            reason="Agents SDK runtime is not configured for run_turn. Missing: "
            + ", ".join(missing),
            model=config.openai_model or "editorial-brain",
        )
    return AgentsSdkBrainRunner.from_config(config)
