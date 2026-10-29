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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.models import (
    MAX_CPU_MILLICORES,
    MAX_EMAIL_LENGTH,
    MAX_MEMORY_MB,
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


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(
            f"char_length(email) BETWEEN 3 AND {MAX_EMAIL_LENGTH}", name="email_length"
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
