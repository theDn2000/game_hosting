import dataclasses
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.models import (
    MAX_CPU_MILLICORES,
    MAX_EMAIL_LENGTH,
    MAX_MEMORY_MB,
    Game,
    GameServer,
    Plan,
    User,
    normalize_email,
)
from app.errors import InvalidDataError

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
OWNER = uuid.uuid4()
PLAN = uuid.uuid4()


def make_plan(**overrides):
    values = dict(id=uuid.uuid4(), slug="basic", name="Basic", memory_mb=2048, cpu_millicores=1500)
    return Plan(**{**values, **overrides})


def make_server(**overrides):
    values = dict(
        id=uuid.uuid4(),
        owner_id=OWNER,
        plan_id=PLAN,
        game=Game.MINECRAFT_JAVA,
        name="My world",
        container_name="gh-abc",
        created_at=NOW,
    )
    return GameServer(**{**values, **overrides})


# --- User -------------------------------------------------------------------


def test_user_new_normalises_the_email_and_fills_id_and_time():
    user = User.new("  Ana.Lopez@Example.COM ")
    assert user.email == "ana.lopez@example.com"
    assert isinstance(user.id, uuid.UUID) and user.id.version == 4
    assert user.created_at.tzinfo is not None
    assert abs(datetime.now(UTC) - user.created_at) < timedelta(seconds=5)


def test_user_new_accepts_an_explicit_time():
    assert User.new("a@b.co", created_at=NOW).created_at == NOW


def test_two_new_users_get_different_ids():
    assert User.new("a@b.co").id != User.new("a@b.co").id


@pytest.mark.parametrize(
    "email",
    ["", " ", "no-at-sign", "a@b", "a b@c.de", "@x.com", "a@@b.co", "a@b .co", "a" * MAX_EMAIL_LENGTH + "@b.co", None, 5],
)
def test_user_rejects_invalid_emails(email):
    with pytest.raises(InvalidDataError):
        User.new(email)


def test_user_requires_the_canonical_lowercase_email_when_built_directly():
    with pytest.raises(InvalidDataError):
        User(id=uuid.uuid4(), email="Ana@Example.com", created_at=NOW)


def test_user_rejects_naive_datetimes_and_bad_ids():
    with pytest.raises(InvalidDataError):
        User(id=uuid.uuid4(), email="a@b.co", created_at=datetime(2026, 1, 1))
    with pytest.raises(InvalidDataError):
        User(id="not-a-uuid", email="a@b.co", created_at=NOW)


def test_user_is_immutable_hashable_and_compared_by_value():
    user = User.new("a@b.co", created_at=NOW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        user.email = "other@b.co"
    assert dataclasses.replace(user) == user
    assert len({user, dataclasses.replace(user)}) == 1


def test_non_utc_aware_datetimes_are_accepted():
    plus_two = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))
    assert User.new("a@b.co", created_at=plus_two).created_at == plus_two


def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  A@B.Co\n") == "a@b.co"


# --- Plan -------------------------------------------------------------------


def test_plan_new_valid():
    plan = Plan.new("pro-2", "Pro 2", memory_mb=4096, cpu_millicores=2000)
    assert (plan.slug, plan.memory_mb, plan.cpu_millicores) == ("pro-2", 4096, 2000)
    assert plan.id.version == 4


@pytest.mark.parametrize("slug", ["", "Pro", "-x", "a_b", "a b", "ñu", "a" * 64, None])
def test_plan_rejects_invalid_slugs(slug):
    with pytest.raises(InvalidDataError):
        make_plan(slug=slug)


def test_plan_accepts_the_longest_slug():
    make_plan(slug="a" * 63)


@pytest.mark.parametrize("name", ["", " x", "x ", "line\nbreak", "tab\there", "x" * 101, None])
def test_plan_rejects_invalid_names(name):
    with pytest.raises(InvalidDataError):
        make_plan(name=name)


@pytest.mark.parametrize("value", [0, -1, 1.5, "2048", True, None, MAX_MEMORY_MB + 1])
def test_plan_rejects_invalid_memory(value):
    with pytest.raises(InvalidDataError):
        make_plan(memory_mb=value)


@pytest.mark.parametrize("value", [0, -1, 0.5, "1000", True, None, MAX_CPU_MILLICORES + 1])
def test_plan_rejects_invalid_cpu(value):
    with pytest.raises(InvalidDataError):
        make_plan(cpu_millicores=value)


def test_plan_accepts_the_limits():
    make_plan(memory_mb=1, cpu_millicores=1)
    make_plan(memory_mb=MAX_MEMORY_MB, cpu_millicores=MAX_CPU_MILLICORES)


# --- GameServer -------------------------------------------------------------


def test_new_server_generates_an_internal_container_name_from_its_id():
    server = GameServer.new(OWNER, PLAN, "  My world  ")
    assert server.name == "My world"
    assert server.game is Game.MINECRAFT_JAVA
    assert server.container_name == f"gh-{server.id.hex}"
    assert str(server.id) not in server.container_name  # the id is shown to customers, the name is not
    assert len(server.container_name) <= 64


def test_new_servers_have_different_ids_and_container_names():
    a, b = GameServer.new(OWNER, PLAN, "a"), GameServer.new(OWNER, PLAN, "b")
    assert a.id != b.id and a.container_name != b.container_name


def test_adopting_an_existing_container_keeps_its_name_but_gets_a_platform_id():
    server = GameServer.new(OWNER, PLAN, "Dev server", container_name="minecraft-dev")
    assert server.container_name == "minecraft-dev"
    assert server.id.version == 4


@pytest.mark.parametrize("name", ["-x", ".x", "a b", "a;b", "a/b", "ñ", "", "a" * 65, None, 5])
def test_server_rejects_invalid_container_names(name):
    with pytest.raises(InvalidDataError):
        make_server(container_name=name)


def test_server_accepts_the_longest_container_name():
    make_server(container_name="a" * 64)


@pytest.mark.parametrize("name", ["", "   ", "line\nbreak", "x" * 65, None])
def test_server_rejects_invalid_display_names(name):
    with pytest.raises(InvalidDataError):
        GameServer.new(OWNER, PLAN, name)


def test_server_requires_a_supported_game_enum_value():
    with pytest.raises(InvalidDataError):
        make_server(game="minecraft-java")  # a plain string is not a Game
    with pytest.raises(InvalidDataError):
        make_server(game="unknown")
    assert make_server(game=Game("minecraft-java")).game is Game.MINECRAFT_JAVA


def test_server_requires_uuid_references_and_aware_time():
    with pytest.raises(InvalidDataError):
        make_server(owner_id="x")
    with pytest.raises(InvalidDataError):
        make_server(plan_id=None)
    with pytest.raises(InvalidDataError):
        make_server(created_at=datetime(2026, 1, 1))


def test_server_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_server().name = "other"
