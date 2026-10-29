"""PostgreSQL connection layer (SQLAlchemy engine and sessions).

Only this package knows about SQLAlchemy and psycopg. Connection failures are
translated into `DatabaseUnavailableError`, and passwords never appear in
messages (URLs are always redacted before being shown).
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.errors import DatabaseUnavailableError

DATABASE_URL_VARIABLE = "GAME_HOSTING_DATABASE_URL"
REQUIRED_DRIVER = "postgresql+psycopg"
CONNECT_TIMEOUT_SECONDS = 5


def redact_url(url):
    """The URL as text, with the password replaced by `***`."""
    return make_url(url).render_as_string(hide_password=True)


def create_database_engine(url, connect_timeout=CONNECT_TIMEOUT_SECONDS):
    """Build an engine for a `postgresql+psycopg://` URL.

    Nothing connects here: the first connection happens on first use.
    """
    try:
        parsed = make_url(url)
    except ArgumentError as exc:
        # Do not echo the value: it may contain a password.
        raise ValueError("The database URL is not a valid URL.") from exc
    if parsed.drivername != REQUIRED_DRIVER:
        raise ValueError(
            f"The database URL must use the '{REQUIRED_DRIVER}' driver, "
            f"for example {REQUIRED_DRIVER}://user:password@host:5432/dbname"
        )
    return create_engine(
        parsed,
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def translate_connection_errors(engine: Engine):
    """Turn connection-level SQLAlchemy errors into DatabaseUnavailableError.

    The message carries the redacted URL and the driver's text (which never
    includes the password).
    """
    try:
        yield
    except (OperationalError, InterfaceError) as exc:
        detail = str(getattr(exc, "orig", None) or exc.__class__.__name__).strip()
        raise DatabaseUnavailableError(
            f"Cannot reach the database {redact_url(engine.url)}: {detail}"
        ) from exc


def check_connection(engine: Engine):
    """Raise DatabaseUnavailableError unless a trivial query succeeds."""
    with translate_connection_errors(engine):
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
