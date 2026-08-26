"""Model-domain deterministic Graders.

Spec gate (PR-4): structured-output / format-repair-count / evidence-
visibility are hard gates. Token-usage-nonzero is a quality signal
(``hard_gate=False``) that future runs can grow into a hard gate once the
price/quality baseline is frozen.

The evidence-visibility check reuses ``evidence_refs_are_visible`` from
``app.harness.evidence`` indirectly, by relying on the collector to emit a
``plan_projection`` that carries both ``evidence_refs`` and ``visible_refs``
as already-validated sets. The Model grader stays pure-python over the
projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.collectors.outcome import RunOutcome
from evals.v2.contracts import GradeResult
from evals.v2.graders.base import AuthorizedView, EvidenceKind, as_dict_list

if TYPE_CHECKING:
    from evals.v2.contracts import EvalCase

GRADER_NAME_PREFIX = "model"
GRADER_VERSION = "v1"
ALLOWED_KINDS = frozenset({
    EvidenceKind.TOOL_CALL_PROJECTION,
    EvidenceKind.EVIDENCE_VISIBLE_REFS,
    EvidenceKind.PLAN_PROJECTION,
    EvidenceKind.RUN_METRICS,
    EvidenceKind.REPAIR_SIGNAL,
    EvidenceKind.PROVIDER_CALL_PROJECTION,
    EvidenceKind.EXPECTED_CITATIONS_MAP,
    EvidenceKind.TASK_PROJECTION,
})


def _text_bigrams(text: str) -> set[str]:
    normalized = "".join(text.split()).lower()
    if len(normalized) < 2:
        return set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def _boolean_grade(
    *, name: str, passed: bool, actual: object, expected: object,
    evidence_ids: list[UUID], rationale: str,
) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="model",
        metric_type="boolean",
        passed=passed, hard_gate=True,
        evidence_item_ids=evidence_ids,
        evidence={"actual": actual, "expected": expected, "subgrader": name},
        rationale=rationale,
    )


def _numeric_grade(
    *, name: str, score: float, threshold: float,
    evidence_ids: list[UUID], rationale: str,
    actual: object | None = None,
    expected: object | None = None,
) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="model",
        metric_type="numeric",
        score=score, threshold=threshold, hard_gate=False,
        evidence_item_ids=evidence_ids,
        evidence={
            "actual": score if actual is None else actual,
            "expected": f">= {threshold}" if expected is None else expected,
            "subgrader": name,
        },
        rationale=rationale,
    )


def _not_applicable(name: str, evidence_ids: list[UUID], rationale: str) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="model",
        metric_type="categorical",
        categorical_value="not_applicable", hard_gate=False,
        evidence_item_ids=evidence_ids,
        evidence={"actual": None, "expected": None, "subgrader": name, "reason": "na"},
        rationale=rationale,
    )


async def grade(outcome: RunOutcome, view: AuthorizedView, expected: EvalCase) -> list[GradeResult]:
    from evals.v2.contracts import PlanningEvalScenario

    if not isinstance(expected.scenario, PlanningEvalScenario):
        raise TypeError("planning model grader requires a planning scenario")
    metrics = view.first(EvidenceKind.RUN_METRICS)
    metrics_id = metrics.id if metrics is not None else None
    repair = view.first(EvidenceKind.REPAIR_SIGNAL)
    repair_id = repair.id if repair is not None else None
    visible = view.first(EvidenceKind.EVIDENCE_VISIBLE_REFS)
    visible_id = visible.id if visible is not None else None
    plan_item = view.first(EvidenceKind.PLAN_PROJECTION)
    plan_id = plan_item.id if plan_item is not None else None

    results: list[GradeResult] = []

    # 1. structured_output -- completed plans and degraded fallback plans both
    #    carry a persisted plan. Other degraded terminals carry a structured
    #    clarification or safe response.
    if outcome.status == "completed":
        structured_ok = outcome.final_plan_id is not None and outcome.plan is not None
        structured_expected = "final_plan_id present + plan projection present"
    elif outcome.status == "degraded":
        if outcome.result_kind == "plan":
            structured_ok = outcome.final_plan_id is not None and outcome.plan is not None
            structured_expected = (
                "fallback plan has final_plan_id + plan projection present"
            )
        else:
            seen = {e.get("event_type") for e in outcome.events}
            structured_ok = outcome.result_kind in {"clarification", "safe_response"} and (
                "clarification.requested" in seen or "run.degraded" in seen
            )
            structured_expected = (
                "result_kind in {clarification,safe_response} "
                "+ clarification.requested or run.degraded event"
            )
    elif outcome.status in {"failed", "cancelled"}:
        structured_ok = True
        structured_expected = "no structured-output expectation on failed/cancelled runs"
    else:
        structured_ok = False
        structured_expected = "unknown status"
    results.append(_boolean_grade(
        name="structured_output",
        passed=structured_ok,
        actual={"status": outcome.status, "result_kind": outcome.result_kind,
                "has_plan": outcome.plan is not None},
        expected=structured_expected,
        evidence_ids=[metrics_id] if metrics_id else [],
        rationale="model output must satisfy the structural contract for the run terminal",
    ))

    # 2. format_repair_count -- ≤1 format repair.
    repair_raw = repair.projection.get("format_repair_attempts", 0) if repair else 0
    repair_count = int(repair_raw) if isinstance(repair_raw, (int, float)) else 0
    if outcome.result_kind == "plan" and outcome.status in {"completed", "degraded"}:
        results.append(_boolean_grade(
            name="format_repair_count",
            passed=repair_count <= 1,
            actual=repair_count, expected="<= 1",
            evidence_ids=[repair_id] if repair_id else [],
            rationale="model output may fix structure at most once before fallback",
        ))
    else:
        results.append(_not_applicable(
            "format_repair_count",
            [repair_id] if repair_id else [],
            "non-plan runs do not exercise the plan format-repair contract",
        ))

    # 3. evidence_visibility -- plan evidence_refs must be a subset of the
    #    visible_refs evidence captured by the collector. This is the PR-1
    #    invariant that the Model grader pins at the evaluation layer.
    plan_projection = plan_item.projection if plan_item is not None else {}
    plan_refs_raw: list[dict[str, object]] = as_dict_list(
        plan_projection.get("evidence_refs", []) or []
    )
    if plan_refs_raw or outcome.result_kind == "plan":
        if visible is None:
            evidence_ok = False
            actual_refs: list[object] = list(plan_refs_raw)
            visible_refs_str: object = "<missing visible_refs evidence>"
        else:
            visible_refs_raw = as_dict_list(visible.projection.get("visible_refs", []) or [])
            plan_ref_set = {
                f"{r.get('kind')}:{r.get('id')}"
                for r in plan_refs_raw if isinstance(r, dict)
            }
            visible_ref_set = {
                f"{r.get('kind')}:{r.get('id')}"
                for r in visible_refs_raw if isinstance(r, dict)
            }
            outside = plan_ref_set - visible_ref_set
            evidence_ok = not outside
            sorted_outside = sorted(outside)
            actual_refs = list(sorted_outside) if sorted_outside else list(plan_refs_raw)
            visible_refs_str = sorted(visible_ref_set)
        results.append(_boolean_grade(
            name="evidence_visibility",
            passed=evidence_ok,
            actual={"plan_refs_outside_visibility": actual_refs if not evidence_ok else []},
            expected=f"refs in visible {visible_refs_str}",
            evidence_ids=[item_id for item_id in (plan_id, visible_id) if item_id],
            rationale="every Plan evidence_ref must be present in the call's visible_refs window",
        ))
    else:
        results.append(_not_applicable(
            "evidence_visibility", [],
            "no plan projection to verify",
        ))

    # 4. token_usage_nonzero -- quality (soft). Mock provider should still emit >0 tokens.
    total = outcome.total_tokens_in + outcome.total_tokens_out
    # memory_grounded -- quality signal (hard_gate=False): the plan TEXT must
    # actually use the planted Personal memories, not merely have the
    # memory tool invoked. Closes the ablation gap where the memory layer's
    # measured value was tool-matching only (risk item #3).
    memories = [
        str(item.get("content", "")).strip()
        for item in as_dict_list(expected.scenario.confirmed_memories)
        if str(item.get("category", "relevant")) == "relevant"
        and str(item.get("content", "")).strip()
    ]
    if not memories:
        results.append(_not_applicable(
            "memory_grounded", [],
            "case plants no relevant memories",
        ))
    elif plan_item is None or not plan_item.projection:
        results.append(_not_applicable(
            "memory_grounded", [],
            "no plan projection to verify grounding against",
        ))
    else:
        plan_text = " ".join(
            str(part)
            for part in (
                plan_item.projection.get("summary"),
                plan_item.projection.get("rationale"),
            )
            if part
        )
        for task_item in view.items(EvidenceKind.TASK_PROJECTION):
            projection = getattr(task_item, "projection", None) or {}
            plan_text += " " + " ".join(
                str(part)
                for part in (
                    projection.get("title"),
                    projection.get("deliverable"),
                    projection.get("starter_action"),
                )
                if part
            )
        plan_bigrams = _text_bigrams(plan_text)
        grounded = 0
        for memory in memories:
            memory_bigrams = _text_bigrams(memory)
            if not memory_bigrams:
                continue
            hit = len(memory_bigrams & plan_bigrams) / len(memory_bigrams)
            if hit >= 0.10:
                grounded += 1
        need = max(1, (len(memories) + 1) // 2)
        results.append(GradeResult(
            grader_name=f"{GRADER_NAME_PREFIX}.memory_grounded",
            grader_version=GRADER_VERSION,
            domain="model",
            metric_type="boolean",
            passed=grounded >= need, hard_gate=False,
            evidence_item_ids=[plan_id] if plan_id else [],
            evidence={
                "grounded_count": grounded,
                "planted_count": len(memories),
                "required": need,
                "subgrader": "memory_grounded",
            },
            rationale=(
                "at least half of the planted Personal memories must leave "
                "lexical traces in the plan text (bigram hit ratio >= 0.10)"
            ),
        ))

    results.append(_numeric_grade(
        name="token_usage_nonzero",
        score=float(total),
        threshold=0.0,
        evidence_ids=[metrics_id] if metrics_id else [],
        rationale="tokens should be non-zero on any real model interaction",
    ))

    # 5. PR-8b: evidence_citation_precision/recall -- counterfactual axis.
    #    Compares PlanCandidate.evidence_refs (Memory UUIDs) against the
    #    expected_citations strings declared on the Case. The collector
    #    emits an EXPECTED_CITATIONS_MAP item that translates the strings
    #    into UUIDs so the two sets can be intersected. Returns N/A rows
    #    (subgrader=N/A shape) when there is no expected citation set or
    #    no plan projection, preserving the per-Trial score contract.
    citation_results = _evidence_citation_grades(
        view=view, expected=expected, evidence_ids=[metrics_id] if metrics_id else []
    )
    results.extend(citation_results)

    return results


def _evidence_citation_grades(
    *,
    view: AuthorizedView,
    expected: EvalCase,
    evidence_ids: list[UUID],
) -> list[GradeResult]:
    """PR-8b Evidence Citation precision + recall.

    Emits two numeric grades (``evidence_citation_precision`` /
    ``evidence_citation_recall``). When the case carries no
    ``expected_citations`` fixture or the runtime produced no PLAN_PROJECTION,
    emits two N/A rows instead so consumers see a deterministic count.
    """

    from evals.v2.contracts import PlanningEvalScenario

    scenario = expected.scenario
    if not isinstance(scenario, PlanningEvalScenario):
        raise TypeError("citation grader requires a planning scenario")
    no_expected = scenario.provider_fixtures.get("expected_citations")
    if not isinstance(no_expected, list) or not no_expected:
        return [
            _not_applicable(
                "evidence_citation_precision", evidence_ids,
                "case does not declare expected_citations",
            ),
            _not_applicable(
                "evidence_citation_recall", evidence_ids,
                "case does not declare expected_citations",
            ),
        ]

    plan_proj = view.first(EvidenceKind.PLAN_PROJECTION)
    if plan_proj is None or not plan_proj.projection:
        return [
            _not_applicable(
                "evidence_citation_precision", evidence_ids,
                "no plan projection to verify",
            ),
            _not_applicable(
                "evidence_citation_recall", evidence_ids,
                "no plan projection to verify",
            ),
        ]

    plan_refs_raw = as_dict_list(plan_proj.projection.get("evidence_refs") or [])
    actual_ids = {
        str(ref.get("id"))
        for ref in plan_refs_raw
        if isinstance(ref, dict) and ref.get("id")
    }
    expected_strs = [s for s in no_expected if isinstance(s, str) and s]

    mapping_item = view.first(EvidenceKind.EXPECTED_CITATIONS_MAP)
    raw_mapping = (
        mapping_item.projection.get("expected_citations_map")
        if mapping_item is not None
        else None
    )
    mapping_dict = raw_mapping if isinstance(raw_mapping, dict) else {}
    expected_uuids = {
        str(mapping_dict[s])
        for s in expected_strs
        if isinstance(mapping_dict.get(s), str)
    }

    hits = actual_ids & expected_uuids
    precision = (len(hits) / len(actual_ids)) if actual_ids else 0.0
    recall = (len(hits) / len(expected_uuids)) if expected_uuids else 1.0

    precision_threshold = 0.5
    recall_threshold = 0.5
    shared_evidence_ids = list(evidence_ids)
    if mapping_item is not None and mapping_item.id is not None:
        shared_evidence_ids.append(mapping_item.id)

    return [
        _numeric_grade(
            name="evidence_citation_precision",
            score=precision,
            threshold=precision_threshold,
            evidence_ids=shared_evidence_ids,
            actual={
                "hits": sorted(hits),
                "actual_refs": sorted(actual_ids),
                "expected_uuids": sorted(expected_uuids),
            },
            expected=f"refs subset of {sorted(expected_uuids)}",
            rationale=(
                "fraction of plan evidence_refs that match expected_citations"
            ),
        ),
        _numeric_grade(
            name="evidence_citation_recall",
            score=recall,
            threshold=recall_threshold,
            evidence_ids=shared_evidence_ids,
            actual={
                "hits": sorted(hits),
                "missing": sorted(expected_uuids - actual_ids),
            },
            expected=f"refs cover {sorted(expected_uuids)}",
            rationale=(
                "fraction of expected_citations that appear in plan evidence_refs"
            ),
        ),
    ]
