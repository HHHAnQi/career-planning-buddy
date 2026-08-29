"""Add auditable task adjustment proposals for fixed weekly cycles."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260824_0027"
down_revision: str | None = "20260823_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_adjustment_proposals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("request_text", sa.String(1000), nullable=False),
        sa.Column("original_task_json", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_patch_json", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.String(500), nullable=False),
        sa.Column("generation_method", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(128)),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','applied','rejected')",
            name="ck_task_adjustments_status",
        ),
        sa.CheckConstraint(
            "generation_method IN ('manual','rule','model','rule_fallback')",
            name="ck_task_adjustments_generation_method",
        ),
        sa.CheckConstraint("version >= 1", name="ck_task_adjustments_version"),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_task_adjustments_user_idempotency"
        ),
    )
    op.create_index(
        "ix_task_adjustments_task_created",
        "task_adjustment_proposals",
        ["task_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_adjustments_task_created", table_name="task_adjustment_proposals")
    op.drop_table("task_adjustment_proposals")
