"""PR-9c.2 Calibration persistence (Alembic 0015).

Adds four tables that wrap PR-9c.1's two Pair/Judge tables with a
calibration workflow layer::

    eval_pairwise_sweeps            -- Sweep control plane
    eval_pairwise_sweep_items       -- Frozen per-(pair, position) work items
    eval_pairwise_human_annotations -- Reviewer annotations (raw + normalized)
    eval_pairwise_calibration_reports -- Versioned calibration report snapshots

Strict invariants locked by the user's Commit-2 supplementary constraints:

1. Sweep counters are pair-count vs judge-run-count separated.
2. ``judge_run_id`` on ``eval_pairwise_sweep_items`` is a deterministic
   uuid5(sweep_id|pair_hash|position_variant|judge identity).
3. SweepItem rows ARE the recovery work list.
4. Annotation idempotency uses
   ``UNIQUE(dataset_id, pair_id, reviewer_id, review_input_hash)`` +
   a separate ``submission_hash`` to distinguish 200-exists from
   409-payload-conflict. No ``ON CONFLICT DO NOTHING``.
5. Annotation ``raw`` vocabulary is ``a/b/tie/both_unacceptable``;
   ``normalized`` vocabulary is ``baseline/candidate/tie/both_unacceptable``.
6. Primary/adjudication contract enforced in service via ``SELECT pair
   FOR UPDATE``; DB provides belt-and-braces partial unique indexes.
7. ``eval_pairwise_calibration_reports`` uses ``UNIQUE(input_hash)``; same
   input + different content raises an integrity error (no second row).
8. ``cancel_requested_at`` lives on the Sweep row as a staging fact.
   ``cancelled`` terminal status is set ONLY by the Executor (Commit 3).

The migration is independently revertible. None of PR-9c.1's tables are
touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260815_0015"
down_revision: str | None = "20260812_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Five PR-9c.1 dimensions stored as explicit columns on the annotations
# table (supplementary constraint #5 — Postgres does not allow subqueries
# inside CHECK constraints, so we cannot enforce JSONB integrity at the DB
# layer; explicit columns give us CHECK-level guarantees without
# compromising).


def upgrade() -> None:
    # ---------------------------- eval_pairwise_sweeps --------------------
    op.create_table(
        "eval_pairwise_sweeps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("export_revision", sa.String(length=64), nullable=False),
        sa.Column(
            "baseline_experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("judge_model_id", sa.String(length=128), nullable=False),
        sa.Column("judge_prompt_version", sa.String(length=32), nullable=False),
        sa.Column("judge_rubric_version", sa.String(length=32), nullable=False),
        sa.Column("annotation_schema_version", sa.String(length=32), nullable=False),
        sa.Column("comparison_group_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_pair_count", sa.Integer, nullable=False),
        sa.Column("requested_judge_run_count", sa.Integer, nullable=False),
        sa.Column(
            "completed_judge_run_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "failed_judge_run_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completed_pair_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "position_pair_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_summary", postgresql.JSONB, nullable=True),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_eval_pairwise_sweeps_status",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweeps_source_sha",
        ),
        sa.CheckConstraint(
            "requested_pair_count > 0",
            name="ck_eval_pairwise_sweeps_requested_pair_positive",
        ),
        sa.CheckConstraint(
            "requested_judge_run_count = requested_pair_count * 2",
            name="ck_eval_pairwise_sweeps_runs_eq_pairs_times_two",
        ),
        sa.CheckConstraint(
            "completed_judge_run_count + failed_judge_run_count "
            "<= requested_judge_run_count",
            name="ck_eval_pairwise_sweeps_terminal_le_requested",
        ),
        sa.CheckConstraint(
            "completed_pair_count <= requested_pair_count",
            name="ck_eval_pairwise_sweeps_pairs_le_requested",
        ),
        sa.CheckConstraint(
            "position_pair_count <= completed_pair_count",
            name="ck_eval_pairwise_sweeps_position_le_completed_pairs",
        ),
        sa.CheckConstraint(
            "comparison_group_id <> ''",
            name="ck_eval_pairwise_sweeps_group_nonempty",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancel_requested_at IS NOT NULL AND "
            "terminal_at IS NOT NULL)",
            name="ck_eval_pairwise_sweeps_cancelled_implies_both_timestamps",
        ),
        sa.UniqueConstraint(
            "comparison_group_id",
            name="uq_eval_pairwise_sweeps_comparison_group",
        ),
    )
    op.create_index(
        "ix_eval_pairwise_sweeps_dataset",
        "eval_pairwise_sweeps",
        ["dataset_id", "dataset_version"],
    )
    op.create_index(
        "ix_eval_pairwise_sweeps_status",
        "eval_pairwise_sweeps",
        ["status"],
    )
    op.create_index(
        "ix_eval_pairwise_sweeps_experiments",
        "eval_pairwise_sweeps",
        ["baseline_experiment_id", "candidate_experiment_id"],
    )

    # ------------------------- eval_pairwise_sweep_items ------------------
    op.create_table(
        "eval_pairwise_sweep_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sweep_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_pairwise_sweeps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pair_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_trial_pairs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position_variant", sa.String(length=16), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("pair_hash", sa.String(length=64), nullable=False),
        sa.Column("baseline_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("baseline_output_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_output_hash", sa.String(length=64), nullable=False),
        sa.Column("display_a_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_b_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "frozen_review_surface_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("judge_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "judge_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "eval_pairwise_judge_results.id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "position_variant IN ('baseline','swapped')",
            name="ck_eval_pairwise_sweep_items_position",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_eval_pairwise_sweep_items_status",
        ),
        sa.CheckConstraint(
            "pair_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_pair_hash",
        ),
        sa.CheckConstraint(
            "baseline_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_baseline_sha",
        ),
        sa.CheckConstraint(
            "candidate_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_candidate_sha",
        ),
        sa.CheckConstraint(
            "frozen_review_surface_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_sweep_items_review_surface_sha",
        ),
        sa.CheckConstraint(
            "(position_variant = 'baseline' AND "
            "display_a_trial_id = baseline_trial_id AND "
            "display_b_trial_id = candidate_trial_id) OR "
            "(position_variant = 'swapped' AND "
            "display_a_trial_id = candidate_trial_id AND "
            "display_b_trial_id = baseline_trial_id)",
            name="ck_eval_pairwise_sweep_items_position_consistency",
        ),
        sa.CheckConstraint(
            "(status IN ('completed','failed','cancelled')) = "
            "(terminal_at IS NOT NULL)",
            name="ck_eval_pairwise_sweep_items_terminal_status",
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (judge_result_id IS NOT NULL)",
            name="ck_eval_pairwise_sweep_items_completed_requires_result",
        ),
        sa.UniqueConstraint(
            "sweep_id",
            "pair_id",
            "position_variant",
            name="uq_eval_pairwise_sweep_items_sweep_pair_pos",
        ),
        sa.UniqueConstraint(
            "judge_run_id",
            name="uq_eval_pairwise_sweep_items_judge_run_id",
        ),
    )
    op.create_index(
        "ix_eval_pairwise_sweep_items_sweep_status",
        "eval_pairwise_sweep_items",
        ["sweep_id", "status"],
    )
    op.create_index(
        "ix_eval_pairwise_sweep_items_pair_pos",
        "eval_pairwise_sweep_items",
        ["pair_id", "position_variant"],
    )

    # --------------------- eval_pairwise_human_annotations ---------------
    op.create_table(
        "eval_pairwise_human_annotations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=False),
        sa.Column(
            "sweep_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_pairwise_sweeps.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "pair_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_trial_pairs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reviewer_id", sa.String(length=128), nullable=False),
        sa.Column("reviewer_role", sa.String(length=16), nullable=False),
        sa.Column(
            "is_adjudication",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("annotation_schema_version", sa.String(length=32), nullable=False),
        sa.Column("rubric_version", sa.String(length=32), nullable=False),
        sa.Column("judge_prompt_version", sa.String(length=32), nullable=False),
        sa.Column("judge_model_id", sa.String(length=128), nullable=False),
        sa.Column(
            "frozen_review_surface_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("position_variant", sa.String(length=16), nullable=False),
        sa.Column("display_a_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_b_trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_winner", sa.String(length=32), nullable=False),
        sa.Column(
            "raw_dim_actionability",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "raw_dim_alignment",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "raw_dim_personalization",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("raw_dim_clarity", sa.String(length=32), nullable=False),
        sa.Column(
            "raw_dim_consistency",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("normalized_winner", sa.String(length=32), nullable=False),
        sa.Column(
            "norm_dim_actionability",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "norm_dim_alignment",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "norm_dim_personalization",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("norm_dim_clarity", sa.String(length=32), nullable=False),
        sa.Column(
            "norm_dim_consistency",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("review_input_hash", sa.String(length=64), nullable=False),
        sa.Column("submission_hash", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "reviewer_role IN ('primary','adjudicator')",
            name="ck_eval_pairwise_ann_role",
        ),
        sa.CheckConstraint(
            "(reviewer_role = 'adjudicator') = is_adjudication",
            name="ck_eval_pairwise_ann_role_adjudication_eq",
        ),
        sa.CheckConstraint(
            "position_variant IN ('baseline','swapped')",
            name="ck_eval_pairwise_ann_position",
        ),
        sa.CheckConstraint(
            "raw_winner IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_winner",
        ),
        sa.CheckConstraint(
            "normalized_winner IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_normalized_winner",
        ),
        # Per dimension: raw in a/b vocabulary, normalized in baseline/candidate
        # vocabulary. Postgres cannot put subqueries inside CHECK, so we use
        # explicit columns instead of JSONB dicts (supplementary constraint #5).
        sa.CheckConstraint(
            "raw_dim_actionability IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_actionability",
        ),
        sa.CheckConstraint(
            "raw_dim_alignment IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_alignment",
        ),
        sa.CheckConstraint(
            "raw_dim_personalization IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_personalization",
        ),
        sa.CheckConstraint(
            "raw_dim_clarity IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_clarity",
        ),
        sa.CheckConstraint(
            "raw_dim_consistency IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_raw_consistency",
        ),
        sa.CheckConstraint(
            "norm_dim_actionability IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_actionability",
        ),
        sa.CheckConstraint(
            "norm_dim_alignment IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_alignment",
        ),
        sa.CheckConstraint(
            "norm_dim_personalization IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_personalization",
        ),
        sa.CheckConstraint(
            "norm_dim_clarity IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_clarity",
        ),
        sa.CheckConstraint(
            "norm_dim_consistency IN "
            "('baseline','candidate','tie','both_unacceptable')",
            name="ck_eval_pairwise_ann_norm_consistency",
        ),
        sa.CheckConstraint(
            "review_input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_ann_review_input_sha",
        ),
        sa.CheckConstraint(
            "submission_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_ann_submission_sha",
        ),
        sa.CheckConstraint(
            "frozen_review_surface_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_ann_review_surface_sha",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "pair_id",
            "reviewer_id",
            "review_input_hash",
            name="uq_eval_pairwise_ann_dataset_pair_reviewer_surface",
        ),
    )
    op.create_index(
        "ix_eval_pairwise_ann_pair",
        "eval_pairwise_human_annotations",
        ["pair_id"],
    )
    op.create_index(
        "ix_eval_pairwise_ann_sweep",
        "eval_pairwise_human_annotations",
        ["sweep_id"],
    )
    op.create_index(
        "ix_eval_pairwise_ann_reviewer",
        "eval_pairwise_human_annotations",
        ["reviewer_id"],
    )
    op.create_index(
        "ix_eval_pairwise_ann_dataset",
        "eval_pairwise_human_annotations",
        ["dataset_id", "dataset_version"],
    )
    op.create_index(
        "uq_eval_pairwise_ann_adjudication",
        "eval_pairwise_human_annotations",
        ["pair_id", "review_input_hash"],
        unique=True,
        postgresql_where=sa.text("is_adjudication = TRUE"),
    )

    # --------------------- eval_pairwise_calibration_reports --------------
    op.create_table(
        "eval_pairwise_calibration_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("judge_model_id", sa.String(length=128), nullable=False),
        sa.Column("judge_prompt_version", sa.String(length=32), nullable=False),
        sa.Column("judge_rubric_version", sa.String(length=32), nullable=False),
        sa.Column("annotation_schema_version", sa.String(length=32), nullable=False),
        sa.Column("calibration_policy_version", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("report_payload", postgresql.JSONB, nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_reports_source_sha",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_reports_input_sha",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_reports_content_sha",
        ),
        sa.CheckConstraint(
            "dataset_version <> ''",
            name="ck_eval_pairwise_reports_version_nonempty",
        ),
        sa.UniqueConstraint(
            "input_hash",
            name="uq_eval_pairwise_reports_input_hash",
        ),
    )
    op.create_index(
        "ix_eval_pairwise_reports_dataset_created",
        "eval_pairwise_calibration_reports",
        ["dataset_id", "dataset_version", "created_at"],
    )
    op.create_index(
        "ix_eval_pairwise_reports_judge",
        "eval_pairwise_calibration_reports",
        ["judge_model_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_eval_pairwise_reports_judge",
        table_name="eval_pairwise_calibration_reports",
    )
    op.drop_index(
        "ix_eval_pairwise_reports_dataset_created",
        table_name="eval_pairwise_calibration_reports",
    )
    op.drop_table("eval_pairwise_calibration_reports")

    op.drop_index(
        "uq_eval_pairwise_ann_adjudication",
        table_name="eval_pairwise_human_annotations",
    )
    op.drop_index(
        "ix_eval_pairwise_ann_dataset",
        table_name="eval_pairwise_human_annotations",
    )
    op.drop_index(
        "ix_eval_pairwise_ann_reviewer",
        table_name="eval_pairwise_human_annotations",
    )
    op.drop_index(
        "ix_eval_pairwise_ann_sweep",
        table_name="eval_pairwise_human_annotations",
    )
    op.drop_index(
        "ix_eval_pairwise_ann_pair",
        table_name="eval_pairwise_human_annotations",
    )
    op.drop_table("eval_pairwise_human_annotations")

    op.drop_index(
        "ix_eval_pairwise_sweep_items_pair_pos",
        table_name="eval_pairwise_sweep_items",
    )
    op.drop_index(
        "ix_eval_pairwise_sweep_items_sweep_status",
        table_name="eval_pairwise_sweep_items",
    )
    op.drop_table("eval_pairwise_sweep_items")

    op.drop_index(
        "ix_eval_pairwise_sweeps_experiments",
        table_name="eval_pairwise_sweeps",
    )
    op.drop_index(
        "ix_eval_pairwise_sweeps_status",
        table_name="eval_pairwise_sweeps",
    )
    op.drop_index(
        "ix_eval_pairwise_sweeps_dataset",
        table_name="eval_pairwise_sweeps",
    )
    op.drop_table("eval_pairwise_sweeps")
