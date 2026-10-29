import pytest
from sqlalchemy import text

from app.errors import DatabaseUnavailableError
from app.infrastructure.db.database import (
    check_connection,
    create_database_engine,
    create_session_factory,
)
from tests.integration.conftest import POSTGRES_DB, POSTGRES_USER

pytestmark = pytest.mark.integration


def test_check_connection_succeeds_against_real_postgres(postgres_engine):
    check_connection(postgres_engine)


def test_server_is_postgresql_18(postgres_engine):
    with postgres_engine.connect() as connection:
        version = connection.execute(text("SHOW server_version")).scalar_one()
    assert version.startswith("18.")


def test_sessions_commit_and_roll_back(postgres_engine):
    factory = create_session_factory(postgres_engine)
    with postgres_engine.begin() as connection:
        connection.execute(text("CREATE TABLE it_probe (id integer PRIMARY KEY)"))
    try:
        with factory() as session:
            session.execute(text("INSERT INTO it_probe VALUES (1)"))
            session.commit()
        with factory() as session:
            session.execute(text("INSERT INTO it_probe VALUES (2)"))
            session.rollback()
        with factory() as session:
            ids = session.execute(text("SELECT id FROM it_probe ORDER BY id")).scalars().all()
        assert ids == [1]
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DROP TABLE it_probe"))


def test_wrong_password_is_unavailable_and_never_echoes_secrets(postgres_credentials):
    password, port = postgres_credentials
    wrong = "definitely-not-the-password"
    engine = create_database_engine(
        f"postgresql+psycopg://{POSTGRES_USER}:{wrong}@127.0.0.1:{port}/{POSTGRES_DB}",
        connect_timeout=2,
    )
    with pytest.raises(DatabaseUnavailableError) as excinfo:
        check_connection(engine)
    assert wrong not in str(excinfo.value)
    assert password not in str(excinfo.value)
    assert "***" in str(excinfo.value)
    engine.dispose()


def test_unknown_database_is_unavailable(postgres_credentials):
    password, port = postgres_credentials
    engine = create_database_engine(
        f"postgresql+psycopg://{POSTGRES_USER}:{password}@127.0.0.1:{port}/no_such_db",
        connect_timeout=2,
    )
    with pytest.raises(DatabaseUnavailableError) as excinfo:
        check_connection(engine)
    assert password not in str(excinfo.value)
    engine.dispose()
