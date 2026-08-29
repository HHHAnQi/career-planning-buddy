"""Persist the immutable source Trial selected by fixture replay."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260818_0021"
down_revision: str | None = "20260817_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_trials",
        sa.Column("fixture_source_trial_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_eval_trials_fixture_source_trial_id",
        "eval_trials",
        "eval_trials",
        ["fixture_source_trial_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_eval_trials_fixture_source",
        "eval_trials",
        ["fixture_source_trial_id"],
        postgresql_where=sa.text("fixture_source_trial_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_eval_trials_fixture_source", table_name="eval_trials")
    op.drop_constraint(
        "fk_eval_trials_fixture_source_trial_id",
        "eval_trials",
        type_="foreignkey",
    )
    op.drop_column("eval_trials", "fixture_source_trial_id")
