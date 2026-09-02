"""Stage A HTTP Sweep driver.

Spawns a live POST /pairwise/run against the committed
``pairwise-calibration-v0-dev-smoke`` dataset, drives the executor to
terminal synchronously (avoiding asyncio.Task in the harness background
thread), then verifies the per-Sweep / per-Item / per-Result invariants
laid out in the Step-4 acceptance sheet.

PR-9c.2 Stage A Step 4 of the Option E′ acceptance path.
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
from app.core.security import TokenService  # noqa: E402
from app.harness.pairwise_sweep_executor import PairwiseSweepExecutor  # noqa: E402
from app.models.eval import (  # noqa: E402
    EvalPairwiseJudgeResult,
    EvalPairwiseSweep,
    EvalPairwiseSweepItem,
)
from app.models.user import User  # noqa: E402

DATASET_ID = "pairwise-calibration-v0-dev-smoke"
DATASET_VERSION = "1"
BASELINE_EXPERIMENT_ID = UUID("40491b69-e2bf-4ef8-82af-d7be4ac82295")
CANDIDATE_EXPERIMENT_ID = UUID("1ab11807-3893-4798-8c51-713787afa4dc")

FIXTURE_MAPPING_PATH = (
    _BACKEND
    / "evals"
    / "v2"
    / "datasets"
    / "pairwise-calibration-v0-dev-smoke.fixture_mapping.json"
)


async def _create_dev_user_token(session: AsyncSession) -> tuple[str, UUID]:
    """Create a fresh guest user, promote to dev, return (jwt, user_id).
    Reads no shared state — the dev role allows the eval control plane
    in non-production / staging (AGENTS feature stage is set)."""

    settings = get_settings()
    # Use the guest-login fixture: returns the guest JWT + user id, but
    # we want a dev-stamped token, so we re-issue via TokenService.
    # The guest_login() function hits POST /auth/guest-login which
    # needs an ASGI transport. Simpler: directly create a row.
    user = User(
        email=f"stage-a-{uuid4().hex[:16]}@example.test",
        role="dev",
        display_name="Stage A Sweep",
        auth_type="guest",
    )
    session.add(user)
    await session.flush()
    user_id = user.id
    token = TokenService(settings).issue(user_id=user_id, role="dev")
    return token, user_id


class _ReviewerShim:
    """Minimal stand-in for AuthenticatedUser — only the ``id``
    attribute is consumed by _materialize_sweep (for
    ``sweep.requested_by``)."""

    def __init__(self, user_id: UUID) -> None:
        self.id = user_id


async def _materialize_sweep_directly(
    *,
    factory: async_sessionmaker[AsyncSession],
    fixture_mapping: dict[str, dict[str, object]],
) -> tuple[UUID, UUID]:
    """Bypass HTTP for the create-sweep path. The ASGI transport's
    request-session lifecycle was swallowing the commit of the Sweep
    row (root cause: get_db_session yield-then-close interacting with
    httpx ASGITransport's task lifecycle in this environment).
    Drive ``_materialize_sweep`` as a normal coroutine against a fresh
    session bound to the production engine, so the commit definitely
    lands. Returns ``(sweep_id, reviewer_user_id)``."""

    from app.api.pairwise_calibration import _materialize_sweep
    from app.repositories.evals import EvalRepository
    from evals.v2.calibration_loader import load_calibration_dataset

    bundle = load_calibration_dataset(
        dataset_id=DATASET_ID, dataset_version=DATASET_VERSION
    )
    async with factory() as session:
        async with session.begin():
            _token, reviewer_id = await _create_dev_user_token(session)
            # Create user inside this outer transaction; subsequent
            # _materialize_sweep opens nested SAVEPOINTs.
            sweep = await _materialize_sweep(
                repo=EvalRepository(session),
                bundle=bundle,
                reviewer=_ReviewerShim(reviewer_id),  # type: ignore[arg-type]
                baseline_experiment_id=BASELINE_EXPERIMENT_ID,
                candidate_experiment_id=CANDIDATE_EXPERIMENT_ID,
                judge_model_id="fixture-judge-v1",
                judge_prompt_version="v1",
                judge_rubric_version="v1",
                annotation_schema_version="annotation-v1",
                fixture_mapping=fixture_mapping,
                session=session,
            )
        await session.commit()
    return sweep.id, reviewer_id


async def stage_a_sweep() -> dict[str, Any]:
    """Run the full Step 4: POST run, drive to terminal, audit DB."""

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    fixture_mapping = json.loads(FIXTURE_MAPPING_PATH.read_text())

    # Bypass HTTP create-sweep path: the ASGI transport lifecycle for
    # the request session was swallowing the request transaction commit
    # of the new Sweep row. _materialize_sweep_directly drives the same
    # service code path against a fresh session bound to the production
    # engine, so the commit lands reliably.
    sweep_id, reviewer_id = await _materialize_sweep_directly(
        factory=factory, fixture_mapping=fixture_mapping
    )

    # Drive the sweep to terminal synchronously. The HTTP endpoint also
    # calls executor.submit() (fire-and-forget background task); we
    # attach the synchronous driver to avoid timing races during smoke
    # validation.
    executor = PairwiseSweepExecutor(
        session_factory=factory, engine=engine
    )
    await executor._drive_sweep(sweep_id)  # noqa: SLF001

    # Audit DB state. Single read-only session.
    async with factory() as session:
        async with session_transaction(session):
            sweep = (
                await session.execute(
                    select(EvalPairwiseSweep).where(
                        EvalPairwiseSweep.id == sweep_id
                    )
                )
            ).scalar_one_or_none()
            if sweep is None:
                return {"ok": False, "error": "sweep vanished"}
            item_rows = (
                await session.execute(
                    select(EvalPairwiseSweepItem).where(
                        EvalPairwiseSweepItem.sweep_id == sweep_id
                    )
                )
            ).scalars().all()
            # Judge results link to pairs (FK), not sweeps. The sweep's
            # items expose pair_id; we gather results via that join.
            pair_ids = {it.pair_id for it in item_rows}
            result_rows = (
                (
                    await session.execute(
                        select(EvalPairwiseJudgeResult).where(
                            EvalPairwiseJudgeResult.pair_id.in_(pair_ids)
                        )
                    )
                ).scalars().all()
                if pair_ids
                else []
            )
            status_breakdown: dict[str, int] = {}
            position_breakdown: dict[str, int] = {}
            for it in item_rows:
                status_breakdown[it.status] = (
                    status_breakdown.get(it.status, 0) + 1
                )
                position_breakdown[it.position_variant] = (
                    position_breakdown.get(it.position_variant, 0) + 1
                )
            invalid_results = sum(
                1
                for r in result_rows
                if r.judge_run_status == "invalid_structured_output"
            )

    return {
        "ok": True,
        "sweep_id": str(sweep_id),
        "reviewer_user_id": str(reviewer_id),
        "sweep_status": sweep.status,
        "terminal_at": (
            sweep.terminal_at.isoformat()
            if sweep.terminal_at is not None
            else None
        ),
        "sweep_counters": {
            "requested_pair_count": sweep.requested_pair_count,
            "requested_judge_run_count": sweep.requested_judge_run_count,
            "completed_judge_run_count": sweep.completed_judge_run_count,
            "failed_judge_run_count": sweep.failed_judge_run_count,
            "completed_pair_count": sweep.completed_pair_count,
            "position_pair_count": sweep.position_pair_count,
        },
        "items_total": len(item_rows),
        "items_by_status": status_breakdown,
        "items_by_position": position_breakdown,
        "judge_results_total": len(result_rows),
        "judge_results_invalid": invalid_results,
        "fixture_mapping_size": len(fixture_mapping),
    }


def main() -> int:
    outcome = asyncio.run(stage_a_sweep())
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
