"""SQLAlchemy table definitions (the persistence side of the domain model).

These classes are an implementation detail of the database adapter: the rest of
the application only sees the domain dataclasses. The database repeats the
domain's rules as constraints (defence in depth), so data written by anything
other than this application still cannot violate them.

Constraint names follow a fixed convention, because the repositories translate
violations by name and the Alembic migrations must produce the very same names.
Alembic does not compare CHECK constraints when autogenerating, so the
constraint tests exercise them against a real database instead.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.auth import MAX_CLIENT_KEY_LENGTH
from app.domain.models import (
    MAX_CPU_MILLICORES,
    MAX_EMAIL_LENGTH,
    MAX_MEMORY_MB,
    MAX_PASSWORD_HASH_LENGTH,
    MAX_PLAN_NAME_LENGTH,
    MAX_SERVER_NAME_LENGTH,
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Regular expressions equivalent to the ones in app.domain.models.
PLAN_SLUG_REGEX = "^[a-z0-9][a-z0-9-]{0,62}$"
CONTAINER_NAME_REGEX = "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
SHA256_HEX_REGEX = "^[0-9a-f]{64}$"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    role: Mapped[str] = mapped_column(Text, server_default="user")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())

    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(
            f"char_length(email) BETWEEN 3 AND {MAX_EMAIL_LENGTH}", name="email_length"
        ),
        CheckConstraint("role IN ('user', 'admin')", name="role_valid"),
        CheckConstraint(
            f"password_hash IS NULL OR char_length(password_hash) BETWEEN 1 AND {MAX_PASSWORD_HASH_LENGTH}",
            name="password_hash_valid",
        ),
    )


class PlanRow(Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    memory_mb: Mapped[int] = mapped_column(Integer)
    cpu_millicores: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("slug"),
        CheckConstraint(f"slug ~ '{PLAN_SLUG_REGEX}'", name="slug_format"),
        CheckConstraint(
            f"char_length(name) BETWEEN 1 AND {MAX_PLAN_NAME_LENGTH} AND name = btrim(name)",
            name="name_valid",
        ),
        CheckConstraint(f"memory_mb BETWEEN 1 AND {MAX_MEMORY_MB}", name="memory_mb_range"),
        CheckConstraint(
            f"cpu_millicores BETWEEN 1 AND {MAX_CPU_MILLICORES}", name="cpu_millicores_range"
        ),
    )


class GameServerRow(Base):
    __tablename__ = "game_servers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # RESTRICT: a user or plan that still has servers cannot be deleted.
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    game: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    container_name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("container_name"),
        CheckConstraint(
            f"char_length(name) BETWEEN 1 AND {MAX_SERVER_NAME_LENGTH} AND name = btrim(name)",
            name="name_valid",
        ),
        CheckConstraint(
            f"container_name ~ '{CONTAINER_NAME_REGEX}'", name="container_name_format"
        ),
        Index("ix_game_servers_owner_id", "owner_id"),
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # CASCADE: a session cannot outlive its user.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(f"token_hash ~ '{SHA256_HEX_REGEX}'", name="token_hash_format"),
        CheckConstraint("last_seen_at >= created_at", name="last_seen_after_created"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_sessions_user_id_created_at", "user_id", "created_at"),
        Index("ix_sessions_expires_at", "expires_at"),
        Index("ix_sessions_revoked_at", "revoked_at"),
    )


class LoginFailureRow(Base):
    __tablename__ = "login_failures"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email_hash: Mapped[str] = mapped_column(Text)
    client_key: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"email_hash ~ '{SHA256_HEX_REGEX}'", name="email_hash_format"),
        CheckConstraint(
            f"char_length(client_key) BETWEEN 1 AND {MAX_CLIENT_KEY_LENGTH}",
            name="client_key_length",
        ),
        Index("ix_login_failures_email_hash_client_key_occurred_at", "email_hash", "client_key", "occurred_at"),
        Index("ix_login_failures_client_key_occurred_at", "client_key", "occurred_at"),
        Index("ix_login_failures_occurred_at", "occurred_at"),
    )
