"""Add ProviderCall audit + frozen fixture bundle tables (PR-5).

Revision ID: 20260807_0010
Revises: 20260806_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID

from alembic import op

revision: str = "20260807_0010"
down_revision: str | None = "20260806_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_LIST = "'llm','embedding','search'"
_METHOD_LIST = (
    "'generate_agent_turn','generate_plan','repair_format',"
    "'repair_business_rules','search','embed'"
)
_STATUS_LIST = "'ok','error','cancelled'"


def upgrade() -> None:
    # PR-5 introduces the PROVIDER_CALL_PROJECTION EvidenceKind value. The
    # eval_evidence_items.ck_eval_evidence_items_kind from 0009 was patched
    # in-place at the source level, but already-applied deployments still
    # carry the old enum. Re-tighten it here so DB-side CK matches the new
    # schema (also covers deploys that applied 0009 before 0010 was written).
    op.execute(
        "ALTER TABLE eval_evidence_items DROP CONSTRAINT IF EXISTS "
        "ck_eval_evidence_items_kind"
    )
    op.execute(
        "ALTER TABLE eval_evidence_items ADD CONSTRAINT "
        "ck_eval_evidence_items_kind CHECK (kind IN ("
        "'request_constraints','profile_projection',"
        "'expected_outcome','trajectory_policy','rubric',"
        "'plan_projection','task_projection','step_projection',"
        "'event_projection','tool_call_projection','tool_spec',"
        "'run_metrics','outcome_status','evidence_visible_refs',"
        "'transcript_hash','risk_signals','redacted_output',"
        "'cross_user_signal','tool_allowlist','repair_signal',"
        "'provider_call_projection'))"
    )

    # --- provider_calls (audit ledger) ---
    op.create_table(
        "provider_calls",
        sa.Column(
            "id",
            PostgreSQLUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            PostgreSQLUUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trial_id",
            PostgreSQLUUID(as_uuid=True),
            sa.ForeignKey("eval_trials.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("provider_kind", sa.String(16), nullable=False),
        sa.Column("provider_method", sa.String(32), nullable=False),
        sa.Column("logical_call_index", sa.Integer, nullable=False),
        sa.Column(
            "retry_attempt", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("request_projection", JSONB, nullable=False),
        sa.Column("request_projection_hash", sa.String(64), nullable=False),
        sa.Column("response_projection", JSONB, nullable=True),
        sa.Column("response_projection_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "run_id", "sequence", name="uq_provider_calls_run_sequence"
        ),
        sa.CheckConstraint(
            f"provider_kind IN ({_KIND_LIST})", name="ck_provider_calls_kind"
        ),
        sa.CheckConstraint(
            f"provider_method IN ({_METHOD_LIST})",
            name="ck_provider_calls_method",
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUS_LIST})", name="ck_provider_calls_status"
        ),
        sa.CheckConstraint(
            "request_projection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_provider_calls_request_hash",
        ),
        sa.CheckConstraint(
            "(response_projection_hash IS NULL) "
            "OR (response_projection_hash ~ '^[0-9a-f]{64}$')",
            name="ck_provider_calls_response_hash",
        ),
        sa.CheckConstraint(
            "(status = 'error') = (error_code IS NOT NULL)",
            name="ck_provider_calls_error_pair",
        ),
        sa.CheckConstraint(
            "(provider_kind IN ('embedding','search')) "
            "= (tokens_in IS NULL AND tokens_out IS NULL)",
            name="ck_provider_calls_tokens_pair",
        ),
    )
    op.create_index(
        "ix_provider_calls_run_kind",
        "provider_calls",
        ["run_id", "provider_kind"],
    )
    op.create_index(
        "ix_provider_calls_trial", "provider_calls", ["trial_id"]
    )

    # --- eval_provider_fixture_bundles ---
    op.create_table(
        "eval_provider_fixture_bundles",
        sa.Column(
            "id",
            PostgreSQLUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "trial_id",
            PostgreSQLUUID(as_uuid=True),
            sa.ForeignKey("eval_trials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("fixture_count", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "trial_id",
            "bundle_hash",
            name="uq_eval_provider_fixture_bundles_trial_hash",
        ),
        sa.CheckConstraint(
            "bundle_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_bundles_hash",
        ),
    )

    # --- eval_provider_fixture_items ---
    op.create_table(
        "eval_provider_fixture_items",
        sa.Column(
            "id",
            PostgreSQLUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bundle_id",
            PostgreSQLUUID(as_uuid=True),
            sa.ForeignKey(
                "eval_provider_fixture_bundles.id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("provider_kind", sa.String(16), nullable=False),
        sa.Column("provider_method", sa.String(32), nullable=False),
        sa.Column(
            "retry_attempt", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("request_projection_hash", sa.String(64), nullable=False),
        sa.Column("response_projection", JSONB, nullable=False),
        sa.Column("response_projection_hash", sa.String(64), nullable=False),
        sa.Column("fixture_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "bundle_id",
            "sequence",
            name="uq_eval_provider_fixture_items_bundle_seq",
        ),
        sa.CheckConstraint(
            f"provider_kind IN ({_KIND_LIST})",
            name="ck_eval_provider_fixture_items_kind",
        ),
        sa.CheckConstraint(
            f"provider_method IN ({_METHOD_LIST})",
            name="ck_eval_provider_fixture_items_method",
        ),
        sa.CheckConstraint(
            "request_projection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_items_request_hash",
        ),
        sa.CheckConstraint(
            "response_projection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_items_response_hash",
        ),
        sa.CheckConstraint(
            "fixture_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_items_fixture_hash",
        ),
    )
    op.create_index(
        "ix_eval_provider_fixture_items_bundle",
        "eval_provider_fixture_items",
        ["bundle_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_eval_provider_fixture_items_bundle",
        table_name="eval_provider_fixture_items",
    )
    op.drop_table("eval_provider_fixture_items")
    op.drop_table("eval_provider_fixture_bundles")
    op.drop_index(
        "ix_provider_calls_trial", table_name="provider_calls"
    )
    op.drop_index(
        "ix_provider_calls_run_kind", table_name="provider_calls"
    )
    op.drop_table("provider_calls")
    # Restore the pre-PR-5 EvidenceKind enum on eval_evidence_items so a
    # downgrade brings the DB back to the pre-PR-5 schema verbatim.
    op.execute(
        "ALTER TABLE eval_evidence_items DROP CONSTRAINT IF EXISTS "
        "ck_eval_evidence_items_kind"
    )
    op.execute(
        "ALTER TABLE eval_evidence_items ADD CONSTRAINT "
        "ck_eval_evidence_items_kind CHECK (kind IN ("
        "'request_constraints','profile_projection',"
        "'expected_outcome','trajectory_policy','rubric',"
        "'plan_projection','task_projection','step_projection',"
        "'event_projection','tool_call_projection','tool_spec',"
        "'run_metrics','outcome_status','evidence_visible_refs',"
        "'transcript_hash','risk_signals','redacted_output',"
        "'cross_user_signal','tool_allowlist','repair_signal'))"
    )
