from __future__ import annotations

from typing import Any

from agents_runtime import AgentsSdkBrainRunner, UnconfiguredAgentsSdkBrainRunner
from config import AppConfig


def build_brain_runner(
    *,
    config: AppConfig,
) -> Any:
    missing: list[str] = []
    if not config.litellm_model:
        missing.append("LITELLM_MODEL")
    if missing:
        return UnconfiguredAgentsSdkBrainRunner(
            reason="LiteLLM workflow is not configured for run_turn. Missing: "
            + ", ".join(missing),
            model=config.litellm_model or "editorial-brain",
        )
    return AgentsSdkBrainRunner.from_config(config)
