"""System-domain deterministic Graders.

Spec gate (PR-4): every grader here is a ``hard_gate``. System owns the
runtime-level invariants -- terminal uniqueness, terminal-last, state-machine
consistency, completed-run-has-plan, failed-no-plan, transcript-hash shape,
token/latency non-negativity -- that must hold regardless of which other
domain produced a quality signal.

Per the design decision: System does NOT query the DB to verify cross-user
isolation. That responsibility falls to the Safety grader's
``cross_user_no_leak`` check using ``plan.task.user_id`` projections. System
stays a pure function over ``RunOutcome``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.collectors.outcome import RunOutcome, terminal_events
from evals.v2.contracts import GradeResult
from evals.v2.graders.base import AuthorizedView, EvidenceKind

if TYPE_CHECKING:
    from evals.v2.contracts import EvalCase

GRADER_NAME_PREFIX = "system"
GRADER_VERSION = "v1"
ALLOWED_KINDS = frozenset({
    EvidenceKind.RUN_METRICS,
    EvidenceKind.EVENT_PROJECTION,
    EvidenceKind.STEP_PROJECTION,
    EvidenceKind.OUTCOME_STATUS,
    EvidenceKind.TRANSCRIPT_HASH,
})

TERMINAL_EVENT_TYPES = frozenset(
    {"run.completed", "run.degraded", "run.failed", "run.cancelled"}
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _boolean_grade(
    *,
    name: str,
    passed: bool,
    actual: object,
    expected: object,
    evidence_id: UUID | None,
    rationale: str,
) -> GradeResult:
    return GradeResult(
        grader_name=f"{GRADER_NAME_PREFIX}.{name}",
        grader_version=GRADER_VERSION,
        domain="system",
        metric_type="boolean",
        passed=passed,
        hard_gate=True,
        evidence_item_ids=[] if evidence_id is None else [evidence_id],
        evidence={
            "actual": actual,
            "expected": expected,
            "subgrader": name,
        },
        rationale=rationale,
    )


async def grade(outcome: RunOutcome, view: AuthorizedView, expected: EvalCase) -> list[GradeResult]:
    del expected  # System checks do not branch on the case's expected side.
    metrics = view.first(EvidenceKind.RUN_METRICS)
    metrics_id = metrics.id if metrics is not None else None
    transcripts = view.items(EvidenceKind.TRANSCRIPT_HASH)
    transcript_id = transcripts[0].id if transcripts else None

    results: list[GradeResult] = []

    # 1. terminal_unique -- exactly one terminal event in the four-type set.
    terminals = terminal_events(outcome)
    results.append(
        _boolean_grade(
            name="terminal_unique",
            passed=len(terminals) == 1,
            actual=len(terminals),
            expected=1,
            evidence_id=metrics_id,
            rationale="exactly one terminal event must be persisted per run",
        )
    )

    # 2. terminal_last -- the last event is one of the four terminal types.
    last_event_type = outcome.events[-1]["event_type"] if outcome.events else None
    last_is_terminal = bool(outcome.events) and last_event_type in TERMINAL_EVENT_TYPES
    results.append(
        _boolean_grade(
            name="terminal_last",
            passed=last_is_terminal,
            actual=last_event_type,
            expected="<one of run.completed|run.degraded|run.failed|run.cancelled>",
            evidence_id=metrics_id,
            rationale="the terminal event must be the last persisted event",
        )
    )

    # 3. state_machine_consistency -- mirror AgentRun's three CHECK constraints.
    state_ok = _state_machine_consistent(outcome)
    results.append(
        _boolean_grade(
            name="state_machine_consistency",
            passed=state_ok,
            actual={
                "status": outcome.status,
                "result_kind": outcome.result_kind,
                "error_code": outcome.error_code,
                "fallback_reason": outcome.fallback_reason,
                "has_plan": outcome.final_plan_id is not None,
            },
            expected={
                "completed": (
                    "result_kind=plan & final_plan_id!=None & "
                    "fallback_reason=None & error_code=None"
                ),
                "degraded": (
                    "result_kind!=None & fallback_reason!=None & error_code=None"
                ),
                "failed/cancelled": (
                    "result_kind=None & final_plan_id=None & error_code!=None"
                ),
            },
            evidence_id=metrics_id,
            rationale=(
                "run row fields must match the AgentRun "
                "completed/degraded/unsuccessful invariants"
            ),
        )
    )

    # 4. completed_run_has_plan
    if outcome.status == "completed":
        has_plan = outcome.final_plan_id is not None and outcome.plan is not None
        results.append(
            _boolean_grade(
                name="completed_run_has_plan",
                passed=has_plan,
                actual={
                    "final_plan_id": (
                        str(outcome.final_plan_id) if outcome.final_plan_id else None
                    ),
                    "plan_projection": "present" if outcome.plan else "absent",
                },
                expected="final_plan_id != None AND plan projection present",
                evidence_id=metrics_id,
                rationale="a completed run must persist a plan",
            )
        )

    # 5. failed_no_plan
    if outcome.status in {"failed", "cancelled"}:
        no_plan = outcome.final_plan_id is None
        results.append(
            _boolean_grade(
                name="failed_no_plan",
                passed=no_plan,
                actual=str(outcome.final_plan_id) if outcome.final_plan_id else None,
                expected="final_plan_id IS NULL for failed/cancelled runs",
                evidence_id=metrics_id,
                rationale="failed or cancelled runs must not have produced a plan",
            )
        )

    # 6. transcript_hash_well_formed
    hash_shape_ok = bool(_HEX64.match(outcome.transcript_hash or ""))
    results.append(
        _boolean_grade(
            name="transcript_hash_well_formed",
            passed=hash_shape_ok,
            actual=outcome.transcript_hash,
            expected="64 lowercase hex digits",
            evidence_id=transcript_id,
            rationale="transcript_hash must match ^[0-9a-f]{64}$",
        )
    )

    # 7. token_latency_nonneg
    nonneg = (
        outcome.total_tokens_in >= 0
        and outcome.total_tokens_out >= 0
        and outcome.total_latency_ms >= 0
    )
    results.append(
        _boolean_grade(
            name="token_latency_nonneg",
            passed=nonneg,
            actual={"tokens_in": outcome.total_tokens_in,
                    "tokens_out": outcome.total_tokens_out,
                    "latency_ms": outcome.total_latency_ms},
            expected="tokens_in >= 0 AND tokens_out >= 0 AND latency_ms >= 0",
            evidence_id=metrics_id,
            rationale="token counts and latency must be non-negative",
        )
    )

    return results


def _state_machine_consistent(outcome: RunOutcome) -> bool:
    if outcome.status == "completed":
        return (
            outcome.result_kind == "plan"
            and outcome.final_plan_id is not None
            and outcome.fallback_reason is None
            and outcome.error_code is None
        )
    if outcome.status == "degraded":
        return (
            outcome.result_kind is not None
            and outcome.fallback_reason is not None
            and outcome.error_code is None
        )
    if outcome.status in {"failed", "cancelled"}:
        return (
            outcome.result_kind is None
            and outcome.final_plan_id is None
            and outcome.error_code is not None
        )
    # pending / running leak into a graded Trial: never consistent.
    return False
