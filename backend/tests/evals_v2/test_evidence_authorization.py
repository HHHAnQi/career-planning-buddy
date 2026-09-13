"""PR-4 evidence authorization tests (pure Python, no PostgreSQL).

These tests pin the per-Grader ``allowed_evidence_kinds`` snapshot from the
spec table, the authorization filter, and the ``EvidenceAccessDenied`` guard.
They run without a database because the authorization layer is pure-python
over constructed ``EvidenceItem`` rows -- exactly what the spec means by
"the grader step itself should be testable without PG".
"""

from uuid import UUID, uuid4

import pytest

from evals.v2.contracts import canonical_sha256
from evals.v2.graders import (
    EvidenceAccessDenied,
    EvidenceItem,
    EvidenceKind,
    allowed_kinds_snapshot,
    authorize,
)

EXPECTED_ALLOWED_KINDS = {
    "system": frozenset({
        EvidenceKind.RUN_METRICS,
        EvidenceKind.EVENT_PROJECTION,
        EvidenceKind.STEP_PROJECTION,
        EvidenceKind.OUTCOME_STATUS,
        EvidenceKind.TRANSCRIPT_HASH,
    }),
    "safety": frozenset({
        EvidenceKind.RISK_SIGNALS,
        EvidenceKind.REDACTED_OUTPUT,
        EvidenceKind.CROSS_USER_SIGNAL,
        EvidenceKind.TOOL_ALLOWLIST,
        EvidenceKind.PLAN_PROJECTION,
        EvidenceKind.EVIDENCE_VISIBLE_REFS,
    }),
    "tool": frozenset({
        EvidenceKind.TOOL_CALL_PROJECTION,
        EvidenceKind.TOOL_SPEC,
        EvidenceKind.TRAJECTORY_POLICY,
        EvidenceKind.EVIDENCE_VISIBLE_REFS,
        EvidenceKind.PROVIDER_CALL_PROJECTION,
    }),
    "behavioral": frozenset({
        EvidenceKind.STEP_PROJECTION,
        EvidenceKind.EVENT_PROJECTION,
        EvidenceKind.EXPECTED_OUTCOME,
        EvidenceKind.REPAIR_SIGNAL,
    }),
    "task": frozenset({
        EvidenceKind.REQUEST_CONSTRAINTS,
        EvidenceKind.PROFILE_PROJECTION,
        EvidenceKind.EXPECTED_OUTCOME,
        EvidenceKind.PLAN_PROJECTION,
        EvidenceKind.TASK_PROJECTION,
        EvidenceKind.OUTCOME_STATUS,
    }),
    "model": frozenset({
        EvidenceKind.TOOL_CALL_PROJECTION,
        EvidenceKind.EVIDENCE_VISIBLE_REFS,
        EvidenceKind.PLAN_PROJECTION,
        EvidenceKind.RUN_METRICS,
        EvidenceKind.REPAIR_SIGNAL,
        EvidenceKind.PROVIDER_CALL_PROJECTION,
        EvidenceKind.EXPECTED_CITATIONS_MAP,
        # memory_grounded quality grader reads task text surfaces.
        EvidenceKind.TASK_PROJECTION,
    }),
}


def test_each_grader_allowed_kinds_frozen() -> None:
    snapshot = allowed_kinds_snapshot()
    # All six domains are present.
    assert set(snapshot) == set(EXPECTED_ALLOWED_KINDS)
    for grader_name, kinds in snapshot.items():
        assert kinds == EXPECTED_ALLOWED_KINDS[grader_name], (
            f"grader {grader_name!r} allowed_kinds drifted from the spec table"
        )


def test_each_grader_sees_only_its_kinds() -> None:
    """An AuthorizedView contains zero items outside ``allowed_kinds``.

    Construct a catalog with one item per EvidenceKind and confirm each
    AuthorizedView only exposes that Grader's allowed kinds.
    """

    trial_id = uuid4()
    items = [
        _item_for(trial_id, kind) for kind in EvidenceKind
    ]
    snapshot = allowed_kinds_snapshot()
    for grader_name, allowed in snapshot.items():
        view = authorize(
            trial_id=trial_id, items=items, allowed_kinds=allowed
        )
        # All non-allowed kinds are absent from the view.
        exposed_kinds = set(view.items_by_kind)
        assert exposed_kinds == set(allowed), (
            f"grader {grader_name!r} should expose only {sorted(k.value for k in allowed)}, "
            f"got {sorted(k.value for k in exposed_kinds)}"
        )


def test_unauthorized_kind_access_raises() -> None:
    """A view that tries to read a kind outside its allowlist raises."""

    trial_id = uuid4()
    items = [_item_for(trial_id, EvidenceKind.TASK_PROJECTION)]
    view = authorize(
        trial_id=trial_id,
        items=items,
        allowed_kinds=frozenset({EvidenceKind.RUN_METRICS}),
    )
    with pytest.raises(EvidenceAccessDenied) as exc_info:
        view.items(EvidenceKind.TASK_PROJECTION)
    assert exc_info.value.grader_name == "<unknown>"
    assert exc_info.value.requested is EvidenceKind.TASK_PROJECTION


def test_graders_cannot_see_other_scores() -> None:
    """No EvidenceKind carries another Grader's score.

    The EvidenceKind enum is closed; confirm there is no ``*score*`` member.
    A future Grader trying to consume another Grader's output would have to
    add a kind -- and the snapshot test would catch the addition.
    """

    score_like = [k for k in EvidenceKind if "score" in k.value.lower()]
    assert score_like == [], (
        f"EvidenceKind must never carry score projections: {score_like}"
    )


def test_authorize_filters_items_by_kind() -> None:
    """Four input kinds + authorize(X,Y) yields only items of kinds X and Y."""

    trial_id = uuid4()
    items = [
        _item_for(trial_id, EvidenceKind.PLAN_PROJECTION),
        _item_for(trial_id, EvidenceKind.TASK_PROJECTION),
        _item_for(trial_id, EvidenceKind.STEP_PROJECTION),
        _item_for(trial_id, EvidenceKind.EVENT_PROJECTION),
    ]
    view = authorize(
        trial_id=trial_id,
        items=items,
        allowed_kinds=frozenset({EvidenceKind.PLAN_PROJECTION, EvidenceKind.STEP_PROJECTION}),
    )
    assert len(view.items(EvidenceKind.PLAN_PROJECTION)) == 1
    assert len(view.items(EvidenceKind.STEP_PROJECTION)) == 1
    # Asking for an unauthorized kind raises rather than silently returning [].
    with pytest.raises(EvidenceAccessDenied):
        view.items(EvidenceKind.TASK_PROJECTION)
    # An allowed kind that happens to have zero items returns ``[]`` (not None).
    # Build a separate view whose allowed set is wider than the items supplied.
    view_empty_kind = authorize(
        trial_id=trial_id,
        # Only PLAN_PROJECTION is supplied, but STEP_PROJECTION is allowed.
        items=[_item_for(trial_id, EvidenceKind.PLAN_PROJECTION)],
        allowed_kinds=frozenset({
            EvidenceKind.PLAN_PROJECTION, EvidenceKind.STEP_PROJECTION
        }),
    )
    assert view_empty_kind.items(EvidenceKind.STEP_PROJECTION) == []


def test_evidence_item_content_hash_stable_for_same_projection() -> None:
    """Two EvidenceItems with identical projection content hash equally.

    This is the invariant the DB UNIQUE + content_hash enforcement leans on
    for "old scores non-silently reusable when content changes".
    """

    trial_id = uuid4()
    same_projection = {"a": 1, "b": [1, 2, 3], "c": {"d": "e"}}
    item_a = _item_for(trial_id, EvidenceKind.PLAN_PROJECTION, projection=same_projection)
    item_b = _item_for(trial_id, EvidenceKind.PLAN_PROJECTION, projection=same_projection)
    assert item_a.content_hash == item_b.content_hash
    # But change one field and hashes must diverge.
    item_c = _item_for(trial_id, EvidenceKind.PLAN_PROJECTION,
                       projection={**same_projection, "a": 2})
    assert item_a.content_hash != item_c.content_hash


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
    )
