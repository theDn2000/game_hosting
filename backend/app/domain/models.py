"""Domain entities of the platform.

Plain, immutable dataclasses with no knowledge of Docker, HTTP or the database.
They validate themselves on construction, so an invalid entity cannot exist
(whether it is created by the application or loaded from storage). Use the
`new` factories to create entities: they generate ids, timestamps and
normalised values.

Server identity: a `GameServer` has a platform-owned UUID that customers see.
Its `container_name` is an internal runtime detail and must never be used as a
customer-facing identifier.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.errors import InvalidDataError

MAX_EMAIL_LENGTH = 254
MAX_PLAN_NAME_LENGTH = 100
MAX_SERVER_NAME_LENGTH = 64
MAX_MEMORY_MB = 1_048_576  # 1 TiB
MAX_CPU_MILLICORES = 256_000  # 256 cores

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLAN_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Valid Docker container names, at most 64 characters.
_CONTAINER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class Game(StrEnum):
    MINECRAFT_JAVA = "minecraft-java"


def normalize_email(email):
    """Trim and lowercase an e-mail address (the canonical stored form)."""
    return email.strip().lower()


def _utc_now():
    return datetime.now(UTC)


def _require_uuid(value, field):
    if not isinstance(value, uuid.UUID):
        raise InvalidDataError(f"{field} must be a UUID.")


def _require_aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidDataError(f"{field} must be a timezone-aware datetime.")


def _require_text(value, field, max_length):
    if not isinstance(value, str) or value != value.strip() or not value:
        raise InvalidDataError(f"{field} must be non-empty text without surrounding spaces.")
    if len(value) > max_length:
        raise InvalidDataError(f"{field} must be at most {max_length} characters.")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise InvalidDataError(f"{field} must not contain control characters.")


def _require_int_in_range(value, field, maximum):
    # bool is an int subclass; reject it explicitly.
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= maximum:
        raise InvalidDataError(f"{field} must be a whole number between 1 and {maximum}.")


@dataclass(frozen=True, slots=True)
class User:
    """A customer identity. Credentials are deliberately absent (Phase 4)."""

    id: uuid.UUID
    email: str
    created_at: datetime

    def __post_init__(self):
        _require_uuid(self.id, "id")
        _require_aware_datetime(self.created_at, "created_at")
        if (
            not isinstance(self.email, str)
            or self.email != normalize_email(self.email)
            or len(self.email) > MAX_EMAIL_LENGTH
            or not _EMAIL_PATTERN.match(self.email)
        ):
            raise InvalidDataError("email must be a valid, lowercase e-mail address.")

    @classmethod
    def new(cls, email, created_at=None):
        return cls(
            id=uuid.uuid4(),
            email=normalize_email(email) if isinstance(email, str) else email,
            created_at=created_at or _utc_now(),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """A product tier and the resources it allows (enforced in Phase 5)."""

    id: uuid.UUID
    slug: str
    name: str
    memory_mb: int
    cpu_millicores: int  # 1000 = one CPU core

    def __post_init__(self):
        _require_uuid(self.id, "id")
        if not isinstance(self.slug, str) or not _PLAN_SLUG_PATTERN.match(self.slug):
            raise InvalidDataError(
                "slug must be lowercase letters, digits and dashes (at most 63 characters)."
            )
        _require_text(self.name, "name", MAX_PLAN_NAME_LENGTH)
        _require_int_in_range(self.memory_mb, "memory_mb", MAX_MEMORY_MB)
        _require_int_in_range(self.cpu_millicores, "cpu_millicores", MAX_CPU_MILLICORES)

    @classmethod
    def new(cls, slug, name, memory_mb, cpu_millicores):
        return cls(
            id=uuid.uuid4(),
            slug=slug,
            name=name,
            memory_mb=memory_mb,
            cpu_millicores=cpu_millicores,
        )


@dataclass(frozen=True, slots=True)
class GameServer:
    """A game server owned by a user and running on a plan.

    `id` is the stable, customer-facing identifier. `container_name` is the
    internal name of the runtime container, unique and never shown to customers.
    """

    id: uuid.UUID
    owner_id: uuid.UUID
    plan_id: uuid.UUID
    game: Game
    name: str
    container_name: str
    created_at: datetime

    def __post_init__(self):
        _require_uuid(self.id, "id")
        _require_uuid(self.owner_id, "owner_id")
        _require_uuid(self.plan_id, "plan_id")
        _require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.game, Game):
            raise InvalidDataError("game must be a supported game.")
        _require_text(self.name, "name", MAX_SERVER_NAME_LENGTH)
        if not isinstance(self.container_name, str) or not _CONTAINER_NAME_PATTERN.match(
            self.container_name
        ):
            raise InvalidDataError("container_name must be a valid container name.")

    @classmethod
    def new(
        cls,
        owner_id,
        plan_id,
        name,
        game=Game.MINECRAFT_JAVA,
        container_name=None,
        created_at=None,
    ):
        """Create a server. Without `container_name` one is generated from the id;
        pass it explicitly only to adopt an existing container."""
        server_id = uuid.uuid4()
        return cls(
            id=server_id,
            owner_id=owner_id,
            plan_id=plan_id,
            game=game,
            name=name.strip() if isinstance(name, str) else name,
            container_name=container_name or f"gh-{server_id.hex}",
            created_at=created_at or _utc_now(),
        )
