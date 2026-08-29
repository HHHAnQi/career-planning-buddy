"""PR-9c.2 server-authoritative blinded Review Surface builder.

The human-reviewer API MUST NOT accept ``reviewer_id``, ``position_variant``,
normalized verdicts, or baseline/candidate mapping from the request body
(per the user's PR-9c.2 plan v2 + supplementary constraints #5). Those fields
are reconstructed by the service from:

* ``reviewer_id`` — JWT subject only (router-injected, never trusted from
  the body);
* ``position_variant`` — derived deterministically from
  ``(pair_hash, reviewer_id, rubric_version, annotation_schema_version)``
  via ``derive_position_variant`` below. The same reviewer judging the
  same Pair always sees the SAME position variant (so a re-submit hits the
  idempotent path); different reviewers may see different variants, which
  reduces shared-position bias;
* ``frozen_review_surface`` — a deterministic, server-rendered payload
  (same fields the Judge saw under :mod:`evals.v2.pairwise`), hashed as
  ``frozen_review_surface_sha256`` for provenance.

Architecture invariant: this module never imports ``app.agent.*`` /
``app.harness.*``. It depends only on the Eval Harness layer and stdlib.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from evals.v2.contracts import canonical_sha256
from evals.v2.pairwise import (
    JUDGE_ALLOWED_KINDS,
    Pair,
    PositionVariant,
    TrialEvidenceProjection,
)

REVIEW_SURFACE_VERSION = "v1"


class EvalTrialPairLike(Protocol):
    """Structural type for what
    :func:`build_frozen_review_surface_for_pair_row` needs from a Pair
    row. Implementations include the SQLAlchemy ``EvalTrialPair`` and
    the storage layer's frozen Pair DTOs."""

    pair_hash: str
    baseline_trial_id: UUID
    candidate_trial_id: UUID
    case_id: str

# The review surface shares the same minimal allowed-evidence set as the
# Judge (PR-9c.1 ``JUDGE_ALLOWED_KINDS``). If either expands, the surface
# version MUST bump — old annotations remain attributable to their own
# surface version.
SurfaceAllowedKinds = frozenset[str]


@dataclass(frozen=True, slots=True)
class FrozenReviewSurface:
    """One server-rendered, blinded review surface for one Pair+Reviewer.

    ``display_a`` / ``display_b`` carry ONLY the ``request`` and ``plan``
    payloads — never baseline/candidate/trial ids. ``position_variant``
    records which slot the baseline occupies; ``display_a_trial_id`` /
    ``display_b_trial_id`` are NON-secret bookkeeping for storing on the
    annotation row, but they are NOT sent back to the reviewer.

    ``frozen_review_surface_sha256`` is the canonical hash of the surface
    payload EXCLUDING any baseline/candidate trial ids (the reviewer never
    sees them, so they must not perturb the provenance hash). Re-rendering
    the same inputs always yields the same hash.
    """

    pair_hash: str
    case_id: str
    reviewer_id: str
    position_variant: PositionVariant
    rubric_version: str
    annotation_schema_version: str
    review_surface_version: str
    display_a: dict[str, object]
    display_b: dict[str, object]
    display_a_trial_id: str
    display_b_trial_id: str
    rubric: list[dict[str, object]]
    frozen_review_surface_sha256: str
    allowed_evidence_kinds: SurfaceAllowedKinds


def derive_position_variant(
    *,
    pair_hash: str,
    reviewer_id: str,
    rubric_version: str,
    annotation_schema_version: str,
) -> PositionVariant:
    """Deterministically map ``(pair, reviewer, versions)`` to a Position.

    The function is total and stable: same inputs always produce the same
    variant. ``sha256(...)[:8]`` bits give a uniform 50/50 split across
    reviewer-pair combinations when the input set is large, but per-pair
    determinism is what matters for idempotent annotation submission (same
    reviewer retrying the same Pair always sees the SAME variant).
    """

    seed_src = (
        f"review-surface/{REVIEW_SURFACE_VERSION}/"
        f"{pair_hash}/{reviewer_id}/{rubric_version}/{annotation_schema_version}"
    )
    digest = sha256(seed_src.encode("utf-8")).digest()
    return PositionVariant.SWAPPED if (digest[0] & 0x01) == 0x01 else PositionVariant.BASELINE


def build_frozen_review_surface(
    *,
    pair: Pair,
    reviewer_id: str,
    rubric: list[dict[str, object]],
    rubric_version: str,
    annotation_schema_version: str,
) -> FrozenReviewSurface:
    """Render the server-authoritative blinded review surface.

    Uses ``pair.baseline_projection`` / ``pair.candidate_projection`` and
    ``derive_position_variant`` to choose display slots. The output hash
    is independent of which side the baseline occupies: a swapped surface
    is the SAME surface bytes with display_a/display_b exchanged, so a
    reviewer re-judging the same Pair always hits the same surface hash
    (modulo rubric/version bumps). That makes the annotation UNIQUE key
    ``(pair_id, reviewer_id, review_input_hash)`` stable across re-submits
    even when the display position shifts (it never shifts deterministically
    for the same reviewer, but the invariant still holds accidentally).
    """

    position_variant = derive_position_variant(
        pair_hash=pair.pair_hash(),
        reviewer_id=reviewer_id,
        rubric_version=rubric_version,
        annotation_schema_version=annotation_schema_version,
    )

    baseline_display = pair.baseline_projection.as_display()
    candidate_display = pair.candidate_projection.as_display()
    if position_variant is PositionVariant.SWAPPED:
        display_a, display_b = candidate_display, baseline_display
        display_a_trial_id = str(pair.candidate_trial_id)
        display_b_trial_id = str(pair.baseline_trial_id)
    else:
        display_a, display_b = baseline_display, candidate_display
        display_a_trial_id = str(pair.baseline_trial_id)
        display_b_trial_id = str(pair.candidate_trial_id)

    frozen_review_surface_sha256 = _hash_surface(
        pair_hash=pair.pair_hash(),
        case_id=pair.case_id,
        rubric=rubric,
        rubric_version=rubric_version,
        annotation_schema_version=annotation_schema_version,
        review_surface_version=REVIEW_SURFACE_VERSION,
        display_a=display_a,
        display_b=display_b,
    )

    return FrozenReviewSurface(
        pair_hash=pair.pair_hash(),
        case_id=pair.case_id,
        reviewer_id=reviewer_id,
        position_variant=position_variant,
        rubric_version=rubric_version,
        annotation_schema_version=annotation_schema_version,
        review_surface_version=REVIEW_SURFACE_VERSION,
        display_a=display_a,
        display_b=display_b,
        display_a_trial_id=display_a_trial_id,
        display_b_trial_id=display_b_trial_id,
        rubric=list(rubric),
        frozen_review_surface_sha256=frozen_review_surface_sha256,
        allowed_evidence_kinds=frozenset(
            kind.value for kind in JUDGE_ALLOWED_KINDS
        ),
    )


def _hash_surface(
    *,
    pair_hash: str,
    case_id: str,
    rubric: list[dict[str, object]],
    rubric_version: str,
    annotation_schema_version: str,
    review_surface_version: str,
    display_a: dict[str, object],
    display_b: dict[str, object],
) -> str:
    """Canonical hash of the visible surface ONLY.

    ``display_a`` / ``display_b`` are sorted by their canonical hash so the
    SAME surface (same Pair content) renders the same hash regardless of
    which side is "A". That guarantees a Turkish-reviewer-1 sees a swapped
    version and reviewer-2 sees a non-swapped version, the surface hash
    stays equal — making inter-reviewer reconciliation meaningful.
    """

    outputs_sorted = sorted([display_a, display_b], key=canonical_sha256)
    return canonical_sha256({
        "schema_version": "pairwise-review-surface/v1",
        "review_surface_version": review_surface_version,
        "pair_hash": pair_hash,
        "case_id": case_id,
        "rubric_version": rubric_version,
        "annotation_schema_version": annotation_schema_version,
        "outputs_sorted": outputs_sorted,
        "rubric": rubric,
    })


def render_payload_dict(surface: FrozenReviewSurface) -> dict[str, object]:
    """Serialize a FrozenReviewSurface into a JSON-safe dict.

    Used by HTTP layer to render the surface back to the reviewer. The
    payload EXCLUDES ``display_a_trial_id`` / ``display_b_trial_id`` /
    ``pair_hash``'s underlying trial ids — only what the reviewer needs to
    write a verdict. ``suggested_label`` is also NOT included per
    supplementary constraint #6.
    """

    return {
        "review_surface_version": surface.review_surface_version,
        "position_variant": surface.position_variant.value,
        "case_id": surface.case_id,
        "rubric": surface.rubric,
        "display_a": surface.display_a,
        "display_b": surface.display_b,
        # NOTE: pair_hash NOT exposed — it would let the reviewer correlate
        # repeated surfaces; the API uses pair_id separately via the path.
    }


def serialize_surface_json(surface: FrozenReviewSurface) -> str:
    """Stable JSON form for hashing / archival. Mostly used in tests."""

    return json.dumps(
        render_payload_dict(surface),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def derive_review_token(
    *,
    pair_id: UUID | str,
    reviewer_id: str,
    frozen_review_surface_sha256: str,
) -> str:
    """Non-secret tamper-protection token linking a POST annotation back
    to the GET review-surface the reviewer was shown.

    NOT a cryptographic MAC — there is no server secret in the mix. Its
    purpose is purely to detect that the reviewer is submitting against
    a DIFFERENT surface than the one they fetched (e.g. they switched
    reviewers, the rubric version bumped under them, or they tried to
    submit a stale token from a prior sweep). The server re-derives it
    and rejects on mismatch.

    16 hex chars (8 bytes) is enough collision-resistance for the
    failure mode (an honest reviewer cannot accidentally forge it).
    """

    seed = f"review-token/v1/{pair_id}/{reviewer_id}/{frozen_review_surface_sha256}"
    return sha256(seed.encode("utf-8")).hexdigest()[:16]


# A trivial sanity-check type to keep the literal vocabulary closed in
# callers that import from here. Mirrors evals.v2.judge.BaselineCandidateLabel
# defined in calibration_metrics.py to avoid a circular import.
BaselineCandidateLabel = Literal[
    "baseline", "candidate", "tie", "both_unacceptable"
]


def normalize_raw_to_baseline_candidate(
    raw_label: Literal["a", "b", "tie", "both_unacceptable"],
    position_variant: PositionVariant,
) -> BaselineCandidateLabel:
    """Map a reviewer's raw a/b verdict to baseline/candidate vocabulary.

    The reviewer only ever picks ``a``/``b`` (positions). The map back to
    baseline/candidate depends on which side the baseline occupied:

    * ``PositionVariant.BASELINE`` — display_a was baseline → ``a`` = baseline;
    * ``PositionVariant.SWAPPED``  — display_b was baseline → ``b`` = baseline.

    ``tie`` and ``both_unacceptable`` are position-invariant.
    """

    if raw_label in ("tie", "both_unacceptable"):
        return raw_label
    baseline_position = "a" if position_variant is PositionVariant.BASELINE else "b"
    return "baseline" if raw_label == baseline_position else "candidate"


def normalize_raw_dimensions(
    raw_dims: Mapping[str, Literal["a", "b", "tie", "both_unacceptable"]],
    position_variant: PositionVariant,
) -> dict[str, BaselineCandidateLabel]:
    """Apply ``normalize_raw_to_baseline_candidate`` to each dimension."""

    return {
        name: normalize_raw_to_baseline_candidate(verdict, position_variant)
        for name, verdict in raw_dims.items()
    }


__all__ = [
    "REVIEW_SURFACE_VERSION",
    "BaselineCandidateLabel",
    "FrozenReviewSurface",
    "ReviewSurfaceFrozenInput",
    "SurfaceAllowedKinds",
    "build_frozen_review_surface",
    "build_frozen_review_surface_for_pair_row",
    "derive_position_variant",
    "derive_review_token",
    "normalize_raw_dimensions",
    "normalize_raw_to_baseline_candidate",
    "render_payload_dict",
    "serialize_surface_json",
]


# Suppress an unused-import warning for ``TrialEvidenceProjection``: it is
# re-exported so callers building a test surface can construct Projections
# without reaching into ``evals.v2.pairwise`` separately.
_ = TrialEvidenceProjection


@dataclass(frozen=True, slots=True)
class ReviewSurfaceFrozenInput:
    """Result of building a frozen review surface FROM a Pair row +
    reviewer identity — grouped so the HTTP layer can pass a single
    object around."""

    position_variant: str
    frozen_review_surface_sha256: str
    display_a_trial_id: UUID
    display_b_trial_id: UUID


def build_frozen_review_surface_for_pair_row(
    *,
    pair_row: EvalTrialPairLike,
    reviewer_id: str,
    rubric_version: str,
    annotation_schema_version: str,
    rubric: list[dict[str, object]] | None = None,
) -> ReviewSurfaceFrozenInput:
    """Build a frozen review surface FROM an ``EvalTrialPair`` row.

    ``pair_row`` carries only ``pair_hash``, ``baseline_trial_id``,
    ``candidate_trial_id``, ``case_id`` — the projection payloads are
    NOT on the Pair row. We approximate a minimal surface by reusing
    the Pair's trial ids as the display candidates (the Judge sees the
    same set). The frozen-review-surface SHA is computed over the
    pair_hash + versions + empty projection payloads; this is stable
    per (pair, reviewer, version) and serves as ``review_input_hash``
    on the annotation row.
    """

    from uuid import UUID as _UUID

    pair = Pair(
        baseline_trial_id=_UUID(str(pair_row.baseline_trial_id))
        if not isinstance(pair_row.baseline_trial_id, _UUID)
        else pair_row.baseline_trial_id,
        candidate_trial_id=_UUID(str(pair_row.candidate_trial_id))
        if not isinstance(pair_row.candidate_trial_id, _UUID)
        else pair_row.candidate_trial_id,
        case_id=pair_row.case_id,
        baseline_projection=TrialEvidenceProjection(
            request_constraints=None, plan_projection=None
        ),
        candidate_projection=TrialEvidenceProjection(
            request_constraints=None, plan_projection=None
        ),
    )
    surface = build_frozen_review_surface(
        pair=pair,
        reviewer_id=reviewer_id,
        rubric=rubric or [],
        rubric_version=rubric_version,
        annotation_schema_version=annotation_schema_version,
    )
    return ReviewSurfaceFrozenInput(
        position_variant=surface.position_variant.value,
        frozen_review_surface_sha256=surface.frozen_review_surface_sha256,
        display_a_trial_id=_UUID(surface.display_a_trial_id),
        display_b_trial_id=_UUID(surface.display_b_trial_id),
    )
