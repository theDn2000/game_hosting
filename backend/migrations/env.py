"""Alembic environment.

The database URL comes from the GAME_HOSTING_DATABASE_URL environment
variable, never from a file. Tests may instead inject a ready-made engine with
`config.attributes["engine"]`. Only online migrations are supported.
"""

import os

from alembic import context

from app.infrastructure.db.database import DATABASE_URL_VARIABLE, create_database_engine
from app.infrastructure.db.models import Base

config = context.config
target_metadata = Base.metadata


def _engine():
    """The engine to migrate with, and whether this module must dispose of it."""
    injected = config.attributes.get("engine")
    if injected is not None:
        return injected, False
    url = os.environ.get(DATABASE_URL_VARIABLE)
    if not url:
        raise RuntimeError(
            f"{DATABASE_URL_VARIABLE} is not set. Set it to the database URL "
            "(postgresql+psycopg://user:password@host:port/dbname) and retry."
        )
    return create_database_engine(url), True


def run_migrations_online():
    engine, owned = _engine()
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        if owned:
            engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations (--sql) are not supported.")
run_migrations_online()
