"""Stable request/response projections for ProviderCall audit + fixtures.

Each function produces a ``dict[str, object]`` whose ``canonical_sha256``
is content-stable across reruns and which excludes raw PII (request message
text), raw transcript (visible_evidence content), and any field that depends
on wall-clock ordering. PR-5 issue #6 (transcript-hash stability) is
satisfied because every projection here derives its hash only from the
input-side structural information the grader cares about.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from evals.v2.contracts import canonical_sha256

if TYPE_CHECKING:
    from app.schemas.agent_runs import PlanningContext

_TITLE_SLICE = 80
_QUERY_SLICE = 64


def _redact_message(message: str) -> dict[str, object]:
    """Replace raw message with a length + sha256 fingerprint + marker list."""

    markers = re.findall(r"\[mock:[a-z0-9_-]+\]", message)
    return {
        "length": len(message),
        "sha256": canonical_sha256(message),
        "markers": markers,
    }


def _evidence_catalog_fingerprint(catalog: Sequence[object]) -> dict[str, object]:
    return {
        "count": len(catalog),
        "sha256": canonical_sha256(
            [
                {
                    "kind": getattr(item, "kind", None),
                    "id": str(getattr(item, "id", None)),
                }
                for item in catalog
            ]
        ),
    }


# ----- LLM (PlanningProvider) -----


def request_generate_agent_turn(
    *,
    message: str,
    context: PlanningContext,
    replan_mode: object,
    available_tools: Sequence[object],
    evidence_catalog: Sequence[object],
    force_final: bool,
) -> dict[str, object]:
    # mypy treats ``list[ModelToolSpec]`` as incompatible with ``list[object]``
    # (List is invariant); accept a Sequence so callers stay loosely typed.
    tools_seq: list[object] = list(available_tools)
    catalog: list[object] = list(evidence_catalog)
    return {
        "method": "generate_agent_turn",
        "message": _redact_message(message),
        "intent": getattr(context, "resolved_intent", None),
        "time_budget_minutes": getattr(context, "time_budget_minutes", None),
        "planning_date": _iso(getattr(context, "planning_date", None)),
        "replan_mode": getattr(replan_mode, "value", replan_mode),
        "available_tools": [
            {"name": getattr(t, "name", None)} for t in tools_seq
        ],
        "evidence_catalog": _evidence_catalog_fingerprint(catalog),
        "force_final": force_final,
    }


def request_generate_plan(
    *, message: str, context: PlanningContext, replan_mode: object
) -> dict[str, object]:
    return {
        "method": "generate_plan",
        "message": _redact_message(message),
        "intent": getattr(context, "resolved_intent", None),
        "time_budget_minutes": getattr(context, "time_budget_minutes", None),
        "planning_date": _iso(getattr(context, "planning_date", None)),
        "replan_mode": getattr(replan_mode, "value", replan_mode),
    }


def request_repair(
    *,
    method: str,
    raw_output: str | None,
    candidate: object | None,
    repair_instructions: str,
    attempt: int,
    raw_output_proj: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "method": method,
        "attempt": attempt,
        "repair_instructions_sha256": canonical_sha256(repair_instructions),
    }
    if raw_output is not None:
        payload["raw_output_hash"] = canonical_sha256(raw_output)
    elif candidate is not None:
        payload["candidate_hash"] = canonical_sha256(
            candidate.model_dump(mode="json")
            if hasattr(candidate, "model_dump")
            else str(candidate)
        )
    elif raw_output_proj is not None:
        payload["raw_output_hash"] = canonical_sha256(raw_output_proj)
    return payload


def response_llm_turn(response: object) -> dict[str, object]:
    raw = dict(response) if isinstance(response, dict) else {}
    tool_calls = raw.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_calls = []
    projected_tool_calls: list[dict[str, object]] = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            args = tc.get("arguments")
            args_keys = sorted(args.keys()) if isinstance(args, dict) else []
        else:
            args_keys = []
        projected_tool_calls.append(
            {"name": tc.get("name") if isinstance(tc, dict) else None,
             "args_keys": args_keys}
        )
    usage_raw = raw.get("usage")
    usage = usage_raw if isinstance(usage_raw, dict) else {}
    final = raw.get("final")
    final_task_count = (
        len(final.get("tasks", []))
        if isinstance(final, dict) and isinstance(final.get("tasks"), list)
        else 0
    )
    return {
        "options": [
            k
            for k in ("final", "tool_calls", "clarification", "safe_response")
            if k in raw and raw[k] is not None
        ],
        "tool_calls_count": len(tool_calls),
        "tool_calls": projected_tool_calls,
        "final_task_count": final_task_count,
        "usage": {
            "tokens_in": usage.get("tokens_in"),
            "tokens_out": usage.get("tokens_out"),
            "model_id": usage.get("model_id"),
        },
    }


# ----- Search -----


def request_search(
    *, query: str, limit: int, freshness_days: int | None
) -> dict[str, object]:
    return {
        "method": "search",
        "query_first_64": (query or "")[:_QUERY_SLICE],
        "query_sha256": canonical_sha256(query),
        "limit": limit,
        "freshness_days": freshness_days,
    }


def response_search(response: object) -> dict[str, object]:
    items_raw = response if isinstance(response, list) else []
    items: list[object] = list(items_raw)
    return {
        "result_count": len(items),
        "first_titles": [
            (
                getattr(item, "title", None)
                or (item.get("title") if isinstance(item, dict) else None)
                or "<untitled>"
            )[:_TITLE_SLICE]
            for item in items[:3]
        ],
    }


# ----- Embedding -----


def request_embedding(*, texts: list[str]) -> dict[str, object]:
    return {
        "method": "embed",
        "text_count": len(texts),
        "texts_sha256": canonical_sha256([canonical_sha256(t) for t in texts]),
    }


def response_embedding(
    response: object, *, dimension_hint: int
) -> dict[str, object]:
    vectors: list[list[float]] = (
        list(response) if isinstance(response, list) else []
    )
    first_dim = len(vectors[0]) if vectors and vectors[0] else dimension_hint
    first_norm = (
        round(sum(abs(x) for x in vectors[0]) / max(len(vectors[0]), 1), 6)
        if vectors and vectors[0]
        else 0.0
    )
    return {
        "vec_count": len(vectors),
        "vec_dim": first_dim,
        "vec_norm_mean_sample": first_norm,
    }


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


__all__ = [
    "request_embedding",
    "request_generate_agent_turn",
    "request_generate_plan",
    "request_repair",
    "request_search",
    "response_embedding",
    "response_llm_turn",
    "response_search",
]
