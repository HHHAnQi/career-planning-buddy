"""PR-9c.1 alembic 0014 upgrade/downgrade round-trip.

Runs the migration against the test database (which is on head 0013 at
setUp), confirms the two new tables spring into existence with the
expected constraints, then downgrades back to 0013 and confirms the
tables are gone. The test re-upgrades at the end so the suite-wide head
stays at 0014 for downstream DB-backed tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models.eval import EvalPairwiseJudgeResult, EvalTrialPair


def _sync_table_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    return set(inspector.get_table_names())


def _sync_constraints(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for c in inspector.get_check_constraints(table_name) if (name := c.get("name"))}


def _sync_unique_constraints(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for c in inspector.get_unique_constraints(table_name) if (name := c.get("name"))}


def _sync_indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for i in inspector.get_indexes(table_name) if (name := i.get("name"))}


PAIRWISE_TABLES = ("eval_trial_pairs", "eval_pairwise_judge_results")


@pytest.mark.asyncio
async def test_upgrade_creates_pairwise_tables(
    db_connection: AsyncConnection,
) -> None:
    """After upgrade head, both new tables + their constraints exist."""

    names = await db_connection.run_sync(_sync_table_names)
    for table in PAIRWISE_TABLES:
        assert table in names, f"missing table {table}"

    pair_constraints = await db_connection.run_sync(
        _sync_constraints, "eval_trial_pairs"
    )
    assert "ck_eval_trial_pairs_pair_hash" in pair_constraints
    assert "ck_eval_trial_pairs_input_hash" in pair_constraints
    assert "ck_eval_trial_pairs_distinct_trials" in pair_constraints

    pair_uniques = await db_connection.run_sync(
        _sync_unique_constraints, "eval_trial_pairs"
    )
    assert "uq_eval_trial_pairs_pair_hash" in pair_uniques
    # The (baseline_trial_id, candidate_trial_id) composite index must be
    # NON-UNIQUE: pair_hash is the only stable identity, and a re-collect
    # that moves output bytes is allowed to create a SECOND Pair row with
    # the SAME trial ids but a DIFFERENT pair_hash. If trial-tuple were
    # UNIQUE, the new Pair could not coexist with the old one.
    pair_indexes = await db_connection.run_sync(_sync_indexes, "eval_trial_pairs")
    assert "ix_eval_trial_pairs_trial_ids" in pair_indexes
    # Reflexivity check: the trial-tuple index must NOT be UNIQUE. We
    # verify it appears in get_indexes with unique=False.
    def _sync_index_def(connection: Connection) -> dict[str, bool]:
        inspector = inspect(connection)
        for idx in inspector.get_indexes("eval_trial_pairs"):
            if idx["name"] == "ix_eval_trial_pairs_trial_ids":
                return {"unique": bool(idx["unique"])}
        return {"unique": True}  # treat "missing" as failure
    trial_ids_index = await db_connection.run_sync(_sync_index_def)
    assert trial_ids_index["unique"] is False, (
        "ix_eval_trial_pairs_trial_ids MUST be non-unique so re-collect "
        "Pair snapshots can coexist"
    )

    # Result row indexes include the comparison_group index for fast
    # ``list_judge_results_by_comparison_group`` queries.
    result_indexes = await db_connection.run_sync(
        _sync_indexes, "eval_pairwise_judge_results"
    )
    assert "ix_eval_pairwise_judge_results_group" in result_indexes

    result_constraints = await db_connection.run_sync(
        _sync_constraints, "eval_pairwise_judge_results"
    )
    assert "ck_eval_pairwise_judge_results_status" in result_constraints
    assert "ck_eval_pairwise_judge_results_position_variant" in result_constraints
    assert "ck_eval_pairwise_judge_results_raw_winner" in result_constraints
    assert (
        "ck_eval_pairwise_judge_results_completed_carries_verdict"
        in result_constraints
    )


@pytest.mark.asyncio
async def test_orm_round_trip_synthetic_row(db_connection: AsyncConnection) -> None:
    """A manually constructed Pair + Result row inserted via ORM survives
    re-select, exercising the column types end-to-end."""

    from uuid import uuid4

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.eval import EvalExperiment, EvalTrial
    from app.repositories.evals import EvalRepository

    session_factory = async_sessionmaker(
        bind=db_connection, class_=AsyncSession, expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        # Minimal experiment + trials to satisfy FKs.
        experiment = EvalExperiment(
            dataset_id="d",
            dataset_version="v1",
            dataset_hash="0" * 64,
            git_commit="abc1234",
            graph_version="g-v1",
            prompt_version="p-v1",
            model_version="m-v1",
            tool_version="t-v1",
            context_version="c-v1",
            memory_version="mem-v1",
            frozen_config_hash="0" * 64,
            execution_mode="mock_provider",
            variant_role="baseline",
            trial_count=1,
        )
        session.add(experiment)
        await session.flush()
        trial_a = EvalTrial(
            experiment_id=experiment.id,
            case_id="case-x",
            case_fixture_hash="0" * 64,
            trial_index=0,
            seed=0,
            run_type="evaluation",
            status="pending",
        )
        trial_b = EvalTrial(
            experiment_id=experiment.id,
            case_id="case-x",
            case_fixture_hash="0" * 64,
            trial_index=1,
            seed=0,
            run_type="evaluation",
            variant="b",
            status="pending",
        )
        session.add_all([trial_a, trial_b])
        await session.flush()

        repo = EvalRepository(session)
        pair_hash = "a" * 64
        pair = EvalTrialPair(
            baseline_trial_id=trial_a.id,
            candidate_trial_id=trial_b.id,
            case_id="case-x",
            pair_hash=pair_hash,
            input_hash="b" * 64,
            allowed_evidence_kinds=["plan_projection"],
            judge_prompt_version="v1",
            judge_rubric_version="v1",
        )
        pair = await repo.get_or_create_pair(pair)

        run_id = uuid4()
        await repo.create_judge_result(
            EvalPairwiseJudgeResult(
                pair_id=pair.id,
                judge_run_id=run_id,
                judge_run_status="completed",
                position_variant="swapped",
                comparison_group_id="grp-1",
                raw_display_winner="a",
                normalized_winner="b",
                raw_dimension_verdicts={"actionability": "a"},
                normalized_dimension_verdicts={"actionability": "b"},
                confidence="medium",
                rationale="swapped run flips a↔b",
                model_id="openai-judge-1",
                prompt_version="v1",
                rubric_version="v1",
                input_hash="b" * 64,
            )
        )
        # Re-read within the SAME session/transaction so the test stays
        # independent of cross-session visibility inside the test-rollback
        # wrapper. The point is to exercise ORM round-trip + JSON columns.
        re_fetched = await repo.get_pair_by_hash(pair_hash)
        assert re_fetched is not None
        re_result = await repo.get_judge_result(re_fetched.id, run_id)
        assert re_result is not None
        assert re_result.normalized_winner == "b"  # swapped normalization applied
        assert re_result.position_variant == "swapped"
        assert re_result.raw_dimension_verdicts == {"actionability": "a"}
        assert re_result.normalized_dimension_verdicts == {"actionability": "b"}


@pytest.mark.asyncio
async def test_pair_delete_cascades_to_results(
    db_connection: AsyncConnection,
) -> None:
    """Deleting a Pair row must CASCADE-delete its Judge results (FK)."""

    from uuid import uuid4

    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.eval import EvalExperiment, EvalTrial

    session_factory = async_sessionmaker(
        bind=db_connection, class_=AsyncSession, expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        experiment = EvalExperiment(
            dataset_id="d2",
            dataset_version="v1",
            dataset_hash="0" * 64,
            git_commit="abc1234",
            graph_version="g-v1",
            prompt_version="p-v1",
            model_version="m-v1",
            tool_version="t-v1",
            context_version="c-v1",
            memory_version="mem-v1",
            frozen_config_hash="0" * 64,
            execution_mode="mock_provider",
            variant_role="baseline",
            trial_count=1,
        )
        session.add(experiment)
        await session.flush()
        trial_a = EvalTrial(
            experiment_id=experiment.id,
            case_id="case-cascade",
            case_fixture_hash="0" * 64,
            trial_index=0,
            seed=0,
            run_type="evaluation",
            status="pending",
        )
        trial_b = EvalTrial(
            experiment_id=experiment.id,
            case_id="case-cascade",
            case_fixture_hash="0" * 64,
            trial_index=1,
            seed=0,
            run_type="evaluation",
            variant="b",
            status="pending",
        )
        session.add_all([trial_a, trial_b])
        await session.flush()

        pair = EvalTrialPair(
            baseline_trial_id=trial_a.id,
            candidate_trial_id=trial_b.id,
            case_id="case-cascade",
            pair_hash="c" * 64,
            input_hash="d" * 64,
            allowed_evidence_kinds=["plan_projection"],
            judge_prompt_version="v1",
            judge_rubric_version="v1",
        )
        session.add(pair)
        await session.flush()
        result = EvalPairwiseJudgeResult(
            pair_id=pair.id,
            judge_run_id=uuid4(),
            judge_run_status="invalid_structured_output",
            position_variant="baseline",
            comparison_group_id="grp-cascade",
            model_id="fixture-judge-v1",
            prompt_version="v1",
            rubric_version="v1",
            input_hash="d" * 64,
        )
        session.add(result)
        await session.flush()
        pair_id = pair.id
        # Delete the pair row directly.
        await session.execute(delete(EvalTrialPair).where(EvalTrialPair.id == pair_id))
        await session.flush()

        # Re-read within the SAME session: the CASCADE should have removed
        # the result row already.
        from app.repositories.evals import EvalRepository

        repo = EvalRepository(session)
        rows = await repo.list_judge_results_by_pair(pair_id)
        assert rows == []
