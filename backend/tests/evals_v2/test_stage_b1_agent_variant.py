"""Stage B-1a-lite unit tests.

Covers:
* ``ExperimentRuntimeContext`` is frozen + carries agent_variant
* ``build_planning_provider`` with ``agent_variant`` dispatches to the
  correct PairSmokePlanningProvider profile
* ``frozen_config_hash`` differs between two experiments whose only
  difference is ``agent_variant``
* ``agent_variant=None`` preserves the legacy MockPlanningProvider path
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import Settings, get_settings
from app.providers.llm import (
    DirectLLMPlanningProvider,
    MockPlanningProvider,
    OpenAICompatiblePlanningProvider,
    PairSmokePlanningProvider,
    build_planning_provider,
)
from evals.v2.contracts import ExperimentCreate
from evals.v2.dataset_loader import load_dataset
from evals.v2.experiment_runtime_context import ExperimentRuntimeContext


def _base_config(**overrides: object) -> ExperimentCreate:
    manifest = load_dataset().manifest
    defaults: dict[str, object] = {
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.dataset_version,
        "dataset_hash": manifest.source_sha256,
        "git_commit": "abc1234",
        "graph_version": "stage5-v1",
        "prompt_version": "career-plan-v1",
        "model_version": "mock-v1",
        "tool_version": "tool-contract-v1",
        "context_version": "context-v1",
        "memory_version": "memory-v1",
        "execution_mode": "fixture_provider",
        "variant_role": "baseline",
        "trial_count": 1,
    }
    defaults.update(overrides)
    return ExperimentCreate(**defaults)


# ---------------------------------------------------------------------------
# ExperimentRuntimeContext
# ---------------------------------------------------------------------------


def test_runtime_context_is_frozen() -> None:
    ctx = ExperimentRuntimeContext(
        experiment_id=uuid4(),
        agent_variant="compact_execution_v1",
        graph_version="stage5-v1",
        prompt_version="career-plan-v1",
        model_version="mock-v1",
    )
    with pytest.raises(AttributeError):
        ctx.agent_variant = "structured_reasoning_v1"  # type: ignore[misc]


def test_runtime_context_agent_variant_none_is_legacy() -> None:
    ctx = ExperimentRuntimeContext(
        experiment_id=uuid4(),
        agent_variant=None,
        graph_version="stage5-v1",
        prompt_version="career-plan-v1",
        model_version="mock-v1",
    )
    assert ctx.agent_variant is None


# ---------------------------------------------------------------------------
# build_planning_provider dispatch
# ---------------------------------------------------------------------------


def test_build_provider_compact_variant() -> None:
    settings = get_settings().model_copy(update={"llm_provider": "mock"})
    provider = build_planning_provider(
        settings, agent_variant="compact_execution_v1"
    )
    assert isinstance(provider, PairSmokePlanningProvider)
    assert provider._profile == "compact_v1"  # noqa: SLF001


def test_build_provider_structured_variant() -> None:
    settings = get_settings().model_copy(update={"llm_provider": "mock"})
    provider = build_planning_provider(
        settings, agent_variant="structured_reasoning_v1"
    )
    assert isinstance(provider, PairSmokePlanningProvider)
    assert provider._profile == "structured_v1"  # noqa: SLF001


def test_build_provider_none_variant_is_legacy_mock() -> None:
    settings = get_settings().model_copy(update={"llm_provider": "mock"})
    provider = build_planning_provider(settings, agent_variant=None)
    assert isinstance(provider, MockPlanningProvider)


def test_build_provider_unknown_variant_falls_back_to_mock() -> None:
    """An unrecognized agent_variant MUST NOT crash — it falls back to
    the legacy MockPlanningProvider. The variant namespace is open-ended
    at the DB layer; the factory silently ignores unknown values so
    adding a new variant doesn't break experiments that haven't been
    re-provisioned."""

    settings = get_settings().model_copy(update={"llm_provider": "mock"})
    provider = build_planning_provider(
        settings, agent_variant="unknown_future_variant_v3"
    )
    assert isinstance(provider, MockPlanningProvider)


def test_live_provider_dispatches_direct_and_full_variants() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai_compatible",
        llm_api_key="unit-test-key",
        llm_base_url="https://llm.example.test/v1",
        llm_model="configured-model",
    )

    direct = build_planning_provider(settings, agent_variant="direct_llm_v1")
    full = build_planning_provider(settings, agent_variant="full_agent_v1")

    assert isinstance(direct, DirectLLMPlanningProvider)
    assert isinstance(full, OpenAICompatiblePlanningProvider)


# ---------------------------------------------------------------------------
# frozen_config_hash divergence
# ---------------------------------------------------------------------------


def test_frozen_config_hash_differs_by_agent_variant() -> None:
    """Two ExperimentCreate configs that differ ONLY in agent_variant
    MUST produce different frozen_config() dumps → different hashes.
    This is the core invariant that makes the variant identity
    experiment-scoped (not global)."""

    from evals.v2.contracts import canonical_sha256

    compact = _base_config(agent_variant="compact_execution_v1")
    structured = _base_config(agent_variant="structured_reasoning_v1")
    none_variant = _base_config(agent_variant=None)

    hash_compact = canonical_sha256(compact.frozen_config())
    hash_structured = canonical_sha256(structured.frozen_config())
    hash_none = canonical_sha256(none_variant.frozen_config())

    assert hash_compact != hash_structured, (
        "compact and structured variants must have different frozen_config_hash"
    )
    assert hash_compact != hash_none, (
        "variant experiment must differ from legacy (agent_variant=None)"
    )
    assert hash_structured != hash_none
