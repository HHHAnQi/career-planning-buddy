"""ProviderCall audit + frozen fixture bundle ORM.

PR-5 captures every Provider call (LLM/Search/Embedding) made inside an eval
TrialRunner-driven Run into ``provider_calls`` (the truth ledger) plus
``eval_provider_fixture_bundles`` + ``eval_provider_fixture_items`` (the
deterministic replay contract). The two layers are kept separate so the
audit table can carry live-mode calls (``trial_id IS NULL``) while the
bundle layer only exists in fixture-mode Trials.

The (run_id, sequence) UNIQUE makes ``sequence`` a stable addressing key:
``sequence`` is run-global, monotonically assigned by ``ProviderCallRecorder``.
The bundle/items ``consume()`` contract validates identity (kind/method/
retry_attempt) + content (request_projection_hash) before replay.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

_PROVIDER_KIND_VALUES = ("llm", "embedding", "search")
_PROVIDER_METHOD_VALUES = (
    "generate_agent_turn",
    "generate_plan",
    "repair_format",
    "repair_business_rules",
    "search",
    "embed",
)
_STATUS_VALUES = ("ok", "error", "cancelled")

# CK literals kept in sync both here and in migration 0010; rendered as a
# comma-joined SQL IN list to avoid a Python f-string eval-time coupling.
_KIND_LIST = ",".join(f"'{v}'" for v in _PROVIDER_KIND_VALUES)
_METHOD_LIST = ",".join(f"'{v}'" for v in _PROVIDER_METHOD_VALUES)
_STATUS_LIST = ",".join(f"'{v}'" for v in _STATUS_VALUES)


class ProviderCall(Base):
    """One Provider invocation captured for one Run.

    ``trial_id`` is NULL for live-mode Runs (prod path); filled by the
    TrialRunner-driven path so the table is joinable from ``eval_trials``.
    """

    __tablename__ = "provider_calls"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "sequence", name="uq_provider_calls_run_sequence"
        ),
        Index("ix_provider_calls_run_kind", "run_id", "provider_kind"),
        Index("ix_provider_calls_trial", "trial_id"),
        CheckConstraint(
            f"provider_kind IN ({_KIND_LIST})",
            name="ck_provider_calls_kind",
        ),
        CheckConstraint(
            f"provider_method IN ({_METHOD_LIST})",
            name="ck_provider_calls_method",
        ),
        CheckConstraint(
            f"status IN ({_STATUS_LIST})",
            name="ck_provider_calls_status",
        ),
        CheckConstraint(
            "request_projection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_provider_calls_request_hash",
        ),
        CheckConstraint(
            "(response_projection_hash IS NULL) "
            "OR (response_projection_hash ~ '^[0-9a-f]{64}$')",
            name="ck_provider_calls_response_hash",
        ),
        CheckConstraint(
            "(status = 'error') = (error_code IS NOT NULL)",
            name="ck_provider_calls_error_pair",
        ),
        # PR-9c.2 Commit 3.6: the original single tokens-pair CHECK was
        # split into two single-responsibility CHECKs (migration 0018).
        # (a) The kind-vs-tokens relationship is expressed as a
        #     disjunction below (non-LLM ⇒ NULL tokens; LLM ⇒
        #     unconstrained at this layer).
        # (b) The status-vs-tokens relationship now lives separately so
        #     error/cancelled LLM rows can legitimately carry NULL tokens
        #     (no usage info on the failure path -- previously this
        #     tripped an IntegrityError that ``AgentRunExecutor``
        #     swallowed into AGENT_EXECUTION_FAILED, masking Stage B's
        #     retry/error audit trail).
        CheckConstraint(
            "(provider_kind IN ('embedding','search') "
            "  AND tokens_in IS NULL AND tokens_out IS NULL) "
            "OR (provider_kind = 'llm')",
            name="ck_provider_calls_tokens_kind_pair",
        ),
        CheckConstraint(
            "(provider_kind <> 'llm') "
            "OR (status IN ('error', 'cancelled')) "
            "OR (tokens_in IS NOT NULL AND tokens_out IS NOT NULL)",
            name="ck_provider_calls_llm_success_tokens",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    trial_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="CASCADE"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_method: Mapped[str] = mapped_column(String(32), nullable=False)
    logical_call_index: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    request_projection: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    request_projection_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    response_projection: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    response_projection_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EvalProviderFixtureBundle(Base):
    """Frozen replay contract for one Trial.

    ``bundle_hash`` is the canonical sha256 over every member
    ``EvalProviderFixtureItem.fixture_hash``.
    """

    __tablename__ = "eval_provider_fixture_bundles"
    __table_args__ = (
        UniqueConstraint(
            "trial_id",
            "bundle_hash",
            name="uq_eval_provider_fixture_bundles_trial_hash",
        ),
        CheckConstraint(
            "bundle_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_bundles_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    trial_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_trials.id", ondelete="CASCADE"),
        nullable=False,
    )
    bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fixture_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EvalProviderFixtureItem(Base):
    """One frozen (kind, method, request_hash) -> response replay slot."""

    __tablename__ = "eval_provider_fixture_items"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id",
            "sequence",
            name="uq_eval_provider_fixture_items_bundle_seq",
        ),
        Index(
            "ix_eval_provider_fixture_items_bundle", "bundle_id"
        ),
        CheckConstraint(
            f"provider_kind IN ({_KIND_LIST})",
            name="ck_eval_provider_fixture_items_kind",
        ),
        CheckConstraint(
            f"provider_method IN ({_METHOD_LIST})",
            name="ck_eval_provider_fixture_items_method",
        ),
        CheckConstraint(
            "request_projection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_items_request_hash",
        ),
        CheckConstraint(
            "response_projection_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_items_response_hash",
        ),
        CheckConstraint(
            "fixture_hash ~ '^[0-9a-f]{64}$'",
            name="ck_eval_provider_fixture_items_fixture_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bundle_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("eval_provider_fixture_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_method: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    request_projection_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    response_projection: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False
    )
    # NULL identifies pre-0020 projection-only bundles, which are intentionally
    # rejected for replay because they cannot reconstruct the provider response.
    response_payload_json: Mapped[object | None] = mapped_column(JSONB)
    response_projection_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    fixture_hash: Mapped[str] = mapped_column(String(64), nullable=False)
