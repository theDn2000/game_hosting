"""Behaviour specific to the PostgreSQL repositories (the shared contract is in
tests/repositories/test_repository_contract.py)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.domain.models import GameServer, Plan, User
from app.errors import InvalidDataError
from app.infrastructure.db.database import create_database_engine, create_session_factory
from app.infrastructure.db.repositories import SqlUnitOfWork

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.fixture
def engine(migrated_database_url):
    engine = create_database_engine(migrated_database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE game_servers, plans, users"))
    yield engine
    engine.dispose()


@pytest.fixture
def uow_factory(engine):
    factory = create_session_factory(engine)
    return lambda: SqlUnitOfWork(factory)


def test_a_check_violation_that_slips_past_the_domain_becomes_invalid_data(uow_factory):
    plan = Plan.new("basic", "Basic", memory_mb=2048, cpu_millicores=1000)
    # Bypass the domain's validation on purpose: the database is the last line of defence.
    object.__setattr__(plan, "slug", "NOT-VALID")
    with uow_factory() as uow:
        with pytest.raises(InvalidDataError) as excinfo:
            uow.plans.add(plan)
    assert "ck_plans_slug_format" in str(excinfo.value)
    assert "NOT-VALID" not in str(excinfo.value)  # values are not echoed


def test_a_stored_server_with_an_unknown_game_is_reported_not_crashed_on(engine, uow_factory):
    owner, plan = User.new("ana@example.com", created_at=NOW), Plan.new(
        "basic", "Basic", memory_mb=2048, cpu_millicores=1000
    )
    server_id = uuid.uuid4()
    with uow_factory() as uow:
        uow.users.add(owner)
        uow.plans.add(plan)
        uow.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO game_servers (id, owner_id, plan_id, game, name, container_name, created_at) "
                "VALUES (:id, :owner, :plan, 'unknown-game', 'X', 'gh-x', :now)"
            ),
            {"id": server_id, "owner": owner.id, "plan": plan.id, "now": NOW},
        )
    with uow_factory() as uow:
        with pytest.raises(InvalidDataError):
            uow.servers.get(server_id)
        with pytest.raises(InvalidDataError):
            uow.servers.list_all()


def test_deleting_is_not_possible_through_the_repositories(uow_factory):
    # There is deliberately no delete operation yet (deletion policy: Phase 5/7).
    with uow_factory() as uow:
        for repository in (uow.users, uow.plans, uow.servers):
            assert not hasattr(repository, "delete") and not hasattr(repository, "remove")


def test_data_written_by_one_unit_is_durable_for_a_new_engine(migrated_database_url, uow_factory):
    user = User.new("durable@example.com", created_at=NOW)
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    other_engine = create_database_engine(migrated_database_url)
    try:
        with SqlUnitOfWork(create_session_factory(other_engine)) as uow:
            assert uow.users.get(user.id) == user
    finally:
        other_engine.dispose()


def test_a_server_survives_a_round_trip_with_its_references(uow_factory):
    owner = User.new("ana@example.com", created_at=NOW)
    plan = Plan.new("basic", "Basic", memory_mb=2048, cpu_millicores=1000)
    server = GameServer.new(owner.id, plan.id, "World", created_at=NOW)
    with uow_factory() as uow:
        uow.users.add(owner)
        uow.plans.add(plan)
        uow.servers.add(server)
        uow.commit()
    with uow_factory() as uow:
        loaded = uow.servers.get(server.id)
        assert loaded == server
        assert uow.users.get(loaded.owner_id) == owner
        assert uow.plans.get(loaded.plan_id) == plan
