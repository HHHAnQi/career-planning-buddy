"""User persistence model."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """Guest-first application identity."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("auth_type IN ('guest', 'email', 'github')", name="ck_users_auth_type"),
        CheckConstraint(
            "(auth_type != 'email') OR (email IS NOT NULL AND password_hash IS NOT NULL)",
            name="ck_users_email_auth_has_password",
        ),
        CheckConstraint("role IN ('user', 'dev')", name="ck_users_role"),
        Index(
            "uq_users_guest_device_hash",
            "guest_device_hash",
            unique=True,
            postgresql_where=text("guest_device_hash IS NOT NULL"),
        ),
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "uq_users_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    auth_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'guest'"),
    )
    guest_device_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'user'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
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
