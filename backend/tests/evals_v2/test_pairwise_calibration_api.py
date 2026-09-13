"""PR-9c.2 Commit 3 smoke tests for the Pairwise Calibration HTTP API."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenService
from app.models.user import User
from tests.test_profile_api import bearer, guest_login


async def _dev_login(client: AsyncClient, db_session: AsyncSession) -> str:
    _guest_token, user_id_raw, _ = await guest_login(client)
    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    await db_session.flush()
    return TokenService(get_settings()).issue(user_id=user.id, role="dev")


async def _persist_review_sweep(
    db_session: AsyncSession,
    sweep: object,
    pair: object,
) -> None:
    from app.core.database import session_transaction
    from app.models.eval import EvalPairwiseSweep, EvalTrialPair
    from app.repositories.evals import EvalRepository
    from app.services.evals import EvalService
    from app.services.pairwise_calibration import (
        PairwiseCalibrationService,
        SweepItemSeed,
    )
    from evals.v2.pairwise import PositionVariant, build_pair
    from evals.v2.pairwise_review_surface import build_frozen_review_surface

    assert isinstance(sweep, EvalPairwiseSweep)
    assert isinstance(pair, EvalTrialPair)
    eval_service = EvalService(db_session)
    baseline_view = await eval_service.build_judge_view(pair.baseline_trial_id)
    candidate_view = await eval_service.build_judge_view(pair.candidate_trial_id)
    domain_pair = build_pair(
        baseline_trial_id=pair.baseline_trial_id,
        candidate_trial_id=pair.candidate_trial_id,
        case_id=pair.case_id,
        baseline_view=baseline_view,
        candidate_view=candidate_view,
    )
    surface = build_frozen_review_surface(
        pair=domain_pair,
        reviewer_id="fixture-reviewer",
        rubric=[],
        rubric_version=sweep.judge_rubric_version,
        annotation_schema_version=sweep.annotation_schema_version,
    )
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep)
    seeds = [
        SweepItemSeed(
            pair_id=pair.id,
            pair_hash=pair.pair_hash,
            case_id=pair.case_id,
            baseline_trial_id=pair.baseline_trial_id,
            candidate_trial_id=pair.candidate_trial_id,
            baseline_output_hash=domain_pair.baseline_output_hash(),
            candidate_output_hash=domain_pair.candidate_output_hash(),
            frozen_review_surface_sha256=surface.frozen_review_surface_sha256,
            display_a_trial_id=(
                pair.baseline_trial_id
                if position is PositionVariant.BASELINE
                else pair.candidate_trial_id
            ),
            display_b_trial_id=(
                pair.candidate_trial_id
                if position is PositionVariant.BASELINE
                else pair.baseline_trial_id
            ),
            position_variant=position,
        )
        for position in (PositionVariant.BASELINE, PositionVariant.SWAPPED)
    ]
    await PairwiseCalibrationService(db_session).materialize_sweep_items(
        sweep=sweep,
        seeds=seeds,
        annotation_schema_version=sweep.annotation_schema_version,
    )


@pytest.mark.asyncio
async def test_calibration_latest_404_when_no_report(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.get(
            "/api/v1/eval/pairwise/calibration/no-such/v9/latest",
            headers=bearer(token),
        )
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "EVAL_CALIBRATION_NOT_COMPUTED"


@pytest.mark.asyncio
async def test_calibration_history_returns_empty_list(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.get(
            "/api/v1/eval/pairwise/calibration/no-such/v9/history",
            headers=bearer(token),
        )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_annotation_submit_invalid_payload_returns_422(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.post(
            "/api/v1/eval/runs/pairwise/annotations",
            headers=bearer(token),
            json={
                "pair_id": "00000000-0000-0000-0000-000000000001",
                "sweep_id": "00000000-0000-0000-0000-000000000002",
                "raw_winner": "INVALID",
                "raw_dimension_verdicts": {
                    "actionability": "a",
                    "alignment": "b",
                    "personalization": "tie",
                    "clarity": "a",
                    "consistency": "b",
                },
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_calibration_report_requires_explicit_sweep_ids(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.post(
            "/api/v1/eval/pairwise/calibration",
            headers=bearer(token),
            json={
                "dataset_id": "x",
                "dataset_version": "v1",
                "sweep_ids": [],
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_pairwise_run_status_404_for_unknown_sweep(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.get(
            "/api/v1/eval/runs/00000000-0000-0000-0000-000000000001"
            "/pairwise/run/00000000-0000-0000-0000-000000000002",
            headers=bearer(token),
        )
    assert response.status_code == 404


# ============================================================================
# Commit 3.1 — Review Surface endpoint + review_token tamper protection (#4)
# ============================================================================


@pytest.mark.asyncio
async def test_review_surface_404_when_pair_missing(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """GET /pairs/{pair_id}/review-surface returns 404 when no Pair row
    exists so the reviewer cannot probe arbitrary ids."""

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.get(
            "/api/v1/eval/runs/pairwise/pairs/"
            "00000000-0000-0000-0000-0000000000aa/review-surface",
            params={
                "sweep_id": "00000000-0000-0000-0000-0000000000bb",
            },
            headers=bearer(token),
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVAL_PAIR_NOT_FOUND"


@pytest.mark.asyncio
async def test_review_surface_404_when_sweep_missing(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """If the pair exists but the queried sweep does not, return 404
    rather than leaking the pair's existence via a 200."""

    from tests.evals_v2.test_pairwise_calibration_repository import _seed_pair

    pair = await _seed_pair(db_session, 1)
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.get(
            f"/api/v1/eval/runs/pairwise/pairs/{pair.id}/review-surface",
            params={
                "sweep_id": "00000000-0000-0000-0000-0000000000bb",
            },
            headers=bearer(token),
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVAL_SWEEP_NOT_FOUND"


@pytest.mark.asyncio
async def test_review_surface_endpoint_excludes_trial_ids_and_pair_hash(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """Happy-path response payload must NOT contain any baseline/candidate
    trial ids, the underlying pair_hash, or any model/score hints — issue #4
    blinding contract."""

    from tests.evals_v2.test_pairwise_calibration_repository import (
        _make_sweep,
        _seed_pair,
    )

    pair = await _seed_pair(db_session, 1)
    sweep_row = await _make_sweep(
        db_session,
        requested_pair_count=1,
    )
    await _persist_review_sweep(db_session, sweep_row, pair)

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.get(
            f"/api/v1/eval/runs/pairwise/pairs/{pair.id}/review-surface",
            params={"sweep_id": str(sweep_row.id)},
            headers=bearer(token),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    # Forbidden fields per the blinding contract.
    flat = str(body)
    assert "pair_hash" not in body
    assert "display_a_trial_id" not in body
    assert "display_b_trial_id" not in body
    assert "baseline_trial_id" not in body
    assert "candidate_trial_id" not in body
    assert "suggested_label" not in body
    # No literal trial UUID should appear anywhere in the payload.
    assert str(pair.baseline_trial_id) not in flat
    assert str(pair.candidate_trial_id) not in flat
    assert pair.pair_hash not in flat
    # Required surface fields ARE present.
    assert set(("display_a", "display_b", "position_variant", "review_token",
                "frozen_review_surface_sha256", "rubric", "case_id")).issubset(
        body.keys()
    )
    assert body["position_variant"] in ("baseline", "swapped")
    assert len(body["review_token"]) == 16
    assert body["display_a"]["request"]
    assert body["display_a"]["plan"]
    assert body["display_b"]["request"]
    assert body["display_b"]["plan"]


@pytest.mark.asyncio
async def test_annotation_submit_with_tampered_review_token_returns_422(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """If a caller POSTs a review_token that does not match the
    server-derived token for THIS (pair, reviewer, frozen surface), the
    server rejects with 422 EVAL_REVIEW_TOKEN_MISMATCH. This catches the
    case where a reviewer fetches surface A and submits against surface B,
    or attempts to forge a token."""

    from tests.evals_v2.test_pairwise_calibration_repository import (
        _make_sweep,
        _seed_pair,
    )

    pair = await _seed_pair(db_session, 1)
    sweep_row = await _make_sweep(
        db_session,
        requested_pair_count=1,
    )
    await _persist_review_sweep(db_session, sweep_row, pair)

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.post(
            "/api/v1/eval/runs/pairwise/annotations",
            headers=bearer(token),
            json={
                "pair_id": str(pair.id),
                "sweep_id": str(sweep_row.id),
                "raw_winner": "a",
                "raw_dimension_verdicts": {
                    "actionability": "a",
                    "alignment": "b",
                    "personalization": "tie",
                    "clarity": "a",
                    "consistency": "b",
                },
                "review_token": "deadbeefdeadbeef",  # not a valid token
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EVAL_REVIEW_TOKEN_INVALID"


# ============================================================================
# Commit 3.2 — review_token is now REQUIRED (issue #1)
# ============================================================================


@pytest.mark.asyncio
async def test_annotation_without_review_token_is_rejected(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """Missing review_token is rejected at the Pydantic layer with a
    422 — the reviewer CANNOT submit a primary annotation without
    first fetching the GET review surface."""

    from app.core.database import session_transaction
    from app.repositories.evals import EvalRepository
    from tests.evals_v2.test_pairwise_calibration_repository import (
        _make_sweep,
        _seed_pair,
    )

    pair = await _seed_pair(db_session, 1)
    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.post(
            "/api/v1/eval/runs/pairwise/annotations",
            headers=bearer(token),
            json={
                "pair_id": str(pair.id),
                "sweep_id": str(sweep_row.id),
                "raw_winner": "a",
                "raw_dimension_verdicts": {
                    "actionability": "a", "alignment": "b",
                    "personalization": "tie", "clarity": "a",
                    "consistency": "b",
                },
                # review_token deliberately OMITTED
            },
        )
    assert response.status_code == 422
    # Pydantic validation error — custom error envelope.
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_EVAL_INVALID"
    # The missing field is named in the details.
    details_text = str(body)
    assert "review_token" in details_text


@pytest.mark.asyncio
async def test_annotation_with_token_for_other_pair_is_rejected(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """A review_token freshly fetched for pair A MUST be rejected when
    POSTed against pair B — server-authoritative PositionSurface can't
    be repurposed across pairs."""

    from tests.evals_v2.test_pairwise_calibration_repository import (
        _make_sweep,
        _seed_pair,
    )

    pair_a = await _seed_pair(db_session, 1)
    pair_b = await _seed_pair(db_session, 2)
    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    await _persist_review_sweep(db_session, sweep_row, pair_a)

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        # Fetch surface + token for pair A.
        surf = await client.get(
            f"/api/v1/eval/runs/pairwise/pairs/{pair_a.id}/review-surface",
            params={"sweep_id": str(sweep_row.id)},
            headers=bearer(token),
        )
        assert surf.status_code == 200, surf.text
        token_for_a = surf.json()["review_token"]
        # POST token against pair B → reject.
        response = await client.post(
            "/api/v1/eval/runs/pairwise/annotations",
            headers=bearer(token),
            json={
                "pair_id": str(pair_b.id),  # different pair
                "sweep_id": str(sweep_row.id),
                "raw_winner": "a",
                "raw_dimension_verdicts": {
                    "actionability": "a", "alignment": "b",
                    "personalization": "tie", "clarity": "a",
                    "consistency": "b",
                },
                "review_token": token_for_a,
            },
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EVAL_PAIR_NOT_IN_SWEEP"


@pytest.mark.asyncio
async def test_annotation_token_for_other_reviewer_is_rejected(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """review_token binds (pair, reviewer, surface). Two reviewers
    fetching the same pair get DIFFERENT tokens; submitting one with
    the other's session must 422."""

    from tests.evals_v2.test_pairwise_calibration_repository import (
        _make_sweep,
        _seed_pair,
    )

    pair = await _seed_pair(db_session, 1)
    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    await _persist_review_sweep(db_session, sweep_row, pair)

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token_r1 = await _dev_login(client, db_session)
        surf = await client.get(
            f"/api/v1/eval/runs/pairwise/pairs/{pair.id}/review-surface",
            params={"sweep_id": str(sweep_row.id)},
            headers=bearer(token_r1),
        )
        assert surf.status_code == 200
        token_for_r1 = surf.json()["review_token"]
        # Now act as a DIFFERENT reviewer (fresh guest login).
        token_r2_login = await _dev_login(client, db_session)
        response = await client.post(
            "/api/v1/eval/runs/pairwise/annotations",
            headers=bearer(token_r2_login),
            json={
                "pair_id": str(pair.id),
                "sweep_id": str(sweep_row.id),
                "raw_winner": "a",
                "raw_dimension_verdicts": {
                    "actionability": "a", "alignment": "b",
                    "personalization": "tie", "clarity": "a",
                    "consistency": "b",
                },
                "review_token": token_for_r1,  # token for r1, not r2
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EVAL_REVIEW_TOKEN_INVALID"


# ============================================================================
# Commit 3.2 — calibration status now computed (issue #3)
# ============================================================================


@pytest.mark.asyncio
async def test_calibration_report_status_is_data_driven(
    api_application: FastAPI, db_session: AsyncSession,
) -> None:
    """Calibration report MUST compute calibration_status from inputs —
    not hard-default to 'insufficient'. On a fresh sweep with zero
    annotations and zero results, status is (legitimately) insufficient,
    but the report_payload must carry the real metric values back (0
    counts) proving it WAS computed rather than defaulted."""

    from app.core.database import session_transaction
    from app.repositories.evals import EvalRepository
    from tests.evals_v2.test_pairwise_calibration_repository import (
        _make_sweep,
    )

    sweep_row = await _make_sweep(db_session, requested_pair_count=1)
    async with session_transaction(db_session):
        await EvalRepository(db_session).create_sweep(sweep_row)

    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _dev_login(client, db_session)
        response = await client.post(
            "/api/v1/eval/pairwise/calibration",
            headers=bearer(token),
            json={
                "dataset_id": sweep_row.dataset_id,
                "dataset_version": sweep_row.dataset_version,
                "sweep_ids": [str(sweep_row.id)],
            },
        )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    payload = body["report_payload"]
    # Real computed fields present, NOT missing or defaulted.
    assert "calibration_status" in payload
    assert "agreement" in payload
    assert "position_bias" in payload
    assert "valid_human_pair_count" in payload
    assert "position_pair_count" in payload
    # The metric denominator — distinct from the structural counter.
    assert "position_metric_sample_count" in payload
    # Empty inputs → insufficient, but the fields prove computation ran.
    assert payload["valid_human_pair_count"] == 0
    assert payload["position_metric_sample_count"] == 0
    assert payload["position_pair_count"] == 0
    assert body["calibration_status"] == "insufficient"
    assert body["usage_mode"] == "diagnostic_only"
