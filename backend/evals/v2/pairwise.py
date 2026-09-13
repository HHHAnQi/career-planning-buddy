"""PR-9c.1 Pairwise Pair contract and authorized Judge input projection.

The Pairwise Judge compares two ``EvalTrial`` outcomes (one baseline + one
candidate) for the same ``case_id``. The Pair is the *stable identity* that
ties together one or more physical Judge executions ("runs"); each run has
its own row in ``eval_pairwise_judge_results`` (PR-9c.1 commit 2) keyed by
``judge_run_id``.

Architecture invariants (see PR-9c.1 plan):

* This module never imports ``app.harness.*`` / ``app.agent.*`` — the Eval
  Harness depends on the Agent runtime, never the reverse.
* The Judge only sees ``PairwiseJudgeInput``; the underlying
  ``baseline_trial_id`` / ``candidate_trial_id`` / model ids / auto-scores
  are deliberately absent. ``build_judge_input`` is the only constructor.
* ``PairwiseJudgeInput.input_hash`` is swap-invariant: it sorts the two
  outputs by ``canonical_sha256`` before hashing, so swapping A and B does
  not change ``input_hash``. The display order is communicated separately
  via ``position_variant`` so the Judge prompt stays positional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from evals.v2.contracts import canonical_sha256
from evals.v2.graders.base import (
    AuthorizedView,
    EvidenceItem,
    EvidenceKind,
)

# Evidence kinds the Pairwise Judge is authorized to read.
#
# Frozen by the spec: the Judge sees the request constraints + the produced
# plan projection + the rubric the human graders intended the run to honour.
# Anything else (transcript, tool calls, raw provider responses, model
# metadata, other Graders' scores) is out of scope and must not leak into
# the prompt. Adding/removing a kind here is an authorization change and
# requires updating ``EXPECTED_JUDGE_ALLOWED_KINDS`` in
# ``test_pairwise_pair``.
JUDGE_ALLOWED_KINDS: frozenset[EvidenceKind] = frozenset({
    EvidenceKind.REQUEST_CONSTRAINTS,
    EvidenceKind.PLAN_PROJECTION,
    EvidenceKind.RUBRIC,
})


class PositionVariant(StrEnum):
    """Which physical display slot ("A") the baseline occupies.

    The Pairwise Judge prompt is positional: it always judges "display_a"
    vs "display_b". ``baseline`` means baseline is shown as A and candidate
    as B; ``swapped`` means baseline is shown as B. Persisting this on each
    Judge result lets the caller unswap a raw display winner into a stable
    baseline-relative ``normalized_winner`` without re-running the Judge.
    """

    BASELINE = "baseline"
    SWAPPED = "swapped"


@dataclass(frozen=True, slots=True)
class TrialEvidenceProjection:
    """Authorized, minimized evidence extracted from one Trial.

    Carries only the projection payloads the Judge is allowed to see, never
    the originating ``EvidenceItem`` ids or the trial id. Two projections
    that are byte-identical must hash equal so the swap-invariant
    ``input_hash`` holds.
    """

    request_constraints: dict[str, object] | None
    plan_projection: dict[str, object] | None

    def as_display(self) -> dict[str, object]:
        """Render the projection as one opaque "display" payload.

        The Judge sees ``{"request": ..., "plan": ...}`` for both sides and
        must not be told which side is baseline. Keys are intentionally
        neutral so a swapped Pair renders the same JSON with display_a /
        display_b exchanged.
        """

        payload: dict[str, object] = {}
        if self.request_constraints is not None:
            payload["request"] = self.request_constraints
        if self.plan_projection is not None:
            payload["plan"] = self.plan_projection
        return payload


@dataclass(frozen=True, slots=True)
class Pair:
    """Stable identity of one baseline-vs-candidate Pair.

    The ``pair_hash`` is computed from *role-determined* (not sorted) trial
    ids, the case_id, and stable output content hashes, so the same two
    trials with equivalent outputs always produce the same Pair row
    regardless of which side is currently shown as A or which comparison
    group / Judge version is running. ``comparison_group_id`` is NOT part
    of Pair identity — it lives on ``EvalPairwiseJudgeResult`` rows.
    """

    baseline_trial_id: UUID
    candidate_trial_id: UUID
    case_id: str
    baseline_projection: TrialEvidenceProjection
    candidate_projection: TrialEvidenceProjection

    def baseline_output_hash(self) -> str:
        """Stable content fingerprint of the baseline projection payload.

        Computed over the same bytes the Judge would see; if a re-collect
        changes the plan content, the fingerprint changes, and the Pair is
        treated as a different Pair. This is the contract that lets
        ``eval_trial_pairs`` stay unique per *equivalent* comparison
        rather than per trial-id pair.
        """

        return canonical_sha256(self.baseline_projection.as_display())

    def candidate_output_hash(self) -> str:
        """Stable content fingerprint of the candidate projection payload."""

        return canonical_sha256(self.candidate_projection.as_display())

    def pair_hash(self) -> str:
        """Stable identity hash for one baseline-vs-candidate Pair.

        Deliberately EXCLUDES any per-execution field — ``comparison_group_id``,
        ``judge_run_id``, ``position_variant``, ``judge_model``, prompt /
        rubric versions — so that the same baseline/candidate trial pair
        with the same outputs keeps a stable identity across:

        * multiple ``comparison_group_id`` runs (a re-evaluation with a
          new Judge prompt / model should NOT create a new Pair row);
        * PR-9c.2's human-label attaching workflow;
        * cross-prompt / cross-model consistency analysis.

        What IS included:

        * ``schema_version`` — bump when the hashing algorithm changes, so
          old Pairs do not silently alias to new ones;
        * ``case_id`` — two Trials from different cases are not a Pair;
        * role-determined trial refs — swapping baseline/candidate is a
          different Pair (verdict semantics are baseline-relative);
        * baseline/candidate output hashes — if a re-collect moves output
          bytes, the old Pair row stays attributable to the old bytes and
          the new run gets a new Pair row, which is what we want.

        ``comparison_group_id`` is recorded on ``EvalPairwiseJudgeResult``
        rows instead, where it ties together one original+swapped run pair.
        """

        return canonical_sha256({
            "schema_version": "eval-trial-pair/v1",
            "case_id": self.case_id,
            "baseline_trial_id": str(self.baseline_trial_id),
            "candidate_trial_id": str(self.candidate_trial_id),
            "baseline_output_hash": self.baseline_output_hash(),
            "candidate_output_hash": self.candidate_output_hash(),
        })


@dataclass(frozen=True, slots=True)
class PairwiseJudgeInput:
    """The frozen, authorized input handed to one Judge execution.

    ``display_a`` / ``display_b`` are positional, fully blinded payloads —
    the Judge cannot tell which side is baseline. ``position_variant``
    records which slot the baseline occupies for this run. ``input_hash`` is
    swap-invariant: it sorts the two projections by hash before serializing,
    so swapped and non-swapped runs of the same Pair share ``input_hash``.
    """

    pair: Pair
    judge_run_id: UUID
    position_variant: PositionVariant
    rubric: list[dict[str, object]]
    display_a: dict[str, object]
    display_b: dict[str, object]
    input_hash: str
    judge_prompt_version: str = "v1"
    judge_rubric_version: str = "v1"
    # Frozen at construction so Judge results are attributable to the exact
    # input bytes the LLM saw. Defaults keep the field optional in tests.
    allowed_evidence_kinds: frozenset[str] = field(
        default_factory=lambda: frozenset(kind.value for kind in JUDGE_ALLOWED_KINDS)
    )


def _extract_min_projection(view: AuthorizedView) -> TrialEvidenceProjection:
    """Pull only the two authorized kinds out of an ``AuthorizedView``.

    ``AuthorizedView`` is constructed by the caller (the EvalService) with
    ``JUDGE_ALLOWED_KINDS``; if for any reason the view was built with a
    wider set, this function still only reads the two intended kinds, so
    the projection cannot leak unrelated evidence.
    """

    return TrialEvidenceProjection(
        request_constraints=_first_projection(view, EvidenceKind.REQUEST_CONSTRAINTS),
        plan_projection=_first_projection(view, EvidenceKind.PLAN_PROJECTION),
    )


def _first_projection(view: AuthorizedView, kind: EvidenceKind) -> dict[str, object] | None:
    """Read the first projection payload of ``kind`` from the view, if any.

    Defensive against views authorized with a narrower set than
    ``JUDGE_ALLOWED_KINDS``: when a caller built a view without this kind
    (e.g. the legacy Trial that lacks RUBRIC evidence altogether), we return
    ``None`` rather than raising ``EvidenceAccessDenied``. The grader-side
    authorization guard remains intact because this function never reads a
    kind that the view refused to expose — it just degrades gracefully.
    """

    if kind not in view.allowed_kinds:
        return None
    item: EvidenceItem | None = view.first(kind)
    if item is None:
        return None
    return dict(item.projection)


def _rubric_from(view: AuthorizedView) -> list[dict[str, object]]:
    """Materialize the rubric payload from the authorized RUBRIC evidence.

    The rubric is stored as a single ``RUBRIC`` evidence item whose
    ``projection`` carries ``{"criteria": [...]}``. If absent (a Trial that
    pre-dates the rubric kind), the Pair judges against an empty rubric —
    the Judge is the only arbiter in that case. We explicitly do NOT fall
    back to the Case rubric here, because the Case is not part of the
    authorized view and pulling it in would violate invariant #2.
    """

    item = view.first(EvidenceKind.RUBRIC)
    if item is None:
        return []
    criteria = item.projection.get("criteria")
    if not isinstance(criteria, list):
        return []
    return [c for c in criteria if isinstance(c, dict)]


def build_pair(
    *,
    baseline_trial_id: UUID,
    candidate_trial_id: UUID,
    case_id: str,
    baseline_view: AuthorizedView,
    candidate_view: AuthorizedView,
) -> Pair:
    """Construct a ``Pair`` from two authorized trial views.

    Both views must already be filtered to ``JUDGE_ALLOWED_KINDS`` — this
    function does not widen or narrow them. It extracts the two projections
    and freezes them on the Pair. ``comparison_group_id`` is NOT taken
    here: it is a per-execution attribute that lives on
    ``EvalPairwiseJudgeResult`` rows, not on the Pair.
    """

    return Pair(
        baseline_trial_id=baseline_trial_id,
        candidate_trial_id=candidate_trial_id,
        case_id=case_id,
        baseline_projection=_extract_min_projection(baseline_view),
        candidate_projection=_extract_min_projection(candidate_view),
    )


def build_judge_input(
    *,
    pair: Pair,
    judge_run_id: UUID,
    baseline_view: AuthorizedView,
    candidate_view: AuthorizedView,
    position_variant: PositionVariant,
) -> PairwiseJudgeInput:
    """Assemble one frozen, blinded Judge input from a Pair.

    Position assignment is decided *outside* (by the caller from
    ``ab_seed`` / ``force_position_variant``) and is asserted here via the
    explicit ``position_variant`` argument; this function never reads the
    Pair's trial ids to decide A/B. It only places baseline and candidate
    into the correct display slot according to ``position_variant``.

    ``input_hash`` is swap-invariant: the two display payloads are sorted
    by their canonical hash before serialization, so a swapped run and a
    non-swapped run of the same Pair share the same ``input_hash``.
    """

    baseline_display = pair.baseline_projection.as_display()
    candidate_display = pair.candidate_projection.as_display()
    if position_variant is PositionVariant.SWAPPED:
        display_a, display_b = candidate_display, baseline_display
    else:
        display_a, display_b = baseline_display, candidate_display

    rubric = _rubric_from(baseline_view)
    if not rubric:
        # Candidate view carries the same case rubric; fall through only if
        # the baseline genuinely lacks it (e.g. legacy trial). Both views
        # come from the same case, so prefer whichever has the rubric.
        rubric = _rubric_from(candidate_view)

    # Sort by canonical hash so the serialized form does not depend on which
    # side is A vs B. Identical inputs on either position produce the same
    # input_hash, satisfying invariant #5.
    outputs_sorted = sorted(
        [baseline_display, candidate_display],
        key=canonical_sha256,
    )
    input_hash = canonical_sha256({
        "case_id": pair.case_id,
        "outputs_sorted": outputs_sorted,
        "rubric": rubric,
        "judge_prompt_version": "v1",
        "judge_rubric_version": "v1",
    })

    return PairwiseJudgeInput(
        pair=pair,
        judge_run_id=judge_run_id,
        position_variant=position_variant,
        rubric=rubric,
        display_a=display_a,
        display_b=display_b,
        input_hash=input_hash,
    )
