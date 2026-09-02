"""Add bounded profile periods and versioned Review CRUD fields."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0028"
down_revision: str | None = "20260824_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("start_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_user_profiles_period",
        "user_profiles",
        "start_date IS NULL OR deadline IS NULL OR start_date <= deadline",
    )
    op.add_column(
        "reviews", sa.Column("version", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "reviews",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_check_constraint("ck_reviews_version", "reviews", "version >= 1")


def downgrade() -> None:
    op.drop_constraint("ck_reviews_version", "reviews", type_="check")
    op.drop_column("reviews", "updated_at")
    op.drop_column("reviews", "version")
    op.drop_constraint("ck_user_profiles_period", "user_profiles", type_="check")
    op.drop_column("user_profiles", "start_date")
