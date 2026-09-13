"""Persist per-step execution progress and deliverable verification."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260826_0029"
down_revision: str | None = "20260825_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "completed_step_indexes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "deliverable_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_completed_step_indexes_array",
        "tasks",
        "jsonb_typeof(completed_step_indexes_json) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_completed_step_indexes_array", "tasks", type_="check")
    op.drop_column("tasks", "deliverable_verified")
    op.drop_column("tasks", "completed_step_indexes_json")
