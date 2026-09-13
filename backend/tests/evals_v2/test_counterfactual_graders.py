"""PR-8b Evidence Citation precision / recall sub-grader tests.

Pure-Python unit tests (no PostgreSQL) that exercise the new
``model.evidence_citation_precision`` / ``model.evidence_citation_recall``
sub-graders. They reuse the helpers from ``test_graders.py`` so the test
infrastructure matches the canonical grader unit suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from evals.v2.contracts import EvalCase, canonical_sha256
from evals.v2.graders.base import EvidenceKind
from tests.evals_v2.test_graders import (
    _case,
    _grader,
    _outcome,
    _view_for,
)


def _case_with_expected_citations(expected_citations: list[str] | None) -> EvalCase:
    """Build a Case carrying an ``expected_citations`` provider_fixtures entry.

    Mirrors ``test_graders._case`` but lets the citation list be customised
    (or omitted) to exercise each branch of the new sub-grader.
    """

    base = _case()
    provider_fixtures: dict[str, object] = dict(base.scenario.provider_fixtures)
    if expected_citations is not None:
        provider_fixtures["expected_citations"] = expected_citations
    else:
        provider_fixtures.pop("expected_citations", None)
    payload = base.model_dump(mode="json")
    payload["scenario"]["provider_fixtures"] = provider_fixtures
    # Re-stamp fixture_hash since the payload mutated.
    payload.pop("fixture_hash", None)
    payload["fixture_hash"] = canonical_sha256(
        {k: v for k, v in payload.items() if k != "fixture_hash"}
    )
    return EvalCase.model_validate(payload)


# ---------------------------------------------------------------------------
# 1. visible_evidence baseline: full citations -> precision=1.0, recall=1.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_citation_grader_full_match_baseline() -> None:
    """When the Plan cites every expected memory, both score 1.0."""

    mem_a_uuid = uuid4()
    mem_b_uuid = uuid4()
    expected = _case_with_expected_citations(["mem-A", "mem-B"])
    outcome = _outcome(
        status="completed", result_kind="plan", final_plan_id=uuid4(),
        plan={
            "summary": "x",
            "evidence_refs": [
                {"kind": "memory", "id": str(mem_a_uuid)},
                {"kind": "memory", "id": str(mem_b_uuid)},
            ],
        },
        events=[{
            "sequence": 1, "event_type": "run.completed",
            "result_kind": "plan", "error_code": None,
            "fallback_reason": None, "tool_name": None, "success": None,
        }],
    )
    view, _tid = _view_for(
        "model", outcome, expected,
        overrides={
            EvidenceKind.PLAN_PROJECTION: {
                "evidence_refs": [
                    {"kind": "memory", "id": str(mem_a_uuid)},
                    {"kind": "memory", "id": str(mem_b_uuid)},
                ],
            },
            EvidenceKind.EXPECTED_CITATIONS_MAP: {
                "expected_citations_map": {
                    "mem-A": str(mem_a_uuid),
                    "mem-B": str(mem_b_uuid),
                },
            },
        },
    )
    results = await _grader("model").grade(outcome, view, expected)
    precision = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_precision"
    )
    recall = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_recall"
    )
    assert precision.score == 1.0
    assert recall.score == 1.0
    assert precision.metric_type == "numeric"
    assert recall.metric_type == "numeric"


# ---------------------------------------------------------------------------
# 2. hidden_evidence: Plan has no evidence_refs -> precision=0, recall=0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_citation_grader_hidden_lowers_recall() -> None:
    """When the hidden axis filters them out, no Plan refs match."""

    mem_a_uuid = uuid4()
    mem_b_uuid = uuid4()
    expected = _case_with_expected_citations(["mem-A", "mem-B"])
    outcome = _outcome(
        status="completed", result_kind="plan", final_plan_id=uuid4(),
        plan={"summary": "x", "evidence_refs": []},
        events=[{
            "sequence": 1, "event_type": "run.completed",
            "result_kind": "plan", "error_code": None,
            "fallback_reason": None, "tool_name": None, "success": None,
        }],
    )
    view, _tid = _view_for(
        "model", outcome, expected,
        overrides={
            EvidenceKind.PLAN_PROJECTION: {"evidence_refs": []},
            EvidenceKind.EXPECTED_CITATIONS_MAP: {
                "expected_citations_map": {
                    "mem-A": str(mem_a_uuid),
                    "mem-B": str(mem_b_uuid),
                },
            },
        },
    )
    results = await _grader("model").grade(outcome, view, expected)
    precision = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_precision"
    )
    recall = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_recall"
    )
    assert precision.score == 0.0
    assert recall.score == 0.0


# ---------------------------------------------------------------------------
# 3. Partial recall: 1 of 2 expected -> precision=1.0, recall=0.5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_citation_grader_partial_citation() -> None:
    """One expected UUID is claimed, the other missing."""

    mem_a_uuid = uuid4()
    mem_b_uuid = uuid4()
    expected = _case_with_expected_citations(["mem-A", "mem-B"])
    outcome = _outcome(
        status="completed", result_kind="plan", final_plan_id=uuid4(),
        plan={
            "summary": "x",
            "evidence_refs": [
                {"kind": "memory", "id": str(mem_a_uuid)},
            ],
        },
        events=[{
            "sequence": 1, "event_type": "run.completed",
            "result_kind": "plan", "error_code": None,
            "fallback_reason": None, "tool_name": None, "success": None,
        }],
    )
    view, _tid = _view_for(
        "model", outcome, expected,
        overrides={
            EvidenceKind.PLAN_PROJECTION: {
                "evidence_refs": [
                    {"kind": "memory", "id": str(mem_a_uuid)},
                ],
            },
            EvidenceKind.EXPECTED_CITATIONS_MAP: {
                "expected_citations_map": {
                    "mem-A": str(mem_a_uuid),
                    "mem-B": str(mem_b_uuid),
                },
            },
        },
    )
    results = await _grader("model").grade(outcome, view, expected)
    precision = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_precision"
    )
    recall = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_recall"
    )
    assert precision.score == 1.0  # the single actual ref fully matches
    assert recall.score == 0.5  # only 1 of 2 expected


# ---------------------------------------------------------------------------
# 4. N/A path: Case has no expected_citations -> both rows are not_applicable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_citation_grader_no_expected_returns_not_applicable() -> None:
    """Cases that declare no expected_citations emit N/A rows."""

    expected = _case_with_expected_citations(None)
    outcome = _outcome(
        status="completed", result_kind="plan", final_plan_id=uuid4(),
        plan={"summary": "x", "evidence_refs": []},
        events=[{
            "sequence": 1, "event_type": "run.completed",
            "result_kind": "plan", "error_code": None,
            "fallback_reason": None, "tool_name": None, "success": None,
        }],
    )
    view, _tid = _view_for("model", outcome, expected)
    results = await _grader("model").grade(outcome, view, expected)
    precision = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_precision"
    )
    recall = next(
        r for r in results
        if r.evidence.get("subgrader") == "evidence_citation_recall"
    )
    assert precision.categorical_value == "not_applicable"
    assert recall.categorical_value == "not_applicable"
