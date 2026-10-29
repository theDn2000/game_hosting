import pytest
from sqlalchemy.orm import Session

from app.errors import DatabaseUnavailableError, GameHostingError
from app.infrastructure.db.database import (
    REQUIRED_DRIVER,
    check_connection,
    create_database_engine,
    create_session_factory,
    redact_url,
)

PASSWORD = "s3cr3t-p4ssw0rd"
URL = f"postgresql+psycopg://gh_user:{PASSWORD}@127.0.0.1:5432/gh_db"
# Nothing listens on port 1, so connecting fails immediately and offline.
UNREACHABLE_URL = f"postgresql+psycopg://gh_user:{PASSWORD}@127.0.0.1:1/gh_db"


def test_redact_url_hides_the_password_only():
    redacted = redact_url(URL)
    assert PASSWORD not in redacted
    assert redacted == "postgresql+psycopg://gh_user:***@127.0.0.1:5432/gh_db"


def test_creating_an_engine_does_not_connect():
    # Port 1 is closed: this would raise if creation tried to connect.
    engine = create_database_engine(UNREACHABLE_URL)
    assert engine.url.host == "127.0.0.1"
    assert engine.url.drivername == REQUIRED_DRIVER


def test_engine_repr_does_not_leak_the_password():
    engine = create_database_engine(URL)
    assert PASSWORD not in repr(engine)
    assert PASSWORD not in str(engine)


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://u:{PASSWORD}@h/db",
        f"postgresql+psycopg2://u:{PASSWORD}@h/db",
        f"sqlite:///{PASSWORD}.db",
        f"mysql+pymysql://u:{PASSWORD}@h/db",
    ],
    ids=["no-driver", "psycopg2", "sqlite", "mysql"],
)
def test_other_drivers_are_rejected_without_echoing_the_url(url):
    with pytest.raises(ValueError) as excinfo:
        create_database_engine(url)
    assert REQUIRED_DRIVER in str(excinfo.value)
    assert PASSWORD not in str(excinfo.value)


@pytest.mark.parametrize(
    "url", ["", "not a url", "://", f"user:{PASSWORD}@host-without-scheme/db"]
)
def test_garbage_urls_are_rejected_without_echoing_them(url):
    with pytest.raises(ValueError) as excinfo:
        create_database_engine(url)
    assert PASSWORD not in str(excinfo.value)
    assert url not in str(excinfo.value) or url == ""


def test_session_factory_builds_sessions_bound_to_the_engine():
    engine = create_database_engine(URL)
    factory = create_session_factory(engine)
    with factory() as session:
        assert isinstance(session, Session)
        assert session.get_bind() is engine
    # Objects stay usable after commit, so repositories can return them.
    assert factory.kw["expire_on_commit"] is False


def test_unreachable_database_raises_a_redacted_application_error():
    # Short timeout: a closed port is not refused instantly on every OS.
    engine = create_database_engine(UNREACHABLE_URL, connect_timeout=1)
    with pytest.raises(DatabaseUnavailableError) as excinfo:
        check_connection(engine)
    message = str(excinfo.value)
    assert PASSWORD not in message
    assert "postgresql+psycopg://gh_user:***@127.0.0.1:1/gh_db" in message


def test_database_unavailable_is_an_application_error():
    assert issubclass(DatabaseUnavailableError, GameHostingError)
