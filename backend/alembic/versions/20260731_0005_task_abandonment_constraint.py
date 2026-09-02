"""Constrain Stage 3 Task abandonment reasons.

Revision ID: 20260731_0005
Revises: 20260731_0004
Create Date: 2026-07-31 03:10:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260731_0005"
down_revision: str | Sequence[str] | None = "20260731_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enforce the Task abandonment-reason contract at the database layer."""
    op.create_check_constraint(
        "ck_tasks_abandoned_reason",
        "tasks",
        "abandoned_reason IS NULL OR abandoned_reason IN "
        "('too_hard','too_easy','no_time','lost_interest','blocked','other')",
    )
    op.create_check_constraint(
        "ck_tasks_state_fields",
        "tasks",
        "(state = 'completed' AND actual_minutes IS NOT NULL "
        "AND abandoned_reason IS NULL AND abandoned_reason_text IS NULL) OR "
        "(state = 'abandoned' AND actual_minutes IS NULL "
        "AND abandoned_reason IS NOT NULL AND "
        "((abandoned_reason = 'other' "
        "AND NULLIF(BTRIM(abandoned_reason_text), '') IS NOT NULL) OR "
        "(abandoned_reason <> 'other' AND abandoned_reason_text IS NULL))) OR "
        "(state NOT IN ('completed','abandoned') AND actual_minutes IS NULL "
        "AND abandoned_reason IS NULL AND abandoned_reason_text IS NULL)",
    )


def downgrade() -> None:
    """Remove only the Stage 3 Task abandonment-reason constraint."""
    op.drop_constraint("ck_tasks_state_fields", "tasks", type_="check")
    op.drop_constraint("ck_tasks_abandoned_reason", "tasks", type_="check")
