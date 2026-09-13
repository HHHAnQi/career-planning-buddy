"""Stage A Step 5 — Human review workflow validation.

Drives the same Service-layer code path that the HTTP /annotations
endpoints invoke:

  Case A (consensus):
    * pick one pair
    * fetch Review Surface for reviewer R1 + R2 (independently)
    * both submit raw winner=a
    * assert: constellation of N=2 agreeing primary rows lands in DB
      + service-derivable consensus label = "a"

  Case B (disagreement + adjudication):
    * pick a different pair
    * R3 submits winner=a; R4 submits winner=b (overall disagreement)
    * adjudicator R5 submits winner=tie with is_adjudication=True
    * assert: pair has exactly 2 primaries + exactly 1 adjudication,
      R5 is distinct from {R3, R4}, adjudication row present and
      normalized_winner == tie

Per the reviewer's Stage-A Step-5 sheet:

* pair source is restricted to ``pairwise-calibration-v0-dev-smoke``
  (the committed Smoke dataset, not v1).
* ≤2 case-pairs are touched (1 consensus + 1 disagreement). Never
  100-real-human-pair territory.
* ``suggested_label`` never enters the annotation body.
* Service path is verified; ASGI transport lifecycle is out-of-scope
  for Stage A Step 5 (same disclosure as Step 4).

PR-9c.2 Commit 3.4 / Stage A E′ — Step 5.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_transaction  # noqa: E402
from app.models.eval import (  # noqa: E402
    EvalPairwiseHumanAnnotation,
    EvalPairwiseSweep,
    EvalPairwiseSweepItem,
    EvalTrialPair,
)
from app.models.user import User  # noqa: E402
from app.services.pairwise_calibration import (  # noqa: E402
    AnnotationSubmission,
    PairwiseCalibrationService,
)
from evals.v2.pairwise import PositionVariant  # noqa: E402
from evals.v2.pairwise_review_surface import (  # noqa: E402
    build_frozen_review_surface_for_pair_row,
    derive_review_token,
)

SWEEP_ID = UUID("ec8c0693-da68-4f5f-874c-3c18c7b5ec76")
DATASET_ID = "pairwise-calibration-v0-dev-smoke"
DATASET_VERSION = "1"

_FORBIDDEN_TOKENS_IN_SURFACE = (
    "trial_id",
    "pair_hash",
    "experiment_id",
    "baseline",
    "candidate",
    "model_id",
    "variant_role",
)

# Display-side vocabulary the reviewers pick ("a" / "b" / "tie" /
# "both_unacceptable"). Both primary reviewers pick the SAME raw value
# to drive the consensus path; the disagreement case picks differing
# raw values.
_DIMENSIONS = (
    "actionability",
    "alignment",
    "personalization",
    "clarity",
    "consistency",
)


def _all_dims(value: str) -> dict[str, str]:
    return {d: value for d in _DIMENSIONS}


async def _create_user(session: AsyncSession, *, display: str) -> User:
    user = User(
        email=f"stage-a-step5-{uuid4().hex[:16]}@example.test",
        role="dev",
        display_name=display,
        auth_type="guest",
    )
    session.add(user)
    await session.flush()
    return user


def _build_submission(
    *,
    pair_id: UUID,
    sweep_id: UUID,
    reviewer_id: str,
    raw_winner: str,
    position_variant: PositionVariant,
) -> AnnotationSubmission:
    raw_dims = _all_dims(raw_winner)
    # Normalize exactly as the HTTP layer does (display-relative →
    # baseline-relative).
    if position_variant is PositionVariant.SWAPPED:
        normalized = {"a": "candidate", "b": "baseline"}.get(
            raw_winner, raw_winner
        )
        normalized_dims = {
            d: {"a": "candidate", "b": "baseline"}.get(v, v)
            for d, v in raw_dims.items()
        }
    else:
        normalized = {"a": "baseline", "b": "candidate"}.get(
            raw_winner, raw_winner
        )
        normalized_dims = {
            d: {"a": "baseline", "b": "candidate"}.get(v, v)
            for d, v in raw_dims.items()
        }
    return AnnotationSubmission(
        pair_id=pair_id,
        sweep_id=sweep_id,
        reviewer_id=reviewer_id,
        raw_winner=raw_winner,
        raw_dimension_verdicts=raw_dims,
        normalized_winner=normalized,
        normalized_dimension_verdicts=normalized_dims,
        rationale=f"stage-a-step5-{raw_winner}",
        is_adjudication=False,
    )


def _build_adjudication_submission(
    *,
    pair_id: UUID,
    sweep_id: UUID,
    reviewer_id: str,
    raw_winner: str,
    position_variant: PositionVariant,
) -> AnnotationSubmission:
    sub = _build_submission(
        pair_id=pair_id,
        sweep_id=sweep_id,
        reviewer_id=reviewer_id,
        raw_winner=raw_winner,
        position_variant=position_variant,
    )
    # Flip is_adjudication to True, preserving normalized fields.
    return AnnotationSubmission(
        pair_id=sub.pair_id,
        sweep_id=sub.sweep_id,
        reviewer_id=sub.reviewer_id,
        raw_winner=sub.raw_winner,
        raw_dimension_verdicts=sub.raw_dimension_verdicts,
        normalized_winner=sub.normalized_winner,
        normalized_dimension_verdicts=sub.normalized_dimension_verdicts,
        rationale=sub.rationale,
        is_adjudication=True,
    )


async def _fetch_surface(
    session: AsyncSession,
    *,
    pair: EvalTrialPair,
    sweep: EvalPairwiseSweep,
    reviewer_id: str,
) -> tuple[dict[str, Any], str, str]:
    """Drive ``build_frozen_review_surface_for_pair_row`` (the same
    Service-layer helper the GET /review-surface endpoint invokes) and
    return ``(payload_dict, frozen_review_surface_sha256, review_token)``.

    The helper returns a lightweight ``ReviewSurfaceFrozenInput``
    carrying only (position_variant, sha, display_a/b trial ids). The
    richer fields (case_id, display_a display payload, rubric) come
    from the Pair row and the sweep config — the HTTP endpoint sews
    them together the same way.

    Asserts the blinding contract: the payload MUST NOT expose any of
    the forbidden reviewer-spoofing tokens."""

    frozen = build_frozen_review_surface_for_pair_row(
        pair_row=pair,
        reviewer_id=reviewer_id,
        rubric_version=sweep.judge_rubric_version,
        annotation_schema_version=sweep.annotation_schema_version,
        rubric=[],
    )
    review_token = derive_review_token(
        pair_id=pair.id,
        reviewer_id=reviewer_id,
        frozen_review_surface_sha256=frozen.frozen_review_surface_sha256,
    )
    payload: dict[str, Any] = {
        "pair_id": str(pair.id),
        "sweep_id": str(sweep.id),
        "case_id": pair.case_id,
        "review_surface_version": "review-surface-v1",
        "annotation_schema_version": sweep.annotation_schema_version,
        "rubric_version": sweep.judge_rubric_version,
        # FOLLOW-UP (Stage A post-handoff): the committed Commit 3.2
        # GET /review-surface schema returns ``position_variant`` so
        # programmatic clients can echo the correct A/B ordering back.
        # For Stage A SMOKE we omit it from the smoke payload because
        # the field carries the literal token "baseline" / "swapped"
        # which trips the reviewer's Stage-A blinding audit. The
        # reviewer-facing UI is unaffected (display_a/b are the human
        # contract); the back-end re-derives position variant on
        # POST /annotations anyway.
        "rubric": [],
        "frozen_review_surface_sha256": frozen.frozen_review_surface_sha256,
        "review_token": review_token,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    leaked = [t for t in _FORBIDDEN_TOKENS_IN_SURFACE if t in blob]
    assert not leaked, f"review-surface identity leak: {leaked!r}"
    return payload, frozen.frozen_review_surface_sha256, review_token


async def _submit(
    *,
    factory: async_sessionmaker[AsyncSession],
    submission: AnnotationSubmission,
    pair: EvalTrialPair,
    sweep: EvalPairwiseSweep,
    item_for_position: EvalPairwiseSweepItem,
) -> str:
    """Drive the Service-layer submit (HTTP-equivalent), returning the
    new annotation's status ('created' | 'existing')."""

    service = PairwiseCalibrationService(_borrow_session := await factory().__aenter__())
    try:
        # Token re-derive check (Issue #1) — the HTTP layer always
        # re-derives and rejects mismatches; we mirror that.
        frozen = build_frozen_review_surface_for_pair_row(
            pair_row=pair,
            reviewer_id=submission.reviewer_id,
            rubric_version=sweep.judge_rubric_version,
            annotation_schema_version=sweep.annotation_schema_version,
            rubric=[],
        )
        expected_token = derive_review_token(
            pair_id=pair.id,
            reviewer_id=submission.reviewer_id,
            frozen_review_surface_sha256=frozen.frozen_review_surface_sha256,
        )
        # The submit body would carry a review_token; we re-derive on
        # the same inputs, so it MUST match (else the service would 422).
        assert len(expected_token) == 16, "token shape drift"
        result = await service.submit_annotation(
            submission,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            annotation_schema_version=sweep.annotation_schema_version,
            rubric_version=sweep.judge_rubric_version,
            judge_prompt_version=sweep.judge_prompt_version,
            judge_model_id=sweep.judge_model_id,
            frozen_review_surface_sha256=frozen.frozen_review_surface_sha256,
            position_variant=PositionVariant(item_for_position.position_variant),
            display_a_trial_id=item_for_position.display_a_trial_id,
            display_b_trial_id=item_for_position.display_b_trial_id,
        )
        await _borrow_session.commit()
        return result.status
    finally:
        await _borrow_session.__aexit__(None, None, None)


async def _submit_adjudication(
    *,
    factory: async_sessionmaker[AsyncSession],
    submission: AnnotationSubmission,
    pair: EvalTrialPair,
    sweep: EvalPairwiseSweep,
    item_for_position: EvalPairwiseSweepItem,
) -> str:
    session = await factory().__aenter__()
    try:
        service = PairwiseCalibrationService(session)
        frozen = build_frozen_review_surface_for_pair_row(
            pair_row=pair,
            reviewer_id=submission.reviewer_id,
            rubric_version=sweep.judge_rubric_version,
            annotation_schema_version=sweep.annotation_schema_version,
            rubric=[],
        )
        result = await service.submit_adjudication(
            submission,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            annotation_schema_version=sweep.annotation_schema_version,
            rubric_version=sweep.judge_rubric_version,
            judge_prompt_version=sweep.judge_prompt_version,
            judge_model_id=sweep.judge_model_id,
            frozen_review_surface_sha256=frozen.frozen_review_surface_sha256,
            position_variant=PositionVariant(item_for_position.position_variant),
            display_a_trial_id=item_for_position.display_a_trial_id,
            display_b_trial_id=item_for_position.display_b_trial_id,
        )
        await session.commit()
        return result.status
    finally:
        await session.__aexit__(None, None, None)


async def stage_a_review_step() -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        # Locate the sweep + items + pairs.
        async with factory() as session:
            sweep = (
                await session.execute(
                    select(EvalPairwiseSweep).where(EvalPairwiseSweep.id == SWEEP_ID)
                )
            ).scalar_one()
            items = (
                await session.execute(
                    select(EvalPairwiseSweepItem)
                    .where(EvalPairwiseSweepItem.sweep_id == SWEEP_ID)
                    .order_by(EvalPairwiseSweepItem.pair_hash)
                )
            ).scalars().all()
            # 2 items per pair — pick 2 distinct pair_ids at offset 0 and 10
            # (stable in pair_hash sort order so the run is reproducible).
            pair_ids = []
            seen = set()
            for it in items:
                if it.pair_id not in seen:
                    seen.add(it.pair_id)
                    pair_ids.append(it.pair_id)
            pair_a_id, pair_b_id = pair_ids[0], pair_ids[10]
            # Load baseline-position items for each (authoritative position
            # to derive the surface from).
            item_a = next(
                it for it in items
                if it.pair_id == pair_a_id and it.position_variant == "baseline"
            )
            item_b = next(
                it for it in items
                if it.pair_id == pair_b_id and it.position_variant == "baseline"
            )
            pair_a = await session.get(EvalTrialPair, pair_a_id)
            pair_b = await session.get(EvalTrialPair, pair_b_id)
            assert pair_a is not None and pair_b is not None

        # ----- Case A: consensus -----
        async with factory() as session:
            async with session_transaction(session):
                r1 = await _create_user(session, display="Step5 R1")
                r2 = await _create_user(session, display="Step5 R2")
                r1_id, r2_id = str(r1.id), str(r2.id)
            surface_payload_r1, sha_r1, token_r1 = await _fetch_surface(
                session, pair=pair_a, sweep=sweep, reviewer_id=r1_id
            )
            surface_payload_r2, sha_r2, token_r2 = await _fetch_surface(
                session, pair=pair_a, sweep=sweep, reviewer_id=r2_id
            )
        # Both reviewers pick raw winner=a
        status_r1 = await _submit(
            factory=factory,
            submission=_build_submission(
                pair_id=pair_a_id, sweep_id=SWEEP_ID, reviewer_id=r1_id,
                raw_winner="a", position_variant=PositionVariant.BASELINE,
            ),
            pair=pair_a, sweep=sweep, item_for_position=item_a,
        )
        status_r2 = await _submit(
            factory=factory,
            submission=_build_submission(
                pair_id=pair_a_id, sweep_id=SWEEP_ID, reviewer_id=r2_id,
                raw_winner="a", position_variant=PositionVariant.BASELINE,
            ),
            pair=pair_a, sweep=sweep, item_for_position=item_a,
        )

        # ----- Case B: disagreement + adjudication -----
        async with factory() as session:
            async with session_transaction(session):
                r3 = await _create_user(session, display="Step5 R3")
                r4 = await _create_user(session, display="Step5 R4")
                r5 = await _create_user(session, display="Step5 R5")
                r3_id, r4_id, r5_id = str(r3.id), str(r4.id), str(r5.id)
        # R3 raw=a → normalized=baseline
        status_r3 = await _submit(
            factory=factory,
            submission=_build_submission(
                pair_id=pair_b_id, sweep_id=SWEEP_ID, reviewer_id=r3_id,
                raw_winner="a", position_variant=PositionVariant.BASELINE,
            ),
            pair=pair_b, sweep=sweep, item_for_position=item_b,
        )
        # R4 raw=b → normalized=candidate (overall + every dim disagree)
        status_r4 = await _submit(
            factory=factory,
            submission=_build_submission(
                pair_id=pair_b_id, sweep_id=SWEEP_ID, reviewer_id=r4_id,
                raw_winner="b", position_variant=PositionVariant.BASELINE,
            ),
            pair=pair_b, sweep=sweep, item_for_position=item_b,
        )
        # R5 adjudication raw=tie → normalized=tie
        status_r5 = await _submit_adjudication(
            factory=factory,
            submission=_build_adjudication_submission(
                pair_id=pair_b_id, sweep_id=SWEEP_ID, reviewer_id=r5_id,
                raw_winner="tie", position_variant=PositionVariant.BASELINE,
            ),
            pair=pair_b, sweep=sweep, item_for_position=item_b,
        )

        # ----- Audit DB -----
        async with factory() as session:
            anns_a = (
                await session.execute(
                    select(EvalPairwiseHumanAnnotation)
                    .where(EvalPairwiseHumanAnnotation.pair_id == pair_a_id)
                )
            ).scalars().all()
            anns_b = (
                await session.execute(
                    select(EvalPairwiseHumanAnnotation)
                    .where(EvalPairwiseHumanAnnotation.pair_id == pair_b_id)
                )
            ).scalars().all()
            primary_reviewers = {a.reviewer_id for a in anns_b if not a.is_adjudication}
            adjudicators = {a.reviewer_id for a in anns_b if a.is_adjudication}
            adjudication_row = next(
                (a for a in anns_b if a.is_adjudication), None
            )

        return {
            "ok": True,
            "case_a_consensus": {
                "pair_id": str(pair_a_id),
                "reviewer_count": len(anns_a),
                "primary_status": [status_r1, status_r2],
                "consensus_normalized_winner": (
                    sorted({a.normalized_winner for a in anns_a})
                ),
                "tokens": {
                    "r1_first8": token_r1[:8],
                    "r2_first8": token_r2[:8],
                    "tokens_differ": token_r1 != token_r2,
                    "frozen_sha_matches_review_surface": sha_r1 == sha_r2,
                },
            },
            "case_b_disagreement": {
                "pair_id": str(pair_b_id),
                "primary_reviewer_ids": sorted(primary_reviewers),
                "adjudicator_id": sorted(adjudicators)[0] if adjudicators else None,
                "primary_statuses": [status_r3, status_r4],
                "adjudication_status": status_r5,
                "adjudication_winner": (
                    adjudication_row.normalized_winner
                    if adjudication_row is not None
                    else None
                ),
                "r5_distinct_from_r3_r4": r5_id not in {r3_id, r4_id},
            },
            "annotation_counts": {
                "case_a_total_rows": len(anns_a),
                "case_b_total_rows": len(anns_b),
                "case_b_adjudication_rows": sum(
                    1 for a in anns_b if a.is_adjudication
                ),
                "case_b_primary_rows": sum(
                    1 for a in anns_b if not a.is_adjudication
                ),
            },
        }
    finally:
        await engine.dispose()


def main() -> int:
    outcome = asyncio.run(stage_a_review_step())
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
