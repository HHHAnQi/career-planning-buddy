"""Pure runtime transformations shared by the planning graph orchestration."""

import json
from dataclasses import dataclass
from hashlib import sha256

from pydantic import ValidationError

from app.agent.errors import StructuredOutputError
from app.agent.node_runner import NodeOutput, NodeTelemetry
from app.schemas.agent_runs import (
    EvidenceCatalogItem,
    EvidenceVisibility,
    PlanCandidate,
    PlanningContext,
    PlanningState,
    ProviderUsage,
)
from app.schemas.enums import ReplanMode

CandidateResult = tuple[
    PlanCandidate,
    list[EvidenceCatalogItem],
    EvidenceVisibility,
    int,
    int,
]


@dataclass(frozen=True, slots=True)
class RestoredCandidateCheckpoint:
    """Validated checkpoint data without mutating graph state."""

    output: NodeOutput[CandidateResult]
    fallback_reason: str | None


def as_int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def planning_input_fingerprint(
    message: str,
    context: PlanningContext,
    replan_mode: ReplanMode | str,
) -> str:
    """Hash every immutable prompt determinant for checkpoint reuse."""

    mode = replan_mode.value if isinstance(replan_mode, ReplanMode) else replan_mode
    encoded = json.dumps(
        {
            "message": message,
            "context": context.model_dump(mode="json"),
            "replan_mode": mode,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()


def candidate_checkpoint_payload(
    output: NodeOutput[CandidateResult],
    state: PlanningState,
) -> dict[str, object]:
    """Serialize a successful candidate result into a replay-safe checkpoint."""

    candidate, catalog, visibility, tool_round, tool_call_count = output.value
    telemetry = output.telemetry
    return {
        "attempt": as_int(state.get("attempt_count"), 1),
        "candidate": candidate.model_dump(mode="json"),
        "evidence_catalog": [item.model_dump(mode="json") for item in catalog],
        "visibility": visibility.model_dump(mode="json"),
        "tool_round": tool_round,
        "tool_call_count": tool_call_count,
        "fallback_reason": state.get("fallback_reason"),
        "usage": {
            "tokens_in": telemetry.tokens_in,
            "tokens_out": telemetry.tokens_out,
            "cost_cny": str(telemetry.cost_cny),
            "model_id": telemetry.model_id,
            # A restored checkpoint represents no new physical provider call.
            "latency_ms": 0,
        },
    }


def restore_candidate_checkpoint(
    frozen: dict[str, object],
) -> RestoredCandidateCheckpoint | None:
    """Validate untrusted checkpoint JSON and return a typed candidate result."""

    try:
        candidate = PlanCandidate.model_validate(frozen["candidate"])
        raw_catalog = frozen["evidence_catalog"]
        catalog = [
            EvidenceCatalogItem.model_validate(item)
            for item in (raw_catalog if isinstance(raw_catalog, list) else [])
        ]
        visibility = EvidenceVisibility.model_validate(frozen["visibility"])
        usage = ProviderUsage.model_validate(frozen["usage"])
        fallback_reason = frozen.get("fallback_reason")
        return RestoredCandidateCheckpoint(
            output=NodeOutput(
                (
                    candidate,
                    catalog,
                    visibility,
                    as_int(frozen.get("tool_round")),
                    as_int(frozen.get("tool_call_count")),
                ),
                NodeTelemetry(
                    trace_data={
                        "checkpoint_reused": True,
                        "checkpoint_attempts_saved": as_int(frozen.get("attempt")),
                    },
                    tokens_in=usage.tokens_in,
                    tokens_out=usage.tokens_out,
                    cost_cny=usage.cost_cny,
                    model_id=usage.model_id,
                ),
            ),
            fallback_reason=(str(fallback_reason) if fallback_reason is not None else None),
        )
    except (KeyError, ValidationError, ValueError, TypeError):
        return None


def extract_provider_usage(raw: object) -> ProviderUsage:
    """Read the provider usage envelope or normalize it to a domain error."""

    if not isinstance(raw, dict):
        raise StructuredOutputError
    try:
        return ProviderUsage.model_validate(raw.get("usage"))
    except ValidationError as exc:
        raise StructuredOutputError from exc


def combine_provider_usage(first: ProviderUsage, second: ProviderUsage) -> ProviderUsage:
    """Aggregate sequential calls while retaining the latest call identity."""

    return ProviderUsage(
        model_id=second.model_id,
        provider=second.provider,
        request_id=second.request_id,
        raw_output_hash=second.raw_output_hash,
        tokens_in=first.tokens_in + second.tokens_in,
        tokens_out=first.tokens_out + second.tokens_out,
        latency_ms=first.latency_ms + second.latency_ms,
        cost_cny=first.cost_cny + second.cost_cny,
    )


def build_provider_telemetry(
    usage: ProviderUsage,
    prompt_version: str,
    visibility: EvidenceVisibility,
) -> NodeTelemetry:
    """Build the stable, redacted trace contract for one provider path."""

    trace_data: dict[str, object] = {
        "latency_ms": usage.latency_ms,
        "provider": usage.provider,
        "evidence_call_id": visibility.call_id,
        "evidence_catalog_hash": visibility.catalog_hash,
        "visible_evidence_refs": [item.model_dump(mode="json") for item in visibility.visible_refs],
        "visible_evidence_ids": [str(item.id) for item in visibility.visible_refs],
        "visible_evidence_count": len(visibility.visible_refs),
        "truncated_evidence_ids": [str(item.id) for item in visibility.truncated_refs],
        "truncated_evidence_count": len(visibility.truncated_refs),
    }
    if usage.request_id is not None:
        trace_data["request_id"] = usage.request_id
    if usage.raw_output_hash is not None:
        trace_data["raw_output_hash"] = usage.raw_output_hash
    return NodeTelemetry(
        trace_data=trace_data,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cost_cny=usage.cost_cny,
        model_id=usage.model_id,
        prompt_version=prompt_version,
    )
