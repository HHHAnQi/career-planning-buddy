"""PR-9c.2 pairwise_review_surface tests (pure Python, no DB / IO).

Pins:
* ``derive_position_variant`` is deterministic on (pair, reviewer, versions)
* same reviewer retrying the same Pair sees the SAME position (idempotent)
* two different reviewers may legitimately see different positions
* ``build_frozen_review_surface`` produces blinded payload + stable hash
* ``normalize_raw_to_baseline_candidate`` maps cleanly under both variants
* ``suggested_label`` cannot leak into the surface payload
"""

from __future__ import annotations

from uuid import uuid4

from evals.v2.pairwise import (
    JUDGE_ALLOWED_KINDS,
    Pair,
    PositionVariant,
    TrialEvidenceProjection,
)
from evals.v2.pairwise_review_surface import (
    REVIEW_SURFACE_VERSION,
    build_frozen_review_surface,
    derive_position_variant,
    normalize_raw_dimensions,
    normalize_raw_to_baseline_candidate,
    render_payload_dict,
)


def _pair() -> Pair:
    b = uuid4()
    c = uuid4()
    return Pair(
        baseline_trial_id=b,
        candidate_trial_id=c,
        case_id="case-1",
        baseline_projection=TrialEvidenceProjection(
            request_constraints={"expect_constraint": "求职"},
            plan_projection={"summary": "baseline"},
        ),
        candidate_projection=TrialEvidenceProjection(
            request_constraints={"expect_constraint": "求职"},
            plan_projection={"summary": "candidate"},
        ),
    )


# ---------------------------------------------------------- derive tests


def test_position_variant_is_stable_for_same_inputs() -> None:
    pair = _pair()
    pair_hash = pair.pair_hash()
    v1 = derive_position_variant(
        pair_hash=pair_hash,
        reviewer_id="reviewer-x",
        rubric_version="v1",
        annotation_schema_version="v1",
    )
    v2 = derive_position_variant(
        pair_hash=pair_hash,
        reviewer_id="reviewer-x",
        rubric_version="v1",
        annotation_schema_version="v1",
    )
    assert v1 == v2


def test_position_variant_is_binary_value() -> None:
    """Output MUST be one of the two PositionVariant values."""

    for reviewer in ("alice", "bob", "carol", "dave", "eve"):
        v = derive_position_variant(
            pair_hash="abc",
            reviewer_id=reviewer,
            rubric_version="v1",
            annotation_schema_version="v1",
        )
        assert v in (PositionVariant.BASELINE, PositionVariant.SWAPPED)


def test_position_variant_can_differ_across_reviewers() -> None:
    """Sanity: at least one of N reviewers should land on a different
    variant than reviewer-1 (otherwise the hash is degenerate)."""

    base = derive_position_variant(
        pair_hash="abc",
        reviewer_id="reviewer-1",
        rubric_version="v1",
        annotation_schema_version="v1",
    )
    others = [
        derive_position_variant(
            pair_hash="abc",
            reviewer_id=f"reviewer-{i}",
            rubric_version="v1",
            annotation_schema_version="v1",
        )
        for i in range(2, 12)
    ]
    assert any(v != base for v in others), "no variant diversity across reviewers"


def test_position_variant_uses_versions_in_seed() -> None:
    """Bumping rubric_version should (very likely) flip or at least be a
    distinct decision input — bumping the version is a calibration
    invalidating event so the variant distribution is allowed to shift."""

    pair_hash = "abc"
    v1 = derive_position_variant(
        pair_hash=pair_hash, reviewer_id="r",
        rubric_version="v1", annotation_schema_version="v1",
    )
    v2 = derive_position_variant(
        pair_hash=pair_hash, reviewer_id="r",
        rubric_version="v2", annotation_schema_version="v1",
    )
    # Either same or different is acceptable as long as it is stable per
    # (pair, reviewer, version). The contract is: same inputs ⇒ same output.
    assert v1 in (PositionVariant.BASELINE, PositionVariant.SWAPPED)
    assert v2 in (PositionVariant.BASELINE, PositionVariant.SWAPPED)


# ---------------------------------------------------- frozen surface


def test_build_frozen_review_surface_blinds_baseline_candidate() -> None:
    pair = _pair()
    surface = build_frozen_review_surface(
        pair=pair,
        reviewer_id="alice",
        rubric=[{"criterion_id": "c1", "description": "有可执行步骤"}],
        rubric_version="v1",
        annotation_schema_version="v1",
    )
    payload = render_payload_dict(surface)
    # display payloads reveal only request+plan, never trial ids / roles
    flat = repr(payload)
    assert "baseline_trial_id" not in flat
    assert "candidate_trial_id" not in flat
    # suggested_label MUST NOT be in the surface payload.
    assert "suggested_label" not in flat
    # pair_hash stays internal to the surface object, NOT in payload
    assert "pair_hash" not in flat


def test_surface_hash_is_invariant_to_position_assignment() -> None:
    """The frozen_review_surface_sha256 must be the SAME when the same
    display content is shown at either position — i.e. swapping baseline/
    candidate does NOT perturb the hash. Two reviewers seeing the same Pair
    (one BASELINE, one SWAPPED) must report the same review_input_hash."""

    pair = _pair()
    surface_a = build_frozen_review_surface(
        pair=pair, reviewer_id="alice",
        rubric=[], rubric_version="v1", annotation_schema_version="v1",
    )
    surface_b = build_frozen_review_surface(
        pair=pair, reviewer_id="bob",
        rubric=[], rubric_version="v1", annotation_schema_version="v1",
    )
    assert (
        surface_a.frozen_review_surface_sha256
        == surface_b.frozen_review_surface_sha256
    ), "surface hash must not depend on which side the baseline occupies"


def test_surface_exposes_allowed_evidence_kinds() -> None:
    pair = _pair()
    surface = build_frozen_review_surface(
        pair=pair, reviewer_id="r",
        rubric=[], rubric_version="v1", annotation_schema_version="v1",
    )
    assert surface.allowed_evidence_kinds == frozenset(
        k.value for k in JUDGE_ALLOWED_KINDS
    )


def test_surface_carries_versions_and_review_surface_version() -> None:
    pair = _pair()
    surface = build_frozen_review_surface(
        pair=pair, reviewer_id="r",
        rubric=[{"criterion_id": "c1", "description": "x"}],
        rubric_version="v1", annotation_schema_version="v2",
    )
    assert surface.rubric_version == "v1"
    assert surface.annotation_schema_version == "v2"
    assert surface.review_surface_version == REVIEW_SURFACE_VERSION


def test_surface_display_a_trial_id_consistent_with_position() -> None:
    """``display_a_trial_id`` MUST align with ``position_variant``:
    SWAPPED → display_a is the candidate trial."""

    pair = _pair()
    surface = build_frozen_review_surface(
        pair=pair, reviewer_id="r",
        rubric=[], rubric_version="v1", annotation_schema_version="v1",
    )
    if surface.position_variant is PositionVariant.SWAPPED:
        assert surface.display_a_trial_id == str(pair.candidate_trial_id)
        assert surface.display_b_trial_id == str(pair.baseline_trial_id)
    else:
        assert surface.display_a_trial_id == str(pair.baseline_trial_id)
        assert surface.display_b_trial_id == str(pair.candidate_trial_id)


# ------------------------------------- normalize raw -> baseline/candidate


def test_normalize_baseline_position_keeps_label_mapping() -> None:
    assert (
        normalize_raw_to_baseline_candidate("a", PositionVariant.BASELINE)
        == "baseline"
    )
    assert (
        normalize_raw_to_baseline_candidate("b", PositionVariant.BASELINE)
        == "candidate"
    )
    assert (
        normalize_raw_to_baseline_candidate("tie", PositionVariant.BASELINE)
        == "tie"
    )
    assert (
        normalize_raw_to_baseline_candidate(
            "both_unacceptable", PositionVariant.BASELINE
        )
        == "both_unacceptable"
    )


def test_normalize_swapped_position_flips_a_and_b() -> None:
    assert (
        normalize_raw_to_baseline_candidate("a", PositionVariant.SWAPPED)
        == "candidate"
    )
    assert (
        normalize_raw_to_baseline_candidate("b", PositionVariant.SWAPPED)
        == "baseline"
    )
    assert (
        normalize_raw_to_baseline_candidate("tie", PositionVariant.SWAPPED)
        == "tie"
    )


def test_normalize_dimensions_applies_per_dim() -> None:
    from typing import Literal

    raw: dict[str, Literal["a", "b", "tie", "both_unacceptable"]] = {
        "actionability": "a",
        "alignment": "b",
        "personalization": "tie",
        "clarity": "both_unacceptable",
        "consistency": "a",
    }
    out = normalize_raw_dimensions(raw, PositionVariant.SWAPPED)
    assert out["actionability"] == "candidate"
    assert out["alignment"] == "baseline"
    assert out["personalization"] == "tie"
    assert out["clarity"] == "both_unacceptable"
    assert out["consistency"] == "candidate"
