"""Persist lossless provider response payloads for immutable fixture replay."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260817_0020"
down_revision: str | None = "20260816_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_provider_fixture_items",
        sa.Column(
            "response_payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("eval_provider_fixture_items", "response_payload_json")
