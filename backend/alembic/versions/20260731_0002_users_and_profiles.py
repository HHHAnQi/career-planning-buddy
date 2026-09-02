"""Create users and user_profiles.

Revision ID: 20260731_0002
Revises: 20260731_0001
Create Date: 2026-07-31 00:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0002"
down_revision: str | Sequence[str] | None = "20260731_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Stage 1 identity and profile tables."""
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("auth_type", sa.String(length=16), server_default="guest", nullable=False),
        sa.Column("guest_device_hash", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_type IN ('guest', 'email', 'github')",
            name="ck_users_auth_type",
        ),
        sa.CheckConstraint("role IN ('user', 'dev')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_users_guest_device_hash",
        "users",
        ["guest_device_hash"],
        unique=True,
        postgresql_where=sa.text("guest_device_hash IS NOT NULL"),
    )
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )

    op.create_table(
        "user_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goal_type", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("time_budget_minutes", sa.Integer(), nullable=False),
        sa.Column("skill_level", sa.String(length=16), nullable=False),
        sa.Column("skill_summary", sa.Text(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "goal_type IN "
            "('ai_backend', 'agent_app', 'backend_java', 'data_engineer', 'fullstack', 'other')",
            name="ck_user_profiles_goal_type",
        ),
        sa.CheckConstraint(
            "stage IN ('exploring', 'preparing', 'applying', 'interviewing')",
            name="ck_user_profiles_stage",
        ),
        sa.CheckConstraint(
            "time_budget_minutes BETWEEN 15 AND 480",
            name="ck_user_profiles_time_budget",
        ),
        sa.CheckConstraint(
            "skill_level IN ('beginner', 'intermediate', 'advanced')",
            name="ck_user_profiles_skill_level",
        ),
        sa.CheckConstraint("version >= 1", name="ck_user_profiles_version"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Remove only the Stage 1 profile and identity tables."""
    op.drop_table("user_profiles")
    op.drop_index("uq_users_email", table_name="users")
    op.drop_index("uq_users_guest_device_hash", table_name="users")
    op.drop_table("users")
