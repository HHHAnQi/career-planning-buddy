"""Provider-neutral evidence-bounded resume optimization generation."""

import json
from collections.abc import Mapping
from typing import Protocol

from app.core.config import Settings
from app.prompts.resume_optimization import resume_optimization_messages
from app.providers.llm_client import LLMClient
from app.providers.llm_contracts import LLMRequest
from app.providers.llm_profiles import model_for_operation
from app.schemas.agent_runs import ProviderUsage
from app.schemas.resumes import ResumeOptimizationInputSnapshot, ResumeToolEvidenceBundle


class ResumeOptimizationProvider(Protocol):
    async def generate(
        self, snapshot: ResumeOptimizationInputSnapshot, tool_evidence: ResumeToolEvidenceBundle
    ) -> Mapping[str, object]: ...

    async def repair(
        self, snapshot: ResumeOptimizationInputSnapshot, tool_evidence: ResumeToolEvidenceBundle,
        raw: object, error: str
    ) -> Mapping[str, object]: ...


class LLMResumeOptimizationProvider:
    def __init__(self, settings: Settings, client: LLMClient) -> None:
        self._client = client
        self._model = model_for_operation(settings, "planning")
        self._max_output_tokens = settings.agent_max_output_tokens_per_call

    async def generate(
        self, snapshot: ResumeOptimizationInputSnapshot, tool_evidence: ResumeToolEvidenceBundle
    ) -> Mapping[str, object]:
        return await self._complete(snapshot, tool_evidence, operation="resume_optimization")

    async def repair(
        self, snapshot: ResumeOptimizationInputSnapshot, tool_evidence: ResumeToolEvidenceBundle,
        raw: object, error: str
    ) -> Mapping[str, object]:
        del raw
        return await self._complete(
            snapshot,
            tool_evidence,
            operation="resume_optimization_repair",
            extra=(
                f"Previous candidate failed validation: {error}. "
                "Correct it without adding facts."
            ),
        )

    async def _complete(
        self,
        snapshot: ResumeOptimizationInputSnapshot,
        tool_evidence: ResumeToolEvidenceBundle,
        *,
        operation: str,
        extra: str | None = None,
    ) -> Mapping[str, object]:
        messages = resume_optimization_messages(snapshot, tool_evidence)
        if extra:
            messages.append(type(messages[0])(role="user", content=extra))
        response = await self._client.complete(
            LLMRequest(
                operation=operation,
                model=self._model,
                messages=messages,
                max_output_tokens=self._max_output_tokens,
                structured_output="json_object",
                temperature=0,
                reasoning="off",
            )
        )
        try:
            payload = json.loads(response.content or "")
        except json.JSONDecodeError:
            payload = {"_raw_text": (response.content or "")[:12000]}
        if not isinstance(payload, dict):
            payload = {"_raw_value": payload}
        return {
            **payload,
            "usage": ProviderUsage(
                model_id=response.model_id,
                provider=response.provider_id,
                request_id=response.request_id,
                raw_output_hash=response.raw_output_hash,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                latency_ms=response.latency_ms,
            ).model_dump(mode="json"),
        }


class MockResumeOptimizationProvider:
    model_id = "mock-resume-optimizer-v1"

    async def generate(
        self, snapshot: ResumeOptimizationInputSnapshot, tool_evidence: ResumeToolEvidenceBundle
    ) -> Mapping[str, object]:
        from app.agent.resume_optimization_nodes import deterministic_candidate

        return {
            **deterministic_candidate(snapshot, tool_evidence).model_dump(mode="json"),
            "usage": ProviderUsage(
                model_id=self.model_id, tokens_in=180, tokens_out=160, latency_ms=1
            ).model_dump(mode="json"),
        }

    async def repair(
        self, snapshot: ResumeOptimizationInputSnapshot, tool_evidence: ResumeToolEvidenceBundle,
        raw: object, error: str
    ) -> Mapping[str, object]:
        del raw, error
        return await self.generate(snapshot, tool_evidence)


def build_resume_optimization_provider(
    settings: Settings, client: LLMClient | None
) -> ResumeOptimizationProvider:
    if settings.llm_provider == "mock":
        return MockResumeOptimizationProvider()
    if client is None:
        raise RuntimeError("Resume Optimization Provider requires a shared LLM client")
    return LLMResumeOptimizationProvider(settings, client)
