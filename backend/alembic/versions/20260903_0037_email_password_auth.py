"""Add password hash for email authentication.

Revision ID: 20260903_0037
Revises: 20260902_0036
Create Date: 2026-09-03 00:37:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0037"
down_revision: str | Sequence[str] | None = "20260902_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist password verifier material for email users."""
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_users_email_auth_has_password",
        "users",
        "(auth_type != 'email') OR (email IS NOT NULL AND password_hash IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove email password verifier material."""
    op.drop_constraint("ck_users_email_auth_has_password", "users", type_="check")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_column("users", "password_hash")
