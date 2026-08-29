"""Create the empty Stage 0 migration baseline.

Revision ID: 20260731_0001
Revises:
Create Date: 2026-07-31 00:00:00
"""

from collections.abc import Sequence

revision: str = "20260731_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep Stage 0 free of business tables."""


def downgrade() -> None:
    """The empty baseline has no schema objects to remove."""
