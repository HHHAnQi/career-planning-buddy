"""User profile persistence model."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserProfile(Base):
    """One-to-one career-planning profile for a user."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "goal_type IN "
            "('ai_backend', 'agent_app', 'backend_java', 'data_engineer', 'fullstack', 'other')",
            name="ck_user_profiles_goal_type",
        ),
        CheckConstraint(
            "stage IN ('exploring', 'preparing', 'applying', 'interviewing')",
            name="ck_user_profiles_stage",
        ),
        CheckConstraint(
            "time_budget_minutes BETWEEN 15 AND 480",
            name="ck_user_profiles_time_budget",
        ),
        CheckConstraint(
            "skill_level IN ('beginner', 'intermediate', 'advanced')",
            name="ck_user_profiles_skill_level",
        ),
        CheckConstraint("version >= 1", name="ck_user_profiles_version"),
        CheckConstraint(
            "start_date IS NULL OR deadline IS NULL OR start_date <= deadline",
            name="ck_user_profiles_period",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    goal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    time_budget_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_level: Mapped[str] = mapped_column(String(16), nullable=False)
    skill_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    preferences: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
