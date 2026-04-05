from __future__ import annotations

from dataclasses import dataclass

from api_gateway.llm_client import LLMClient, LLMRequest, LLMResponse

from .context_compiler import CompiledBrainContext
from .contracts_core import BrainStepResult
from .output_parser import OutputParser


@dataclass(slots=True)
class BrainRunResult:
    request: LLMRequest
    response: LLMResponse
    step: BrainStepResult


@dataclass(slots=True)
class BrainRunError(RuntimeError):
    message: str
    request: LLMRequest
    response: LLMResponse
    raw_output: str

    def __str__(self) -> str:
        return self.message


class BrainRunner:
    def __init__(
        self,
        llm_client: LLMClient,
        *,
        model: str = "editorial-brain",
        output_parser: OutputParser | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model = model
        self.output_parser = output_parser or OutputParser()

    def run(self, compiled_context: CompiledBrainContext) -> BrainRunResult:
        request = LLMRequest(
            model=self.model,
            system_prompt=compiled_context.system_prompt,
            user_prompt=compiled_context.user_prompt,
            context_blocks=[compiled_context.action_playbook_block.to_dict(), compiled_context.skill_listing_block.to_dict()]
            + [block.to_dict() for block in compiled_context.active_skill_blocks]
            + [block.to_dict() for block in compiled_context.attached_context_blocks],
            metadata={
                "token_budget_report": compiled_context.token_budget_report.to_dict(),
            },
        )
        response = self.llm_client.invoke(request)
        try:
            step = self.output_parser.parse(response)
        except Exception as exc:
            raise BrainRunError(
                message=str(exc),
                request=request,
                response=response,
                raw_output=response.content,
            ) from exc
        return BrainRunResult(request=request, response=response, step=step)
