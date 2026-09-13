"""Evidence authorization layer and base Grader abstraction.

PR-4 introduces the contract every domain Grader honours:

* a Grader declares a frozen ``allowed_evidence_kinds`` set;
* the Registry constructs an ``EvidenceView`` that filters the trial's
  ``EvidenceItem`` rows to *only* those whose ``kind`` is in that set;
* any attempt by a Grader to read an item outside its allowed kinds raises
  ``EvidenceAccessDenied`` -- for use in tests and as a guard against future
  Graders that try to peek at raw transcripts, other Graders' scores, or
  user-sensitive raw content.

The authorized view is the only object passed to ``Grader.grade``. There is no
way for a Grader to receive the underlying ``EvidenceItem`` list, the
``RunOutcome``, the ``EvalTrial``, an AsyncSession, or another Grader's
``GradeResult`` -- the spec contract "Grader 不接收完整 TrialBundle /
Repository / Session / 其他 Grader 结果".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from evals.v2.contracts import GradeResult

if TYPE_CHECKING:
    from evals.v2.collectors.outcome import RunOutcome
    from evals.v2.contracts import EvalCase


class EvidenceKind(StrEnum):
    """The set of evidence kinds the collector can emit.

    Deliberately closed: adding a kind is a Grader-authorization change and
    must update the per-domain ``allowed_evidence_kinds`` snapshots plus the
    ``test_each_grader_allowed_kinds_frozen`` test.
    """

    REQUEST_CONSTRAINTS = "request_constraints"
    PROFILE_PROJECTION = "profile_projection"
    EXPECTED_OUTCOME = "expected_outcome"
    TRAJECTORY_POLICY = "trajectory_policy"
    RUBRIC = "rubric"
    PLAN_PROJECTION = "plan_projection"
    TASK_PROJECTION = "task_projection"
    STEP_PROJECTION = "step_projection"
    EVENT_PROJECTION = "event_projection"
    TOOL_CALL_PROJECTION = "tool_call_projection"
    TOOL_SPEC = "tool_spec"
    RUN_METRICS = "run_metrics"
    OUTCOME_STATUS = "outcome_status"
    EVIDENCE_VISIBLE_REFS = "evidence_visible_refs"
    TRANSCRIPT_HASH = "transcript_hash"
    RISK_SIGNALS = "risk_signals"
    REDACTED_OUTPUT = "redacted_output"
    CROSS_USER_SIGNAL = "cross_user_signal"
    TOOL_ALLOWLIST = "tool_allowlist"
    REPAIR_SIGNAL = "repair_signal"
    PROVIDER_CALL_PROJECTION = "provider_call_projection"

    # PR-8b: synthetic kind emitted by collect_evidence carrying the
    # {fixture_memory_id -> planted Memory UUID} map. Consumed by the
    # evidence_citation_precision_recall sub-grader to translate the
    # expected_citations strings (set on the Case) into the UUID form
    # that PlanCandidate.evidence_refs actually carries.
    EXPECTED_CITATIONS_MAP = "expected_citations_map"
    INTERVIEW_QUESTION = "interview_question"
    INTERVIEW_ANALYSIS = "interview_analysis"
    INTERVIEW_REPORT = "interview_report"
    RESUME_CLAIM_FINDING = "resume_claim_finding"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One piece of evidence the collector froze for one Trial.

    ``content_hash`` makes the "相同 evidence content hash 变化使旧评分不可
    静默复用" invariant enforceable: the ``eval_evidence_items`` table has a
    UNIQUE constraint over ``(trial_id, kind, source_type, source_id)``, so a
    re-collect with changed content produces a different row; the old Score's
    ``evidence_item_ids`` then reference nothing and must be regenerated.
    """

    id: UUID
    trial_id: UUID
    kind: EvidenceKind
    source_type: str
    source_id: str
    content_hash: str
    projection: dict[str, object]
    sensitivity: str = "normal"


class EvidenceAccessDenied(Exception):
    """Raised when a Grader requests evidence outside its allowed kinds."""

    def __init__(self, grader_name: str, requested: EvidenceKind) -> None:
        self.grader_name = grader_name
        self.requested = requested
        super().__init__(
            f"grader {grader_name!r} requested unauthorized evidence kind {requested.value!r}"
        )


@dataclass(frozen=True, slots=True)
class AuthorizedView:
    """Filtered evidence view handed to one Grader.

    Only items whose ``kind`` is in ``allowed_kinds`` are exposed. The view
    also remembers the *full* kind set the Trial owns so a Grader that asks
    for everything can be detected (and refused) without leaking which other
    kinds exist.
    """

    trial_id: UUID
    allowed_kinds: frozenset[EvidenceKind]
    items_by_kind: dict[EvidenceKind, list[EvidenceItem]] = field(default_factory=dict)

    def items(self, kind: EvidenceKind) -> list[EvidenceItem]:
        if kind not in self.allowed_kinds:
            # The view is constructed by GraderRegistry.authorize(), which
            # is the only legitimate caller; reaching this branch from inside
            # a Grader means the Grader tried to fabricate a kind lookup.
            raise EvidenceAccessDenied(grader_name="<unknown>", requested=kind)
        return self.items_by_kind.get(kind, [])

    def first(self, kind: EvidenceKind) -> EvidenceItem | None:
        items = self.items(kind)
        return items[0] if items else None


# Type aliases kept narrow and explicit for static analysis.
GradeFn = Callable[
    ["RunOutcome", AuthorizedView, "EvalCase"],
    Awaitable[list[GradeResult]],
]


@dataclass(frozen=True, slots=True)
class Grader:
    """One deterministic Grader bound to a single domain.

    Instances are registered in ``GraderRegistry`` along with their frozen
    ``allowed_evidence_kinds``. The Registry's ``authorize`` step is the
    single place that turns an EvidenceItem list into an ``AuthorizedView``;
    Graders never see unfiltered evidence.
    """

    name: str
    version: str
    domain: str  # "task" / "behavioral" / "tool" / "model" / "system" / "safety"
    allowed_evidence_kinds: frozenset[EvidenceKind]
    grade_fn: GradeFn

    async def grade(
        self,
        outcome: RunOutcome,
        view: AuthorizedView,
        expected: EvalCase,
    ) -> list[GradeResult]:
        return await self.grade_fn(outcome, view, expected)


def authorize(
    *,
    trial_id: UUID,
    items: list[EvidenceItem],
    allowed_kinds: frozenset[EvidenceKind],
) -> AuthorizedView:
    """Build the ``AuthorizedView`` for one Grader call.

    Visible to the Registry only. Graders receive the resulting view, not this
    function, so they cannot widen their own kind set.
    """

    by_kind: dict[EvidenceKind, list[EvidenceItem]] = {}
    for item in items:
        if item.kind in allowed_kinds:
            by_kind.setdefault(item.kind, []).append(item)
    return AuthorizedView(
        trial_id=trial_id,
        allowed_kinds=allowed_kinds,
        items_by_kind=by_kind,
    )


def as_list(value: object) -> list[object]:
    """Cast an evidence-projection field to ``list[object]`` safely.

    Evidence rows are JSON-backed (``dict[str, object]``); the typical pattern
    is ``item.projection.get("refs", [])`` which mypy widens to ``object``.
    This helper restores the outer list type so iteration stays type-clean.
    Callers that need ``list[dict[str, object]]`` should add their own
    ``isinstance(item, dict)`` filter on top.
    """

    if not isinstance(value, list):
        return []
    return list(value)


def as_dict_list(value: object) -> list[dict[str, object]]:
    """``as_list`` variant for evidence-projection fields known to be dict lists.

    Drops any non-dict elements so downstream dict-only comprehension stays
    type-clean.
    """

    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def as_int(value: object, *, default: int = 0) -> int:
    """Cast an evidence-projection field to ``int`` safely."""

    return int(value) if isinstance(value, (int, float)) else default
