"""Stage 6B source provenance and reviewed semantic knowledge.

Revision ID: 20260804_0007
Revises: 20260731_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260804_0007"
down_revision: str | None = "20260731_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_sources", sa.Column("canonical_url", sa.Text(), nullable=True))
    op.add_column("search_sources", sa.Column("url_hash", sa.String(64), nullable=True))
    op.add_column("search_sources", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("search_sources", sa.Column("provider_request_id", sa.String(200)))
    op.add_column("search_sources", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE search_sources SET canonical_url=url")
    op.execute("UPDATE search_sources SET url_hash=md5(url) || md5(url)")
    op.execute("UPDATE search_sources SET content_hash=md5(snippet) || md5(snippet)")
    op.alter_column("search_sources", "canonical_url", nullable=False)
    op.alter_column("search_sources", "url_hash", nullable=False)
    op.alter_column("search_sources", "content_hash", nullable=False)
    op.drop_constraint("uq_search_sources_run_url", "search_sources", type_="unique")
    op.create_unique_constraint(
        "uq_search_sources_run_url_hash", "search_sources", ["run_id", "url_hash"]
    )
    op.create_table(
        "experience_atom_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("goal_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.String(300), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_excerpt", sa.String(300), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column(
            "proposed_by_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "approved_atom_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experience_atoms.id", ondelete="SET NULL"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="ck_experience_atom_candidates_status",
        ),
        sa.UniqueConstraint("content_hash", name="uq_experience_atom_candidates_content_hash"),
    )
    op.create_index(
        "ix_experience_atom_candidates_status_created",
        "experience_atom_candidates",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("experience_atom_candidates")
    op.drop_constraint("uq_search_sources_run_url_hash", "search_sources", type_="unique")
    op.create_unique_constraint("uq_search_sources_run_url", "search_sources", ["run_id", "url"])
    for column in (
        "published_at",
        "provider_request_id",
        "content_hash",
        "url_hash",
        "canonical_url",
    ):
        op.drop_column("search_sources", column)
