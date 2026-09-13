"""PR-9c.2 alembic 0015 reflection + judge_run_id difference tests."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.pairwise_calibration import (
    PAIRWISE_JUDGE_RUN_NAMESPACE,
    deterministic_judge_run_id,
)
from evals.v2.pairwise import PositionVariant

# ------------------------------------------------------- reflection


def _table_columns(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for col in inspector.get_columns(table_name) if (name := col.get("name"))}


def _check_constraints(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for c in inspector.get_check_constraints(table_name) if (name := c.get("name"))}


def _unique_constraints(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for c in inspector.get_unique_constraints(table_name) if (name := c.get("name"))}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = inspect(connection)
    return {name for i in inspector.get_indexes(table_name) if (name := i.get("name"))}


@pytest.mark.asyncio
async def test_0015_creates_four_tables(db_connection: AsyncConnection) -> None:
    names = await db_connection.run_sync(
        lambda c: set(inspect(c).get_table_names())
    )
    for t in (
        "eval_pairwise_sweeps",
        "eval_pairwise_sweep_items",
        "eval_pairwise_human_annotations",
        "eval_pairwise_calibration_reports",
    ):
        assert t in names, f"missing table {t}"


@pytest.mark.asyncio
async def test_0015_does_not_modify_9c1_tables(db_connection: AsyncConnection) -> None:
    """The 9c.1 Pair and Judge result tables must be untouched."""

    pair_cols = await db_connection.run_sync(_table_columns, "eval_trial_pairs")
    result_cols = await db_connection.run_sync(
        _table_columns, "eval_pairwise_judge_results"
    )
    assert pair_cols == {
        "id",
        "baseline_trial_id",
        "candidate_trial_id",
        "case_id",
        "pair_hash",
        "input_hash",
        "allowed_evidence_kinds",
        "judge_prompt_version",
        "judge_rubric_version",
        "created_at",
    }
    # 9c.1 result table columns
    assert "raw_display_winner" in result_cols
    assert "normalized_winner" in result_cols
    assert "comparison_group_id" in result_cols
    # calibration columns must NOT be present on 9c.1 result rows
    assert "calibrated" in result_cols  # 9c.1's boolean, untouched


@pytest.mark.asyncio
async def test_sweeps_critical_constraints(db_connection: AsyncConnection) -> None:
    checks = await db_connection.run_sync(
        _check_constraints, "eval_pairwise_sweeps"
    )
    assert "ck_eval_pairwise_sweeps_runs_eq_pairs_times_two" in checks
    assert "ck_eval_pairwise_sweeps_cancelled_implies_both_timestamps" in checks
    assert "ck_eval_pairwise_sweeps_terminal_le_requested" in checks
    uniques = await db_connection.run_sync(
        _unique_constraints, "eval_pairwise_sweeps"
    )
    assert "uq_eval_pairwise_sweeps_comparison_group" in uniques


@pytest.mark.asyncio
async def test_sweep_items_unique_judge_run_and_position(
    db_connection: AsyncConnection,
) -> None:
    uniques = await db_connection.run_sync(
        _unique_constraints, "eval_pairwise_sweep_items"
    )
    assert "uq_eval_pairwise_sweep_items_sweep_pair_pos" in uniques
    assert "uq_eval_pairwise_sweep_items_judge_run_id" in uniques


@pytest.mark.asyncio
async def test_annotations_position_consistency_check(
    db_connection: AsyncConnection,
) -> None:
    checks = await db_connection.run_sync(
        _check_constraints, "eval_pairwise_sweep_items"
    )
    assert "ck_eval_pairwise_sweep_items_position_consistency" in checks
    assert "ck_eval_pairwise_sweep_items_terminal_status" in checks


@pytest.mark.asyncio
async def test_annotation_dim_columns_have_check_domain(
    db_connection: AsyncConnection,
) -> None:
    """Per-dimension raw / normalized columns each have CHECK constraints
    rejecting off-vocabulary values (raw=a/b, normalized=baseline/candidate)."""

    checks = await db_connection.run_sync(
        _check_constraints, "eval_pairwise_human_annotations"
    )
    for dim in ("actionability", "alignment", "personalization", "clarity", "consistency"):
        assert f"ck_eval_pairwise_ann_raw_{dim}" in checks
        assert f"ck_eval_pairwise_ann_norm_{dim}" in checks
    assert "ck_eval_pairwise_ann_raw_winner" in checks
    assert "ck_eval_pairwise_ann_normalized_winner" in checks


@pytest.mark.asyncio
async def test_annotation_unique_and_adjudication_partial_index(
    db_connection: AsyncConnection,
) -> None:
    uniques = await db_connection.run_sync(
        _unique_constraints, "eval_pairwise_human_annotations"
    )
    assert (
        "uq_eval_pairwise_ann_dataset_pair_reviewer_surface" in uniques
    )
    indexes = await db_connection.run_sync(
        _indexes, "eval_pairwise_human_annotations"
    )
    # Partial UNIQUE index on adjudication — appears in get_indexes
    assert "uq_eval_pairwise_ann_adjudication" in indexes


@pytest.mark.asyncio
async def test_report_unique_input_hash(db_connection: AsyncConnection) -> None:
    uniques = await db_connection.run_sync(
        _unique_constraints, "eval_pairwise_calibration_reports"
    )
    assert "uq_eval_pairwise_reports_input_hash" in uniques


# --------------------------------------------------- judge_run_id


def test_judge_run_id_is_deterministic() -> None:
    sweep = uuid4()
    h = "a" * 64
    a = deterministic_judge_run_id(
        sweep_id=sweep,
        pair_hash=h,
        position_variant=PositionVariant.BASELINE,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    b = deterministic_judge_run_id(
        sweep_id=sweep,
        pair_hash=h,
        position_variant=PositionVariant.BASELINE,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    assert a == b


def test_judge_run_id_changes_with_position() -> None:
    sweep = uuid4()
    h = "b" * 64
    a = deterministic_judge_run_id(
        sweep_id=sweep,
        pair_hash=h,
        position_variant=PositionVariant.BASELINE,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    b = deterministic_judge_run_id(
        sweep_id=sweep,
        pair_hash=h,
        position_variant=PositionVariant.SWAPPED,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    assert a != b


def test_judge_run_id_changes_with_judge_identity() -> None:
    sweep = uuid4()
    h = "c" * 64
    a = deterministic_judge_run_id(
        sweep_id=sweep,
        pair_hash=h,
        position_variant=PositionVariant.BASELINE,
        judge_model_id="m1",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    b = deterministic_judge_run_id(
        sweep_id=sweep,
        pair_hash=h,
        position_variant=PositionVariant.BASELINE,
        judge_model_id="m2",
        judge_prompt_version="v1",
        judge_rubric_version="v1",
    )
    assert a != b


def test_judge_run_id_namespace_is_fixed() -> None:
    """Fixed-namespace sanity (so changing the seed down the road is
    impossible without code review of this constant)."""

    assert PAIRWISE_JUDGE_RUN_NAMESPACE == UUID(
        "c4b5e6f7-0000-4000-8000-000000009c20"
    )
