"""PR-9c.1 Pairwise Judge persistence: Pairs + Judge results (two-table design).

Revision ID: 20260812_0014
Revises: 20260810_0013

Introduces the two-table Pairwise Judge schema mandated by the PR-9c.1 plan:

* ``eval_trial_pairs`` — the STABLE IDENTITY of one baseline/candidate
  comparison for one case. ``pair_hash`` (canonical_sha256 over
  schema_version + case_id + role-determined trial refs + baseline/
  candidate output hashes) is UNIQUE, so re-running a Judge — even with a
  new prompt / model / ``comparison_group_id`` — re-uses the same Pair
  row when the underlying output bytes have not changed.
  ``comparison_group_id`` is deliberately NOT in ``pair_hash`` and NOT a
  column on this table: it identifies one original+swapped run PAIR, not
  the Pair entity. The partial unique index on
  (baseline_trial_id, candidate_trial_id) enforces "one Pair per ordered
  trial tuple with equivalent output bytes" — if a re-collect moves the
  output, the new pair_hash differs, the unique index catches the
  collision-free insertion, and the new Pair row sits alongside the old
  one so calibration history stays attributable.

* ``eval_pairwise_judge_results`` — ONE ROW PER PHYSICAL JUDGE EXECUTION.
  ``comparison_group_id`` lives HERE: it ties together one original +
  swapped run pair. Position bias / consistency analysis relies on
  multiple runs per Pair, so the unique key is ``(pair_id, judge_run_id)``
  rather than pair_id alone. Persists both ``raw_display_winner``
  (display-side a/b/tie/...) and ``normalized_winner`` (baseline-relative)
  plus ``position_variant`` ('baseline'|'swapped') so analysts can audit
  position bias without re-running. ``judge_run_status`` distinguishes a
  completed run from ``invalid_structured_output``. Winner values are
  constrained to {a,b,tie,both_unacceptable}; ``invalid`` is NOT a
  winner — invalidity lives in ``judge_run_status``.

Both tables are independently revertible: downgrade drops judge results
first (FK pair_id → eval_trial_pairs.ondelete CASCADE) then pairs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260812_0014"
down_revision: str | None = "20260810_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_trial_pairs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "baseline_trial_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_trials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_trial_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_trials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("pair_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "allowed_evidence_kinds",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("judge_prompt_version", sa.String(length=32), nullable=False),
        sa.Column("judge_rubric_version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "baseline_trial_id <> candidate_trial_id",
            name="ck_eval_trial_pairs_distinct_trials",
        ),
        sa.CheckConstraint(
            "pair_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_trial_pairs_pair_hash",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_trial_pairs_input_hash",
        ),
        sa.CheckConstraint(
            "case_id <> ''",
            name="ck_eval_trial_pairs_nonempty_keys",
        ),
        sa.UniqueConstraint(
            "pair_hash",
            name="uq_eval_trial_pairs_pair_hash",
        ),
        # NON-UNIQUE composite index on the ordered (baseline, candidate)
        # trial tuple. The Pair's stable identity is ``pair_hash`` (which
        # is content-aware). The trial-tuple alone is NOT unique because
        # a re-collect that moves output bytes is allowed to create a
        # SECOND Pair row with the SAME trial ids but a DIFFERENT
        # pair_hash — the old row stays attributable to the old bytes,
        # the new row attributes the new bytes. This index only
        # accelerates lookup-by-trial-tuple.
        sa.Index(
            "ix_eval_trial_pairs_trial_ids",
            "baseline_trial_id",
            "candidate_trial_id",
        ),
    )
    op.create_index(
        index_name="ix_eval_trial_pairs_case",
        table_name="eval_trial_pairs",
        columns=["case_id"],
    )

    op.create_table(
        "eval_pairwise_judge_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pair_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_trial_pairs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("judge_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "judge_run_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "position_variant",
            sa.String(length=16),
            nullable=False,
        ),
        # comparison_group_id lives on Result rows, not on Pair rows: it
        # identifies one original+swapped run pair and bumps per
        # re-evaluation. The Pair row stays stable across groups so
        # historical Runs aggregate cleanly under one Pair identity.
        sa.Column(
            "comparison_group_id", sa.String(length=128), nullable=False,
        ),
        # Display-side winner is the raw LLM verdict; normalized is
        # baseline-relative (flipped when position_variant='swapped').
        # NULL only when judge_run_status='invalid_structured_output'.
        sa.Column("raw_display_winner", sa.String(length=32), nullable=True),
        sa.Column("normalized_winner", sa.String(length=32), nullable=True),
        sa.Column(
            "raw_dimension_verdicts",
            postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "normalized_dimension_verdicts",
            postgresql.JSONB,
            nullable=True,
        ),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column(
            "prompt_version",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "rubric_version",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "raw_output_hash",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "tokens_in",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tokens_out",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "calibrated",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "judge_run_status IN ('completed','invalid_structured_output')",
            name="ck_eval_pairwise_judge_results_status",
        ),
        sa.CheckConstraint(
            "position_variant IN ('baseline','swapped')",
            name="ck_eval_pairwise_judge_results_position_variant",
        ),
        sa.CheckConstraint(
            "raw_display_winner IS NULL OR "
            "raw_display_winner IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_judge_results_raw_winner",
        ),
        sa.CheckConstraint(
            "normalized_winner IS NULL OR "
            "normalized_winner IN ('a','b','tie','both_unacceptable')",
            name="ck_eval_pairwise_judge_results_normalized_winner",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low','medium','high')",
            name="ck_eval_pairwise_judge_results_confidence",
        ),
        sa.CheckConstraint(
            "(judge_run_status='completed') = (raw_display_winner IS NOT NULL "
            "AND normalized_winner IS NOT NULL)",
            name="ck_eval_pairwise_judge_results_completed_carries_verdict",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_pairwise_judge_results_input_hash",
        ),
        sa.CheckConstraint(
            "comparison_group_id <> ''",
            name="ck_eval_pairwise_judge_results_group",
        ),
        sa.UniqueConstraint(
            "pair_id",
            "judge_run_id",
            name="uq_eval_pairwise_judge_results_pair_run",
        ),
    )
    op.create_index(
        index_name="ix_eval_pairwise_judge_results_pair",
        table_name="eval_pairwise_judge_results",
        columns=["pair_id"],
    )
    op.create_index(
        index_name="ix_eval_pairwise_judge_results_run",
        table_name="eval_pairwise_judge_results",
        columns=["judge_run_id"],
    )
    op.create_index(
        index_name="ix_eval_pairwise_judge_results_group",
        table_name="eval_pairwise_judge_results",
        columns=["comparison_group_id"],
    )


def downgrade() -> None:
    op.drop_index(
        index_name="ix_eval_pairwise_judge_results_group",
        table_name="eval_pairwise_judge_results",
    )
    op.drop_index(
        index_name="ix_eval_pairwise_judge_results_run",
        table_name="eval_pairwise_judge_results",
    )
    op.drop_index(
        index_name="ix_eval_pairwise_judge_results_pair",
        table_name="eval_pairwise_judge_results",
    )
    op.drop_table("eval_pairwise_judge_results")
    op.drop_index(
        index_name="ix_eval_trial_pairs_case",
        table_name="eval_trial_pairs",
    )
    op.drop_index(
        index_name="ix_eval_trial_pairs_trial_ids",
        table_name="eval_trial_pairs",
    )
    op.drop_table("eval_trial_pairs")
