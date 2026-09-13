"""Registry for the six V2 domain Graders.

The registry is the single source of truth for which Graders run, what their
frozen ``allowed_evidence_kinds`` are, and how an ``EvidenceItem`` list is
turned into the ``AuthorizedView`` each Grader sees. ``grade_all`` is the
entry point ``EvalService.grade_trial`` calls; it:

1. iterates the registered Graders in spec order (System → Safety → Tool →
   Behavioral → Task → Model),
2. builds an ``AuthorizedView`` filtering items to that Grader's allowed
   kinds (anything else is unrecoverable),
3. runs the Grader, collecting its ``list[GradeResult]``.

A Grader never receives the full item list, the Registry, an AsyncSession, or
another Grader's results. ``EvidenceAccessDenied`` is the defensive path for
Graders that try to widen their view via ``AuthorizedView.items unauthorized
kind``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.graders.base import (
    AuthorizedView,
    EvidenceItem,
    EvidenceKind,
    Grader,
    authorize,
)
from evals.v2.graders.behavioral import ALLOWED_KINDS as BEHAVIORAL_KINDS
from evals.v2.graders.behavioral import grade as grade_behavioral
from evals.v2.graders.model import ALLOWED_KINDS as MODEL_KINDS
from evals.v2.graders.model import grade as grade_model
from evals.v2.graders.safety import ALLOWED_KINDS as SAFETY_KINDS
from evals.v2.graders.safety import grade as grade_safety
from evals.v2.graders.system import ALLOWED_KINDS as SYSTEM_KINDS
from evals.v2.graders.system import grade as grade_system
from evals.v2.graders.task import ALLOWED_KINDS as TASK_KINDS
from evals.v2.graders.task import grade as grade_task
from evals.v2.graders.tool import ALLOWED_KINDS as TOOL_KINDS
from evals.v2.graders.tool import grade as grade_tool

if TYPE_CHECKING:
    from evals.v2.collectors.outcome import RunOutcome
    from evals.v2.contracts import EvalCase, GradeResult


# Spec implementation order: System → Safety → Tool → Behavioral → Task → Model.
_REGISTERED: tuple[Grader, ...] = (
    Grader(
        name="system",
        version="v1",
        domain="system",
        allowed_evidence_kinds=SYSTEM_KINDS,
        grade_fn=grade_system,
    ),
    Grader(
        name="safety",
        version="v1",
        domain="safety",
        allowed_evidence_kinds=SAFETY_KINDS,
        grade_fn=grade_safety,
    ),
    Grader(
        name="tool",
        version="v1",
        domain="tool",
        allowed_evidence_kinds=TOOL_KINDS,
        grade_fn=grade_tool,
    ),
    Grader(
        name="behavioral",
        version="v1",
        domain="behavioral",
        allowed_evidence_kinds=BEHAVIORAL_KINDS,
        grade_fn=grade_behavioral,
    ),
    Grader(
        name="task",
        version="v1",
        domain="task",
        allowed_evidence_kinds=TASK_KINDS,
        grade_fn=grade_task,
    ),
    Grader(
        name="model",
        version="v1",
        domain="model",
        allowed_evidence_kinds=MODEL_KINDS,
        grade_fn=grade_model,
    ),
)


def registered_graders() -> tuple[Grader, ...]:
    """Return the frozen Grader tuple (snapshot is pinned by tests)."""

    return _REGISTERED


def allowed_kinds_snapshot() -> dict[str, frozenset[EvidenceKind]]:
    """Map ``{grader_name: frozenset[EvidenceKind]}`` for authorization tests."""

    return {grader.name: grader.allowed_evidence_kinds for grader in _REGISTERED}


async def grade_all(
    *,
    trial_id: UUID,
    outcome: RunOutcome,
    evidence_items: list[EvidenceItem],
    expected: EvalCase,
) -> list[GradeResult]:
    """Run every registered Grader in spec order, returning all GradeResults.

    Each Grader receives only an ``AuthorizedView`` containing the subset of
    ``evidence_items`` whose ``kind`` is in its ``allowed_evidence_kinds``. A
    Grader that attempts to read evidence outside its whitelist raises
    ``EvidenceAccessDenied`` (caught by the caller's persistence layer; tests
    additionally assert this never happens for in-bounds runs).
    """

    results: list[GradeResult] = []
    for grader in _REGISTERED:
        view: AuthorizedView = authorize(
            trial_id=trial_id,
            items=evidence_items,
            allowed_kinds=grader.allowed_evidence_kinds,
        )
        results.extend(await grader.grade(outcome, view, expected))
    return results
