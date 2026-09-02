"""Create Stage 3 daily reviews and runtime references.

Revision ID: 20260731_0004
Revises: 20260731_0003
Create Date: 2026-07-31 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0004"
down_revision: str | Sequence[str] | None = "20260731_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Create Review facts and connect Stage 2 runtime placeholders."""
    op.create_table(
        "reviews",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", _uuid(), nullable=False),
        sa.Column("plan_id", _uuid(), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("mood", sa.SmallInteger(), nullable=False),
        sa.Column("blockers", sa.String(500)),
        sa.Column("adjustment_request", sa.String(300)),
        sa.Column("free_text", sa.Text()),
        sa.Column("completed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("abandoned_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "suggested_replan",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("replan_reason", sa.String(500)),
        sa.Column("next_plan_run_id", _uuid()),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("mood BETWEEN 1 AND 5", name="ck_reviews_mood"),
        sa.CheckConstraint("completed_count >= 0", name="ck_reviews_completed_count"),
        sa.CheckConstraint("abandoned_count >= 0", name="ck_reviews_abandoned_count"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["next_plan_run_id"],
            ["agent_runs.id"],
            name="fk_reviews_next_plan_run_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("next_plan_run_id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_reviews_user_idempotency"),
        sa.UniqueConstraint(
            "user_id",
            "plan_id",
            "review_date",
            name="uq_reviews_user_plan_date",
        ),
    )
    op.create_index(
        "ix_reviews_user_date_created",
        "reviews",
        ["user_id", "review_date", "created_at"],
    )
    op.create_foreign_key(
        "fk_agent_runs_source_review_id",
        "agent_runs",
        "reviews",
        ["source_review_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_companion_messages_review_id",
        "companion_messages",
        "reviews",
        ["review_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Remove only Stage 3 review resources."""
    op.drop_constraint(
        "fk_companion_messages_review_id",
        "companion_messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_runs_source_review_id",
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_reviews_user_date_created", table_name="reviews")
    op.drop_table("reviews")
