"""Stage B-1 HTTP Sweep driver (variant-backed).

Drives the ``pairwise-calibration-v1-candidate`` dataset through the
same PairwiseSweepExecutor path validated by Stage A, but sourced from
the Stage B variant-backed experiments (``compact_execution_v1`` vs
``structured_reasoning_v1``).

Like Stage A, this run uses the FixturePairwiseJudge over a
diagnostic-only ``fixture_mapping`` (all-tie) so the Sweep can complete
end-to-end without a real judge LLM. The downstream Calibration Report
is therefore expected to surface ``calibration_status='insufficient'``
and ``usage_mode='diagnostic_only'`` -- the value of this step is
end-to-end pipeline validation, not real agreement numbers.

PR-9c.2 Stage B-1 (Commit 3.5 onwards).
"""

from __future__ import annotations

import argparse
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

DATASET_ID = "pairwise-calibration-v1-candidate"
DATASET_VERSION = "v1"

FIXTURE_MAPPING_PATH = (
    _BACKEND
    / "evals"
    / "v2"
    / "datasets"
    / "pairwise-calibration-v1-candidate.fixture_mapping.json"
)


async def _create_dev_user_token(session: AsyncSession) -> tuple[str, UUID]:
    """Create a fresh dev user and re-issue a dev-stamped token.

    Mirrors ``stage_a_sweep._create_dev_user_token``: the dev role
    unlocks the eval control plane in non-production feature stages.
    """

    settings = get_settings()
    user = User(
        email=f"stage-b1-{uuid4().hex[:16]}@example.test",
        role="dev",
        display_name="Stage B-1 Sweep",
        auth_type="guest",
    )
    session.add(user)
    await session.flush()
    user_id = user.id
    token = TokenService(settings).issue(user_id=user_id, role="dev")
    return token, user_id


class _ReviewerShim:
    def __init__(self, user_id: UUID) -> None:
        self.id = user_id


async def _materialize_sweep_directly(
    *,
    factory: async_sessionmaker[AsyncSession],
    fixture_mapping: dict[str, dict[str, object]],
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
) -> tuple[UUID, UUID]:
    """Bypass HTTP for the create-sweep path (same rationale as Stage A:
    the ASGI transport's request-session lifecycle was swallowing the
    Sweep row's commit in this environment).
    """

    from app.api.pairwise_calibration import _materialize_sweep
    from app.repositories.evals import EvalRepository
    from evals.v2.calibration_loader import load_calibration_dataset

    bundle = load_calibration_dataset(
        dataset_id=DATASET_ID, dataset_version=DATASET_VERSION
    )
    async with factory() as session:
        async with session.begin():
            _token, reviewer_id = await _create_dev_user_token(session)
            sweep = await _materialize_sweep(
                repo=EvalRepository(session),
                bundle=bundle,
                reviewer=_ReviewerShim(reviewer_id),  # type: ignore[arg-type]
                baseline_experiment_id=baseline_experiment_id,
                candidate_experiment_id=candidate_experiment_id,
                judge_model_id="fixture-judge-v1",
                judge_prompt_version="v1",
                judge_rubric_version="v1",
                annotation_schema_version="annotation-v1",
                fixture_mapping=fixture_mapping,
                session=session,
            )
        await session.commit()
    return sweep.id, reviewer_id


async def stage_b1_sweep(
    *,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
) -> dict[str, Any]:
    """Run the Stage B-1 Sweep end-to-end."""

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    fixture_mapping = json.loads(FIXTURE_MAPPING_PATH.read_text())

    sweep_id, reviewer_id = await _materialize_sweep_directly(
        factory=factory,
        fixture_mapping=fixture_mapping,
        baseline_experiment_id=baseline_experiment_id,
        candidate_experiment_id=candidate_experiment_id,
    )

    executor = PairwiseSweepExecutor(
        session_factory=factory, engine=engine
    )
    await executor._drive_sweep(sweep_id)  # noqa: SLF001

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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--baseline-experiment-id",
        required=True,
        type=UUID,
    )
    p.add_argument(
        "--candidate-experiment-id",
        required=True,
        type=UUID,
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    outcome = asyncio.run(
        stage_b1_sweep(
            baseline_experiment_id=args.baseline_experiment_id,
            candidate_experiment_id=args.candidate_experiment_id,
        )
    )
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
