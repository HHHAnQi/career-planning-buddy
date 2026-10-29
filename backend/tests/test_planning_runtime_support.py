"""Unit contracts for pure planning runtime transformations."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.agent.errors import StructuredOutputError
from app.agent.runtime_support import (
    build_provider_telemetry,
    combine_provider_usage,
    extract_provider_usage,
    restore_candidate_checkpoint,
)
from app.schemas.agent_runs import EvidenceRef, EvidenceVisibility, ProviderUsage


def _usage(**overrides: object) -> ProviderUsage:
    values: dict[str, object] = {
        "model_id": "model-a",
        "provider": "mock",
        "tokens_in": 10,
        "tokens_out": 5,
        "latency_ms": 20,
        "cost_cny": Decimal("0.1"),
    }
    values.update(overrides)
    return ProviderUsage.model_validate(values)


def test_combine_usage_sums_costs_and_keeps_latest_call_identity() -> None:
    combined = combine_provider_usage(
        _usage(request_id="first"),
        _usage(
            model_id="model-b",
            request_id="second",
            tokens_in=7,
            tokens_out=3,
            latency_ms=11,
            cost_cny=Decimal("0.2"),
        ),
    )

    assert combined.model_id == "model-b"
    assert combined.request_id == "second"
    assert combined.tokens_in == 17
    assert combined.tokens_out == 8
    assert combined.latency_ms == 31
    assert combined.cost_cny == Decimal("0.3")


def test_provider_telemetry_exposes_only_evidence_references() -> None:
    visible = EvidenceRef(kind="memory", id=uuid4())
    truncated = EvidenceRef(kind="search_source", id=uuid4())
    visibility = EvidenceVisibility(
        call_id="call-1",
        catalog_hash="a" * 64,
        visible_refs=[visible],
        truncated_refs=[truncated],
    )

    telemetry = build_provider_telemetry(_usage(), "prompt-v1", visibility)

    assert telemetry.prompt_version == "prompt-v1"
    assert telemetry.trace_data["visible_evidence_ids"] == [str(visible.id)]
    assert telemetry.trace_data["truncated_evidence_ids"] == [str(truncated.id)]
    assert "content" not in telemetry.trace_data


@pytest.mark.parametrize("raw", [None, [], {}, {"usage": {"tokens_in": -1}}])
def test_extract_provider_usage_normalizes_invalid_envelopes(raw: object) -> None:
    with pytest.raises(StructuredOutputError):
        extract_provider_usage(raw)


def test_corrupt_checkpoint_is_rejected_without_partial_restore() -> None:
    assert restore_candidate_checkpoint({"candidate": {"invalid": True}}) is None
