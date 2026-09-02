"""Add an explicit task verification lifecycle."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260827_0030"
down_revision: str | None = "20260826_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "verification_status",
            sa.String(length=16),
            server_default="not_ready",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE tasks SET deliverable_verified = true, verification_status = 'passed' "
        "WHERE state = 'completed' OR deliverable_verified = true"
    )
    op.create_check_constraint(
        "ck_tasks_verification_status",
        "tasks",
        "verification_status IN ('not_ready','ready','failed','passed')",
    )
    op.create_check_constraint(
        "ck_tasks_verification_consistency",
        "tasks",
        "(verification_status = 'passed') = deliverable_verified",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_verification_consistency", "tasks", type_="check")
    op.drop_constraint("ck_tasks_verification_status", "tasks", type_="check")
    op.drop_column("tasks", "verification_status")
