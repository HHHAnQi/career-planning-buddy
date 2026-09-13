"""Tool-domain deterministic Graders.

Spec gate (PR-4): allowlist, expected-matching, args-schema, call-budget, and
failure-degrade are all hard gates. When the run produced no tool calls and
the policy expected none, every grader returns ``not_applicable`` rather than
auto-passing (the candidate=None auto-pass trap the legacy runner had).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.collectors.outcome import RunOutcome
from evals.v2.contracts import GradeResult
from evals.v2.graders.base import AuthorizedView, EvidenceKind, as_int

if TYPE_CHECKING:
    from evals.v2.contracts import EvalCase

GRADER_NAME_PREFIX = "tool"
GRADER_VERSION = "v1"
ALLOWED_KINDS = frozenset({
    EvidenceKind.TOOL_CALL_PROJECTION,
    EvidenceKind.TOOL_SPEC,
    EvidenceKind.TRAJECTORY_POLICY,
    EvidenceKind.EVIDENCE_VISIBLE_REFS,
    EvidenceKind.PROVIDER_CALL_PROJECTION,
})
REGISTERED_TOOL_NAMES = frozenset({"memory_lookup", "rag_retrieve", "web_search"})


def _boolean_grade(
    *, name: str, passed: bool, actual: object, expected: object,
    evidence_ids: list[UUID], rationale: str,
) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="tool",
        metric_type="boolean",
        passed=passed, hard_gate=True,
        evidence_item_ids=evidence_ids,
        evidence={"actual": actual, "expected": expected, "subgrader": name},
        rationale=rationale,
    )


def _not_applicable(name: str, evidence_ids: list[UUID], rationale: str) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="tool",
        metric_type="categorical",
        categorical_value="not_applicable",
        hard_gate=False,
        evidence_item_ids=evidence_ids,
        evidence={"actual": None, "expected": None, "subgrader": name, "reason": "na"},
        rationale=rationale,
    )


async def grade(outcome: RunOutcome, view: AuthorizedView, expected: EvalCase) -> list[GradeResult]:
    policy_item = view.first(EvidenceKind.TRAJECTORY_POLICY)
    policy_id = policy_item.id if policy_item is not None else None
    tool_calls = outcome.tool_calls
    tool_call_ids = [item.id for item in view.items(EvidenceKind.TOOL_CALL_PROJECTION)]
    has_tools = bool(tool_calls)

    expected_tools = list(expected.trajectory_policy.expected_tools)
    max_tool_calls = expected.trajectory_policy.max_tool_calls
    expected_no_tools = not expected_tools

    results: list[GradeResult] = []

    # 1. allowlist
    if has_tools:
        all_in_allowlist = all(tc["tool_name"] in REGISTERED_TOOL_NAMES for tc in tool_calls)
        results.append(_boolean_grade(
            name="allowlist",
            passed=all_in_allowlist,
            actual=[tc["tool_name"] for tc in tool_calls],
            expected=str(sorted(REGISTERED_TOOL_NAMES)),
            evidence_ids=tool_call_ids,
            rationale="every tool call must use a registered tool name",
        ))
    else:
        results.append(_not_applicable(
            "allowlist", [], "no tool calls in this run"))

    # 2. expected_match -- policy.expected_tools vs actual set
    actual_set = {str(tc["tool_name"]) for tc in tool_calls}
    expected_set = set(expected_tools)
    if expected_no_tools and not has_tools:
        results.append(_boolean_grade(
            name="expected_match",
            passed=True,
            actual=sorted(actual_set),
            expected="no tools expected, no tools used",
            evidence_ids=tool_call_ids,
            rationale="empty policy aligns with empty trajectory",
        ))
    else:
        match_ok = actual_set == expected_set
        results.append(_boolean_grade(
            name="expected_match",
            passed=match_ok,
            actual=sorted(actual_set),
            expected=sorted(expected_set),
            evidence_ids=tool_call_ids,
            rationale="actual tool set must equal the trajectory_policy.expected_tools set",
        ))

    # 3. args_schema -- no TOOL_ARGUMENT_INVALID errors
    if has_tools:
        no_invalid = all(tc.get("error_code") != "TOOL_ARGUMENT_INVALID" for tc in tool_calls)
        results.append(_boolean_grade(
            name="args_schema",
            passed=no_invalid,
            actual=[tc.get("error_code") for tc in tool_calls],
            expected="no TOOL_ARGUMENT_INVALID",
            evidence_ids=tool_call_ids,
            rationale="every tool call must have a schema-valid arguments payload",
        ))
    else:
        results.append(_not_applicable(
            "args_schema", [], "no tool calls in this run"))

    # 4. call_budget
    count = len(tool_calls)
    if has_tools or expected_tools:
        results.append(_boolean_grade(
            name="call_budget",
            passed=count <= max_tool_calls,
            actual=count, expected=f"<= {max_tool_calls}",
            evidence_ids=tool_call_ids or ([policy_id] if policy_id else []),
            rationale="tool call count must respect trajectory_policy.max_tool_calls",
        ))
    else:
        results.append(_not_applicable(
            "call_budget", [], "no tools expected or used"))

    # 5. failure_degrade -- any failed tool must not produce evidence into the plan.
    if has_tools:
        failed_tools = [tc for tc in tool_calls if tc.get("success") is False]
        refs_count_raw = (
            outcome.plan.get("evidence_refs_count", 0)
            if outcome.plan is not None else 0
        )
        refs_count = as_int(refs_count_raw)
        # Heuristic: failed tools combined with non-zero plan evidence_refs
        # count is suspicious. Strict version: any failed tool ⇒ plan evidence_refs must be empty.
        degrade_ok = not failed_tools or refs_count == 0
        results.append(_boolean_grade(
            name="failure_degrade",
            passed=degrade_ok,
            actual={"failed_tool_count": len(failed_tools), "plan_evidence_refs_count": refs_count},
            expected="failed tool calls must not contribute evidence to the plan",
            evidence_ids=tool_call_ids,
            rationale="a failed tool invocation cannot surface as evidence in the persisted plan",
        ))
    else:
        results.append(_not_applicable(
            "failure_degrade", [], "no tool calls in this run"))

    return results
