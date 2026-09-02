"""Daily review persistence model."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Review(Base):
    """One versioned daily review derived from persisted Task facts."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_reviews_user_idempotency"),
        UniqueConstraint(
            "user_id",
            "plan_id",
            "review_date",
            name="uq_reviews_user_plan_date",
        ),
        CheckConstraint("mood BETWEEN 1 AND 5", name="ck_reviews_mood"),
        CheckConstraint("completed_count >= 0", name="ck_reviews_completed_count"),
        CheckConstraint("abandoned_count >= 0", name="ck_reviews_abandoned_count"),
        CheckConstraint("version >= 1", name="ck_reviews_version"),
        Index("ix_reviews_user_date_created", "user_id", "review_date", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_date: Mapped[date] = mapped_column(Date, nullable=False)
    mood: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    blockers: Mapped[str | None] = mapped_column(String(500))
    adjustment_request: Mapped[str | None] = mapped_column(String(300))
    free_text: Mapped[str | None] = mapped_column(Text)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    abandoned_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suggested_replan: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    replan_reason: Mapped[str | None] = mapped_column(String(500))
    next_plan_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("agent_runs.id", name="fk_reviews_next_plan_run_id"),
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
