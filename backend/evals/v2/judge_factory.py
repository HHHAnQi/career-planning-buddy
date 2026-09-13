"""PR-9c.1 Pairwise Judge factory.

Builds a ``PairwiseJudge`` from ``Settings``. Mirrors
``build_planning_provider``'s contract: ``mock``/``fixture`` returns a
``FixturePairwiseJudge`` for tests, ``openai_compatible`` returns the live
adapter. The judge LLM is configured separately from the agent LLM
(``judge_llm_*`` settings) so a system cannot self-judge; when
``judge_llm_*`` is blank, the caller must pass an explicit mapping (for
the fixture judge) or fall back to the agent LLM explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.agent.errors import ProviderConfigurationError
from app.core.config import Settings
from evals.v2.judge import (
    FixturePairwiseJudge,
    OpenAICompatiblePairwiseJudge,
    PairwiseJudge,
    PairwiseJudgeOutput,
)


def build_pairwise_judge(
    settings: Settings,
    *,
    fixture_mapping: Mapping[str, PairwiseJudgeOutput] | None = None,
) -> PairwiseJudge:
    """Build the configured ``PairwiseJudge``.

    * ``judge_llm_provider='fixture'`` (default): returns a
      ``FixturePairwiseJudge`` over the supplied ``fixture_mapping`` (empty
      by default → fail-closed).
    * ``judge_llm_provider='openai_compatible'``: returns the live adapter,
      requiring ``judge_llm_api_key`` / ``judge_llm_base_url`` /
      ``judge_llm_model`` to be set.
    """

    if settings.judge_llm_provider == "fixture":
        return FixturePairwiseJudge(mapping=fixture_mapping or {})

    if settings.judge_llm_provider == "mock":
        return FixturePairwiseJudge(
            mapping=fixture_mapping or {}, model_id="mock-judge-v1"
        )

    # openai_compatible
    if (
        settings.judge_llm_api_key is None
        or settings.judge_llm_base_url is None
        or settings.judge_llm_model is None
    ):
        missing = [
            name
            for name, value in (
                ("JUDGE_LLM_API_KEY", settings.judge_llm_api_key),
                ("JUDGE_LLM_BASE_URL", settings.judge_llm_base_url),
                ("JUDGE_LLM_MODEL", settings.judge_llm_model),
            )
            if value is None
        ]
        raise ProviderConfigurationError(
            "openai_compatible judge requires " + ", ".join(missing)
        )

    return OpenAICompatiblePairwiseJudge(
        api_key=settings.judge_llm_api_key.get_secret_value(),
        base_url=str(settings.judge_llm_base_url),
        model=settings.judge_llm_model,
        timeout_seconds=settings.judge_llm_timeout_seconds,
        max_output_tokens=settings.judge_llm_max_output_tokens,
        temperature=settings.judge_llm_temperature,
    )
