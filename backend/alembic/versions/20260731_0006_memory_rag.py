"""Add Stage 4 memory, search, and pgvector evidence tables.

Revision ID: 20260731_0006
Revises: 20260731_0005
Create Date: 2026-07-31 19:20:00
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0006"
down_revision: str | Sequence[str] | None = "20260731_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Stage 4 evidence baseline with a locked 1024 dimension."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensitivity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1024), nullable=True),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
            "memory_type IN ('profile_fact','stable_preference','execution_pattern')",
            name="ck_memories_type",
        ),
        sa.CheckConstraint("sensitivity IN ('normal','sensitive')", name="ck_memories_sensitivity"),
        sa.CheckConstraint("status IN ('active','closed')", name="ck_memories_status"),
        sa.CheckConstraint("version >= 1", name="ck_memories_version"),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memories_user_status_type", "memories", ["user_id", "status", "memory_type"]
    )
    op.create_index(
        "ix_memories_active_embedding_hnsw",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("status = 'active' AND embedding IS NOT NULL"),
    )
    op.create_table(
        "memory_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("content_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensitivity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("proposed_by_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("activated_memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "memory_type IN ('profile_fact','stable_preference','execution_pattern')",
            name="ck_memory_candidates_type",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('sensitive','highly_sensitive')",
            name="ck_memory_candidates_sensitivity",
        ),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','rejected','expired')",
            name="ck_memory_candidates_status",
        ),
        sa.ForeignKeyConstraint(["activated_memory_id"], ["memories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposed_by_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_candidates_user_status", "memory_candidates", ["user_id", "status", "created_at"]
    )
    op.create_table(
        "search_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("reliability", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('official','job_board','blog','community','other')",
            name="ck_search_sources_type",
        ),
        sa.CheckConstraint("reliability BETWEEN 0 AND 1", name="ck_search_sources_reliability"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "url", name="uq_search_sources_run_url"),
    )
    op.create_table(
        "experience_atoms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("goal_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1024), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            "goal_type IN ('agent_app','backend','frontend','data','product','other')",
            name="ck_experience_atoms_goal_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experience_atoms_goal_active", "experience_atoms", ["goal_type", "is_active"]
    )
    op.create_index(
        "ix_experience_atoms_active_embedding_hnsw",
        "experience_atoms",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("is_active AND embedding IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove only Stage 4 evidence tables; retain the shared vector extension."""
    op.drop_index(
        "ix_experience_atoms_active_embedding_hnsw",
        table_name="experience_atoms",
        postgresql_using="hnsw",
    )
    op.drop_index("ix_experience_atoms_goal_active", table_name="experience_atoms")
    op.drop_table("experience_atoms")
    op.drop_table("search_sources")
    op.drop_index("ix_memory_candidates_user_status", table_name="memory_candidates")
    op.drop_table("memory_candidates")
    op.drop_index(
        "ix_memories_active_embedding_hnsw", table_name="memories", postgresql_using="hnsw"
    )
    op.drop_index("ix_memories_user_status_type", table_name="memories")
    op.drop_table("memories")
