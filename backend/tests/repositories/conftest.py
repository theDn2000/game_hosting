"""Runs every repository contract test twice: against the in-memory reference
implementation (default suite) and against PostgreSQL (integration suite,
`pytest -m integration`). Both must behave identically."""

import pytest
from sqlalchemy import text

from app.infrastructure.db.database import create_database_engine, create_session_factory
from app.infrastructure.db.repositories import SqlUnitOfWork
from tests.support.in_memory import InMemoryStore, InMemoryUnitOfWork


@pytest.fixture(
    params=["memory", pytest.param("postgres", marks=pytest.mark.integration)]
)
def uow_factory(request):
    if request.param == "memory":
        store = InMemoryStore()
        return lambda: InMemoryUnitOfWork(store)
    # A migrated PostgreSQL database shared by the session; emptied before each test.
    engine = create_database_engine(request.getfixturevalue("migrated_database_url"))
    request.addfinalizer(engine.dispose)
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE sessions, login_failures, game_servers, plans, users")
        )
    session_factory = create_session_factory(engine)
    return lambda: SqlUnitOfWork(session_factory)
