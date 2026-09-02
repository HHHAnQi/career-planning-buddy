"""Add variant + counterfactual_group_id to eval_trials (PR-8).

Revision ID: 20260808_0011
Revises: 20260807_0010

Widens ``uq_eval_trials_case_index`` so paired variants of one case can
coexist inside a single experiment: the new uniqueness key includes
``COALESCE(variant, '')``. Pre-PR-8 trials all have NULL ``variant`` so the
COALESCE form preserves the old behaviour for them.

Also adds a partial index on ``counterfactual_group_id`` for paired-diff
lookups (NULL columns are excluded from the partial index).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260808_0011"
down_revision: str | None = "20260807_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_trials",
        sa.Column("variant", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "eval_trials",
        sa.Column(
            "counterfactual_group_id", sa.String(length=64), nullable=True
        ),
    )
    # Drop the legacy table-level unique constraint and replace it with two
    # partial unique indexes (one for NULL variant = legacy Trials, one for
    # non-NULL variant = paired counterfactual Trials). UniqueConstraint
    # does not accept postgresql_where, hence the Index form.
    op.drop_constraint(
        "uq_eval_trials_case_index", table_name="eval_trials", type_="unique"
    )
    op.create_index(
        index_name="uq_eval_trials_case_index",
        table_name="eval_trials",
        columns=["experiment_id", "case_id", "trial_index"],
        unique=True,
        postgresql_where=sa.text("variant IS NULL"),
    )
    op.create_index(
        index_name="uq_eval_trials_case_index_variant",
        table_name="eval_trials",
        columns=["experiment_id", "case_id", "variant", "trial_index"],
        unique=True,
        postgresql_where=sa.text("variant IS NOT NULL"),
    )
    op.create_index(
        index_name="ix_eval_trials_group",
        table_name="eval_trials",
        columns=["counterfactual_group_id"],
        postgresql_where=sa.text("counterfactual_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        index_name="ix_eval_trials_group", table_name="eval_trials"
    )
    op.drop_index(
        index_name="uq_eval_trials_case_index_variant", table_name="eval_trials"
    )
    op.drop_index(
        index_name="uq_eval_trials_case_index", table_name="eval_trials"
    )
    op.create_unique_constraint(
        constraint_name="uq_eval_trials_case_index",
        table_name="eval_trials",
        columns=["experiment_id", "case_id", "trial_index"],
    )
    op.drop_column("eval_trials", "counterfactual_group_id")
    op.drop_column("eval_trials", "variant")
