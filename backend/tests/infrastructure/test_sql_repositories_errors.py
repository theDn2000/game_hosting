"""Connection failures in the SQL repositories, without any database.

The engine is built with a `creator` that fails like a refused connection, so
these tests are instantaneous and offline.
"""

import uuid

import psycopg
import pytest
from sqlalchemy import create_engine

from app.domain.models import GameServer, Plan, User
from app.errors import DatabaseUnavailableError
from app.infrastructure.db.database import create_session_factory
from app.infrastructure.db.repositories import SqlUnitOfWork

PASSWORD = "s3cr3t-p4ssw0rd"
URL = f"postgresql+psycopg://gh_user:{PASSWORD}@db.example:5432/gh_db"


def refuse_connection():
    raise psycopg.OperationalError("connection refused")


@pytest.fixture
def unit_of_work():
    engine = create_engine(URL, creator=refuse_connection)
    uow = SqlUnitOfWork(create_session_factory(engine))
    yield uow
    engine.dispose()


USER = User.new("ana@example.com")
PLAN = Plan.new("basic", "Basic", memory_mb=2048, cpu_millicores=1000)
SERVER = GameServer.new(USER.id, PLAN.id, "World")

OPERATIONS = {
    "users.add": lambda uow: uow.users.add(USER),
    "users.get": lambda uow: uow.users.get(uuid.uuid4()),
    "users.find_by_email": lambda uow: uow.users.find_by_email("a@b.co"),
    "plans.add": lambda uow: uow.plans.add(PLAN),
    "plans.get": lambda uow: uow.plans.get(uuid.uuid4()),
    "plans.find_by_slug": lambda uow: uow.plans.find_by_slug("basic"),
    "servers.add": lambda uow: uow.servers.add(SERVER),
    "servers.get": lambda uow: uow.servers.get(uuid.uuid4()),
    "servers.find_by_container_name": lambda uow: uow.servers.find_by_container_name("x"),
    "servers.list_by_owner": lambda uow: uow.servers.list_by_owner(uuid.uuid4()),
    "servers.list_all": lambda uow: uow.servers.list_all(),
}


@pytest.mark.parametrize("operation", OPERATIONS.values(), ids=OPERATIONS.keys())
def test_every_operation_reports_an_unreachable_database_without_the_password(
    unit_of_work, operation
):
    with unit_of_work as uow:
        with pytest.raises(DatabaseUnavailableError) as excinfo:
            operation(uow)
    message = str(excinfo.value)
    assert PASSWORD not in message
    assert "gh_user:***@db.example:5432/gh_db" in message


def test_leaving_the_unit_after_a_connection_failure_does_not_hide_the_error(unit_of_work):
    with pytest.raises(DatabaseUnavailableError):
        with unit_of_work as uow:
            uow.users.get(uuid.uuid4())


def test_an_unrelated_exception_is_not_replaced_when_the_database_is_down(unit_of_work):
    with pytest.raises(KeyError):
        with unit_of_work:
            raise KeyError("original")
