"""Structured LLM adapter used only for best-effort evidence distillation."""

import json
from typing import Protocol

from app.core.config import Settings
from app.models.evidence import SearchSource
from app.providers.llm_client import LLMClient, OpenAIChatLLMClient
from app.providers.llm_contracts import LLMMessage, LLMRequest
from app.providers.llm_profiles import model_for_operation, resolve_provider_profile


class EvidenceDistillationProvider(Protocol):
    async def distill(self, *, goal_type: str, sources: list[SearchSource]) -> object: ...

    async def repair(self, *, raw_output: object) -> object: ...


class MockEvidenceDistillationProvider:
    async def distill(self, *, goal_type: str, sources: list[SearchSource]) -> object:
        del goal_type
        return {
            "candidates": [
                {
                    "title": (source.title or "搜索证据")[:200],
                    "content": source.snippet[:300],
                    "source_ids": [str(source.id)],
                    "evidence_excerpt": source.snippet[:300],
                    "confidence": float(source.reliability),
                }
                for source in sources[:3]
                if source.snippet.strip()
            ]
        }

    async def repair(self, *, raw_output: object) -> object:
        return raw_output


class OpenAICompatibleEvidenceDistillationProvider:
    def __init__(self, settings: Settings, *, client: LLMClient | None = None) -> None:
        assert settings.llm_api_key and settings.llm_base_url and settings.llm_model
        base_url = str(settings.llm_base_url)
        self._model = model_for_operation(settings, "evidence_distillation")
        self._reasoning = settings.llm_evidence_distillation_reasoning
        self._owns_client = client is None
        self._client = client or OpenAIChatLLMClient(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=base_url,
            profile=resolve_provider_profile(
                configured_name=settings.llm_provider_name,
                base_url=base_url,
            ),
            timeout_seconds=settings.llm_timeout_seconds,
        )

    async def distill(self, *, goal_type: str, sources: list[SearchSource]) -> object:
        source_payload = [
            {"id": str(item.id), "title": item.title, "content": item.snippet[:1200]}
            for item in sources
        ]
        prompt = (
            "Return JSON only: {candidates:[{title,content,source_ids,evidence_excerpt,"
            "confidence}]}. At most 3 atomic reusable career facts. content and excerpt <=300; "
            "excerpt must be verbatim in a supplied content; never infer private facts. "
            f"goal_type={goal_type}; sources={json.dumps(source_payload, ensure_ascii=False)}"
        )
        return await self._complete(prompt)

    async def repair(self, *, raw_output: object) -> object:
        return await self._complete(
            "Repair this into the exact requested JSON schema without adding facts: "
            + json.dumps(raw_output, ensure_ascii=False, default=str)[:6000]
        )

    async def _complete(self, prompt: str) -> object:
        response = await self._client.complete(
            LLMRequest(
                operation="evidence_distillation",
                model=self._model,
                messages=[LLMMessage(role="user", content=prompt)],
                structured_output="json_object",
                reasoning=self._reasoning,
                temperature=0,
                max_output_tokens=800,
            )
        )
        if response.content is None:
            raise ValueError("Evidence distillation Provider returned empty content")
        return json.loads(response.content)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def build_evidence_distillation_provider(
    settings: Settings,
    *,
    client: LLMClient | None = None,
) -> EvidenceDistillationProvider:
    if settings.llm_provider == "mock":
        return MockEvidenceDistillationProvider()
    return OpenAICompatibleEvidenceDistillationProvider(settings, client=client)
