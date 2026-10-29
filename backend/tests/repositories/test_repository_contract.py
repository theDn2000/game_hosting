"""Contract tests for the persistence interfaces (`app.services.repositories`).

They are written against the interfaces, not an implementation. The
`uow_factory` fixture runs every test twice: against the in-memory reference
implementation (default suite) and against the PostgreSQL implementation
(integration suite, `pytest -m integration`). Both must behave identically.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.domain.models import GameServer, Plan, User
from app.errors import (
    AlreadyExistsError,
    PlanNotFoundError,
    ServerNotFoundError,
    UserNotFoundError,
)
from app.infrastructure.db.database import create_database_engine, create_session_factory
from app.infrastructure.db.repositories import SqlUnitOfWork
from tests.support.in_memory import InMemoryStore, InMemoryUnitOfWork

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


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
        connection.execute(text("TRUNCATE game_servers, plans, users"))
    session_factory = create_session_factory(engine)
    return lambda: SqlUnitOfWork(session_factory)


def make_user(email="ana@example.com", **overrides):
    return User.new(email, created_at=overrides.pop("created_at", T0), **overrides)


def make_plan(slug="basic"):
    return Plan.new(slug, slug.title(), memory_mb=2048, cpu_millicores=1000)


def make_server(owner, plan, name="World", minutes=0, **overrides):
    return GameServer.new(
        owner.id, plan.id, name, created_at=T0 + timedelta(minutes=minutes), **overrides
    )


@pytest.fixture
def seeded(uow_factory):
    """A committed owner and plan."""
    owner, plan = make_user(), make_plan()
    with uow_factory() as uow:
        uow.users.add(owner)
        uow.plans.add(plan)
        uow.commit()
    return owner, plan


# --- users ------------------------------------------------------------------


def test_user_round_trip(uow_factory):
    user = make_user()
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    with uow_factory() as uow:
        assert uow.users.get(user.id) == user


def test_missing_user_raises(uow_factory):
    missing = uuid.uuid4()
    with uow_factory() as uow:
        with pytest.raises(UserNotFoundError) as excinfo:
            uow.users.get(missing)
    assert excinfo.value.user_id == missing


def test_duplicate_email_is_rejected(uow_factory):
    with uow_factory() as uow:
        uow.users.add(make_user("ana@example.com"))
        with pytest.raises(AlreadyExistsError) as excinfo:
            uow.users.add(make_user("ana@example.com"))
    assert excinfo.value.field == "email"


def test_email_uniqueness_ignores_case_because_it_is_normalised(uow_factory):
    with uow_factory() as uow:
        uow.users.add(User.new("Ana@Example.com", created_at=T0))
        with pytest.raises(AlreadyExistsError):
            uow.users.add(User.new("ANA@example.COM", created_at=T0))


def test_find_user_by_email_normalises_the_search(uow_factory):
    user = make_user("ana@example.com")
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    with uow_factory() as uow:
        assert uow.users.find_by_email("  ANA@Example.com ") == user
        assert uow.users.find_by_email("nobody@example.com") is None


# --- plans ------------------------------------------------------------------


def test_plan_round_trip_and_lookup_by_slug(uow_factory):
    plan = make_plan("pro")
    with uow_factory() as uow:
        uow.plans.add(plan)
        uow.commit()
    with uow_factory() as uow:
        assert uow.plans.get(plan.id) == plan
        assert uow.plans.find_by_slug("pro") == plan
        assert uow.plans.find_by_slug("other") is None


def test_missing_plan_raises(uow_factory):
    with uow_factory() as uow:
        with pytest.raises(PlanNotFoundError):
            uow.plans.get(uuid.uuid4())


def test_duplicate_slug_is_rejected(uow_factory):
    with uow_factory() as uow:
        uow.plans.add(make_plan("basic"))
        with pytest.raises(AlreadyExistsError) as excinfo:
            uow.plans.add(make_plan("basic"))
    assert excinfo.value.field == "slug"


# --- servers ----------------------------------------------------------------


def test_server_round_trip(uow_factory, seeded):
    owner, plan = seeded
    server = make_server(owner, plan)
    with uow_factory() as uow:
        uow.servers.add(server)
        uow.commit()
    with uow_factory() as uow:
        assert uow.servers.get(server.id) == server
        assert uow.servers.find_by_container_name(server.container_name) == server


def test_missing_server_raises_server_not_found(uow_factory):
    with uow_factory() as uow:
        with pytest.raises(ServerNotFoundError):
            uow.servers.get(uuid.uuid4())
        assert uow.servers.find_by_container_name("nope") is None


def test_server_needs_an_existing_owner(uow_factory, seeded):
    _, plan = seeded
    ghost = make_user("ghost@example.com")  # never stored
    with uow_factory() as uow:
        with pytest.raises(UserNotFoundError):
            uow.servers.add(make_server(ghost, plan))


def test_server_needs_an_existing_plan(uow_factory, seeded):
    owner, _ = seeded
    with uow_factory() as uow:
        with pytest.raises(PlanNotFoundError):
            uow.servers.add(make_server(owner, make_plan("never-stored")))


def test_container_names_are_unique(uow_factory, seeded):
    owner, plan = seeded
    with uow_factory() as uow:
        uow.servers.add(make_server(owner, plan, "A", container_name="minecraft-dev"))
        with pytest.raises(AlreadyExistsError) as excinfo:
            uow.servers.add(make_server(owner, plan, "B", container_name="minecraft-dev"))
    assert excinfo.value.field == "container name"


def test_listing_filters_by_owner_and_is_ordered(uow_factory, seeded):
    ana, plan = seeded
    bob = make_user("bob@example.com")
    with uow_factory() as uow:
        uow.users.add(bob)
        late = make_server(ana, plan, "late", minutes=30)
        early = make_server(ana, plan, "early", minutes=1)
        middle = make_server(ana, plan, "middle", minutes=10)
        bobs = make_server(bob, plan, "bobs", minutes=5)
        for server in (late, early, middle, bobs):
            uow.servers.add(server)
        uow.commit()
    with uow_factory() as uow:
        assert uow.servers.list_by_owner(ana.id) == [early, middle, late]
        assert uow.servers.list_by_owner(bob.id) == [bobs]
        assert uow.servers.list_by_owner(uuid.uuid4()) == []
        assert uow.servers.list_all() == [early, bobs, middle, late]


def test_listing_order_is_deterministic_when_times_tie(uow_factory, seeded):
    owner, plan = seeded
    # Eight servers: random ids are almost certainly not already in sorted order.
    servers = [make_server(owner, plan, f"s{i}", minutes=0) for i in range(8)]
    with uow_factory() as uow:
        for server in servers:
            uow.servers.add(server)
        uow.commit()
    expected = sorted(servers, key=lambda s: (s.created_at, s.id))
    with uow_factory() as uow:
        assert uow.servers.list_all() == expected


# --- unit of work -----------------------------------------------------------


def test_changes_are_visible_inside_the_unit_before_commit(uow_factory):
    user = make_user()
    with uow_factory() as uow:
        uow.users.add(user)
        assert uow.users.get(user.id) == user


def test_leaving_without_commit_discards_changes(uow_factory):
    user = make_user()
    with uow_factory() as uow:
        uow.users.add(user)
    with uow_factory() as uow:
        assert uow.users.find_by_email(user.email) is None


def test_explicit_rollback_discards_changes(uow_factory):
    user = make_user()
    with uow_factory() as uow:
        uow.users.add(user)
        uow.rollback()
        assert uow.users.find_by_email(user.email) is None
        uow.commit()
    with uow_factory() as uow:
        assert uow.users.find_by_email(user.email) is None


def test_an_exception_discards_changes_and_propagates(uow_factory):
    user = make_user()
    with pytest.raises(RuntimeError):
        with uow_factory() as uow:
            uow.users.add(user)
            raise RuntimeError("boom")
    with uow_factory() as uow:
        assert uow.users.find_by_email(user.email) is None


def test_only_committed_work_survives_a_mixed_unit(uow_factory):
    first, second = make_user("first@example.com"), make_user("second@example.com")
    with uow_factory() as uow:
        uow.users.add(first)
        uow.commit()
        uow.users.add(second)  # never committed
    with uow_factory() as uow:
        assert uow.users.find_by_email(first.email) == first
        assert uow.users.find_by_email(second.email) is None


def test_a_server_and_its_dependencies_commit_together(uow_factory):
    owner, plan = make_user(), make_plan()
    server = make_server(owner, plan)
    with uow_factory() as uow:
        uow.users.add(owner)
        uow.plans.add(plan)
        uow.servers.add(server)
        uow.commit()
    with uow_factory() as uow:
        assert uow.servers.get(server.id) == server
        assert uow.users.get(owner.id) == owner
        assert uow.plans.get(plan.id) == plan


def test_failed_unit_does_not_leave_a_half_written_state(uow_factory):
    owner, plan = make_user(), make_plan()
    with pytest.raises(UserNotFoundError):
        with uow_factory() as uow:
            uow.plans.add(plan)
            uow.servers.add(make_server(owner, plan))  # owner was never added
            uow.commit()
    with uow_factory() as uow:
        assert uow.plans.find_by_slug(plan.slug) is None


# --- behaviours that differ most between implementations ----------------------


def test_a_rejected_add_does_not_poison_the_unit(uow_factory):
    first, second = make_user("first@example.com"), make_user("second@example.com")
    with uow_factory() as uow:
        uow.users.add(first)
        with pytest.raises(AlreadyExistsError):
            uow.users.add(make_user("first@example.com"))
        uow.users.add(second)  # the unit is still usable after the rejection
        uow.commit()
    with uow_factory() as uow:
        assert uow.users.find_by_email(first.email) == first
        assert uow.users.find_by_email(second.email) == second


def test_uncommitted_changes_are_invisible_to_other_units(uow_factory):
    user = make_user()
    with uow_factory() as writer:
        writer.users.add(user)
        with uow_factory() as reader:
            assert reader.users.find_by_email(user.email) is None
        writer.commit()
    with uow_factory() as reader:
        assert reader.users.find_by_email(user.email) == user


def test_timestamps_keep_their_instant_across_time_zones_and_precision(uow_factory):
    precise = datetime(2026, 10, 29, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    user = User.new("tz@example.com", created_at=precise)
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    with uow_factory() as uow:
        stored = uow.users.get(user.id)
    assert stored.created_at == precise  # same instant, microseconds included
    assert stored.created_at.tzinfo is not None
    assert stored == user


def test_unicode_text_round_trips(uow_factory, seeded):
    owner, plan = seeded
    server = make_server(owner, plan, "Mundo ñandú 🌍 世界")
    with uow_factory() as uow:
        uow.servers.add(server)
        uow.commit()
    with uow_factory() as uow:
        assert uow.servers.get(server.id).name == "Mundo ñandú 🌍 世界"


def test_the_longest_valid_values_are_accepted(uow_factory):
    owner = User.new("a" * 240 + "@example.com", created_at=T0)
    plan = Plan.new("p" * 63, "N" * 100, memory_mb=1_048_576, cpu_millicores=256_000)
    server = GameServer.new(
        owner.id, plan.id, "S" * 64, container_name="c" * 64, created_at=T0
    )
    with uow_factory() as uow:
        uow.users.add(owner)
        uow.plans.add(plan)
        uow.servers.add(server)
        uow.commit()
    with uow_factory() as uow:
        assert uow.users.get(owner.id) == owner
        assert uow.plans.get(plan.id) == plan
        assert uow.servers.get(server.id) == server


def test_servers_of_other_owners_are_not_disturbed_by_a_failed_add(uow_factory, seeded):
    owner, plan = seeded
    kept = make_server(owner, plan, "kept", container_name="kept-1")
    with uow_factory() as uow:
        uow.servers.add(kept)
        uow.commit()
    with uow_factory() as uow:
        with pytest.raises(AlreadyExistsError):
            uow.servers.add(make_server(owner, plan, "dup", container_name="kept-1"))
        uow.commit()  # committing after a rejected add must not lose or add anything
    with uow_factory() as uow:
        assert uow.servers.list_all() == [kept]
