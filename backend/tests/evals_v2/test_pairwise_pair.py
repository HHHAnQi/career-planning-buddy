"""PR-9c.1 Pair + JudgeInput construction tests (pure Python, no PG).

Pins:
* the frozen ``JUDGE_ALLOWED_KINDS`` set
* projection extraction respects authorization (no leak)
* ``pair_hash`` is role-determined (ordering-stable, not sort-stable)
* ``input_hash`` is swap-invariant (same Pair, either position → same hash)
* display slot assignment follows ``position_variant`` exactly
* rubric is forwarded only from authorized evidence
"""

from __future__ import annotations

from uuid import UUID, uuid4

from evals.v2.contracts import canonical_sha256
from evals.v2.graders.base import (
    AuthorizedView,
    EvidenceItem,
    EvidenceKind,
    authorize,
)
from evals.v2.pairwise import (
    JUDGE_ALLOWED_KINDS,
    Pair,
    PairwiseJudgeInput,
    PositionVariant,
    TrialEvidenceProjection,
    build_judge_input,
    build_pair,
)

EXPECTED_JUDGE_ALLOWED_KINDS = frozenset({
    EvidenceKind.REQUEST_CONSTRAINTS,
    EvidenceKind.PLAN_PROJECTION,
    EvidenceKind.RUBRIC,
})


# ---------------------------------------------------------------- helpers


def _item_for(
    trial_id: UUID,
    kind: EvidenceKind,
    *,
    projection: dict[str, object] | None = None,
) -> EvidenceItem:
    payload: dict[str, object] = (
        projection if projection is not None else {"_kind": kind.value}
    )
    return EvidenceItem(
        id=uuid4(),
        trial_id=trial_id,
        kind=kind,
        source_type="test",
        source_id=f"test:{kind.value}",
        content_hash=canonical_sha256(payload),
        projection=payload,
        sensitivity="normal",
    )


def _view(trial_id: UUID, items: list[EvidenceItem]) -> AuthorizedView:
    return authorize(
        trial_id=trial_id, items=items, allowed_kinds=JUDGE_ALLOWED_KINDS
    )


def _baseline_items(trial_id: UUID) -> list[EvidenceItem]:
    return [
        _item_for(
            trial_id,
            EvidenceKind.REQUEST_CONSTRAINTS,
            projection={"expect_constraint": "目标：3周内求职"},
        ),
        _item_for(
            trial_id,
            EvidenceKind.PLAN_PROJECTION,
            projection={"summary": "baseline plan", "tasks": []},
        ),
        _item_for(
            trial_id,
            EvidenceKind.RUBRIC,
            projection={"criteria": [{"criterion_id": "c1", "description": "有可执行步骤"}]},
        ),
    ]


def _candidate_items(trial_id: UUID) -> list[EvidenceItem]:
    return [
        _item_for(
            trial_id,
            EvidenceKind.REQUEST_CONSTRAINTS,
            projection={"expect_constraint": "目标：3周内求职"},
        ),
        _item_for(
            trial_id,
            EvidenceKind.PLAN_PROJECTION,
            projection={"summary": "candidate plan", "tasks": []},
        ),
        _item_for(
            trial_id,
            EvidenceKind.RUBRIC,
            projection={"criteria": [{"criterion_id": "c1", "description": "有可执行步骤"}]},
        ),
    ]


# ---------------------------------------------------------------- tests


def test_judge_allowed_kinds_frozen() -> None:
    assert JUDGE_ALLOWED_KINDS == EXPECTED_JUDGE_ALLOWED_KINDS


def test_projection_extracts_only_authorized_kinds() -> None:
    """An out-of-scope kind present in the view is not extracted."""

    trial_id = uuid4()
    items = _baseline_items(trial_id) + [
        _item_for(trial_id, EvidenceKind.TRANSCRIPT_HASH, projection={"secret": 1}),
        _item_for(trial_id, EvidenceKind.RUN_METRICS, projection={"tokens": 999}),
    ]
    view = _view(trial_id, items)
    pair = build_pair(
        baseline_trial_id=trial_id,
        candidate_trial_id=uuid4(),
        case_id="case-1",
        baseline_view=view,
        candidate_view=_view(uuid4(), _candidate_items(uuid4())),
    )
    display = pair.baseline_projection.as_display()
    assert "secret" not in display
    assert "tokens" not in display
    # And the two authorized payloads are present.
    assert display["request"] == {"expect_constraint": "目标：3周内求职"}
    assert display["plan"] == {"summary": "baseline plan", "tasks": []}


def test_projection_handles_missing_evidence_gracefully() -> None:
    projection = TrialEvidenceProjection(
        request_constraints=None,
        plan_projection={"summary": "only plan"},
    )
    assert projection.as_display() == {"plan": {"summary": "only plan"}}


def test_pair_hash_is_role_determined_not_sort_determined() -> None:
    """``pair_hash`` keys on the ordered (baseline, candidate) tuple,
    so swapping roles yields a different hash even though the trials are
    the same set."""

    b = uuid4()
    c = uuid4()
    pair_a = Pair(
        baseline_trial_id=b,
        candidate_trial_id=c,
        case_id="case-1",
        baseline_projection=TrialEvidenceProjection(None, None),
        candidate_projection=TrialEvidenceProjection(None, None),
    )
    pair_b = Pair(
        baseline_trial_id=c,
        candidate_trial_id=b,
        case_id="case-1",
        baseline_projection=TrialEvidenceProjection(None, None),
        candidate_projection=TrialEvidenceProjection(None, None),
    )
    assert pair_a.pair_hash() == pair_a.pair_hash()  # deterministic
    assert pair_a.pair_hash() != pair_b.pair_hash()  # role-sensitive
    # For comparison: a hash keyed on the sorted tuple would be equal.
    sorted_keyed = canonical_sha256({
        "trials": sorted([str(b), str(c)]),
    })
    assert pair_a.pair_hash() != sorted_keyed


def test_pair_hash_stable_for_same_role_inputs() -> None:
    b = uuid4()
    c = uuid4()
    projection = TrialEvidenceProjection(
        request_constraints={"expect_constraint": "x"},
        plan_projection={"summary": "y"},
    )
    pair_a = Pair(b, c, "case-1", projection, projection)
    pair_b = Pair(b, c, "case-1", projection, projection)
    assert pair_a.pair_hash() == pair_b.pair_hash()


def test_input_hash_is_swap_invariant() -> None:
    """The same Pair run at BASELINE and at SWAPPED yields the same
    ``input_hash`` (invariant #5)."""

    baseline_id = uuid4()
    candidate_id = uuid4()
    baseline_view = _view(baseline_id, _baseline_items(baseline_id))
    candidate_view = _view(candidate_id, _candidate_items(candidate_id))
    pair = build_pair(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id="case-1",
        baseline_view=baseline_view,
        candidate_view=candidate_view,
    )
    run_a = uuid4()
    run_b = uuid4()
    input_regular = build_judge_input(
        pair=pair,
        judge_run_id=run_a,
        baseline_view=baseline_view,
        candidate_view=candidate_view,
        position_variant=PositionVariant.BASELINE,
    )
    input_swapped = build_judge_input(
        pair=pair,
        judge_run_id=run_b,
        baseline_view=baseline_view,
        candidate_view=candidate_view,
        position_variant=PositionVariant.SWAPPED,
    )
    assert input_regular.input_hash == input_swapped.input_hash
    assert input_regular.judge_run_id != input_swapped.judge_run_id


def test_display_slots_follow_position_variant() -> None:
    """Under BASELINE the baseline projection lands in display_a; under
    SWAPPED it lands in display_b."""

    baseline_id = uuid4()
    candidate_id = uuid4()
    baseline_view = _view(baseline_id, _baseline_items(baseline_id))
    candidate_view = _view(candidate_id, _candidate_items(candidate_id))
    pair = build_pair(
        baseline_trial_id=baseline_id,
        candidate_trial_id=candidate_id,
        case_id="case-1",
        baseline_view=baseline_view,
        candidate_view=candidate_view,
    )

    regular = build_judge_input(
        pair=pair,
        judge_run_id=uuid4(),
        baseline_view=baseline_view,
        candidate_view=candidate_view,
        position_variant=PositionVariant.BASELINE,
    )
    assert regular.display_a["plan"] == {"summary": "baseline plan", "tasks": []}
    assert regular.display_b["plan"] == {"summary": "candidate plan", "tasks": []}

    swapped = build_judge_input(
        pair=pair,
        judge_run_id=uuid4(),
        baseline_view=baseline_view,
        candidate_view=candidate_view,
        position_variant=PositionVariant.SWAPPED,
    )
    assert swapped.display_a["plan"] == {"summary": "candidate plan", "tasks": []}
    assert swapped.display_b["plan"] == {"summary": "baseline plan", "tasks": []}


def test_input_hash_changes_when_plan_changes() -> None:
    """``input_hash`` must differ when the underlying plan content differs,
    not just when the trial id differs."""

    b = uuid4()
    c = uuid4()
    baseline_view = _view(b, _baseline_items(b))
    candidate_view = _view(c, _candidate_items(c))
    pair_regular = build_pair(
        baseline_trial_id=b,
        candidate_trial_id=c,
        case_id="case-1",
        baseline_view=baseline_view,
        candidate_view=candidate_view,
    )
    # Candidate with a different summary.
    alt_candidate_items = [_item_for(c, EvidenceKind.REQUEST_CONSTRAINTS,
                                     projection={"expect_constraint": "目标：3周内求职"})]
    pair_changed = build_pair(
        baseline_trial_id=b,
        candidate_trial_id=c,
        case_id="case-1",
        baseline_view=baseline_view,
        candidate_view=_view(c, alt_candidate_items),
    )

    hash_regular = build_judge_input(
        pair=pair_regular, judge_run_id=uuid4(),
        baseline_view=baseline_view, candidate_view=candidate_view,
        position_variant=PositionVariant.BASELINE,
    ).input_hash
    hash_changed = build_judge_input(
        pair=pair_changed, judge_run_id=uuid4(),
        baseline_view=baseline_view, candidate_view=candidate_view,
        position_variant=PositionVariant.BASELINE,
    ).input_hash
    assert hash_regular != hash_changed


def test_input_carries_versions_and_allowed_kinds() -> None:
    b = uuid4()
    c = uuid4()
    baseline_view = _view(b, _baseline_items(b))
    candidate_view = _view(c, _candidate_items(c))
    pair = build_pair(
        baseline_trial_id=b, candidate_trial_id=c,
        case_id="case-1",
        baseline_view=baseline_view, candidate_view=candidate_view,
    )
    judge_input = build_judge_input(
        pair=pair, judge_run_id=uuid4(),
        baseline_view=baseline_view, candidate_view=candidate_view,
        position_variant=PositionVariant.BASELINE,
    )
    assert isinstance(judge_input, PairwiseJudgeInput)
    assert judge_input.judge_prompt_version == "v1"
    assert judge_input.judge_rubric_version == "v1"
    assert judge_input.allowed_evidence_kinds == frozenset(
        kind.value for kind in JUDGE_ALLOWED_KINDS
    )
    assert judge_input.rubric == [
        {"criterion_id": "c1", "description": "有可执行步骤"}
    ]


def test_rubric_extracted_only_from_authorized_evidence() -> None:
    """When neither baseline nor candidate view carries RUBRIC, the
    Judge operates against an empty rubric (does not synthesize one)."""

    b = uuid4()
    c = uuid4()
    baseline_items = [
        _item_for(b, EvidenceKind.REQUEST_CONSTRAINTS, projection={"x": 1}),
        _item_for(b, EvidenceKind.PLAN_PROJECTION, projection={"summary": "b"}),
    ]
    candidate_items = [
        _item_for(c, EvidenceKind.REQUEST_CONSTRAINTS, projection={"x": 1}),
        _item_for(c, EvidenceKind.PLAN_PROJECTION, projection={"summary": "c"}),
    ]
    baseline_view = _view(b, baseline_items)
    candidate_view = _view(c, candidate_items)
    pair = build_pair(
        baseline_trial_id=b, candidate_trial_id=c,
        case_id="case-x",
        baseline_view=baseline_view, candidate_view=candidate_view,
    )
    judge_input = build_judge_input(
        pair=pair, judge_run_id=uuid4(),
        baseline_view=baseline_view, candidate_view=candidate_view,
        position_variant=PositionVariant.BASELINE,
    )
    assert judge_input.rubric == []


def test_pair_hash_excludes_comparison_group() -> None:
    """``pair_hash`` MUST NOT depend on the comparison_group_id, the Judge
    prompt/model, or any other per-execution attribute. The Pair row is
    the stable business Pair entity; comparison_group_id lives on
    ``EvalPairwiseJudgeResult`` rows.

    Concretely: two re-evaluations of the same trial pair with the same
    output bytes (one each at G1 and G2) MUST produce equal pair_hashes.
    Otherwise ``eval_trial_pairs`` would duplicate the Pair row, breaking
    ``UNIQUE(pair_hash)`` and PR-9c.2 calibration binding.
    """

    b = uuid4()
    c = uuid4()
    projection = TrialEvidenceProjection(
        request_constraints={"expect_constraint": "求职"},
        plan_projection={"summary": "stable"},
    )
    pair_g1 = Pair(b, c, "case-1", projection, projection)
    pair_g2 = Pair(b, c, "case-1", projection, projection)
    assert pair_g1.pair_hash() == pair_g2.pair_hash()


def test_pair_hash_changes_when_output_bytes_change() -> None:
    """``pair_hash`` IS sensitive to actual output content. A re-collect
    that moves the plan bytes must produce a different pair_hash so that
    ``eval_trial_pairs`` can carry both old + new outputs as separate
    Pair rows attributable to their respective bytes."""

    b = uuid4()
    c = uuid4()
    base_proj = TrialEvidenceProjection(None, {"summary": "v1"})
    pair_v1 = Pair(b, c, "case-1", base_proj, base_proj)
    base_proj_v2 = TrialEvidenceProjection(None, {"summary": "v2"})
    pair_v2 = Pair(b, c, "case-1", base_proj_v2, base_proj_v2)
    assert pair_v1.pair_hash() != pair_v2.pair_hash()


def test_pair_hash_is_not_influenced_by_unrelated_view_kinds() -> None:
    """Even if the caller builds the view with a wider allowed set, the
    Pair hash depends only on trial ids + group, not projection content."""

    b = uuid4()
    c = uuid4()
    wide_kinds = frozenset({EvidenceKind.PLAN_PROJECTION, EvidenceKind.RUN_METRICS})
    wide_view_b = authorize(
        trial_id=b,
        items=[_item_for(b, EvidenceKind.PLAN_PROJECTION, projection={"x": 1}),
               _item_for(b, EvidenceKind.RUN_METRICS, projection={"leak": True})],
        allowed_kinds=wide_kinds,
    )
    wide_view_c = authorize(
        trial_id=c,
        items=[_item_for(c, EvidenceKind.PLAN_PROJECTION, projection={"x": 2})],
        allowed_kinds=wide_kinds,
    )
    pair = build_pair(
        baseline_trial_id=b, candidate_trial_id=c,
        case_id="case-1",
        baseline_view=wide_view_b, candidate_view=wide_view_c,
    )
    # Projection will contain only PLAN_PROJECTION (RUN_METRICS not extracted).
    assert "leak" not in pair.baseline_projection.as_display()
