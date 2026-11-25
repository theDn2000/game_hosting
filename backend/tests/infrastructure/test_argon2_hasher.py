import time

import pytest
from argon2 import PasswordHasher

from app.infrastructure.argon2_hasher import Argon2PasswordHasher

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def hasher():
    return Argon2PasswordHasher()


@pytest.fixture(scope="module")
def stored(hasher):
    return hasher.hash(PASSWORD)


def test_the_hash_is_argon2id_with_the_parameters_embedded(stored):
    assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert PASSWORD not in stored


def test_the_right_password_verifies(hasher, stored):
    assert hasher.verify(stored, PASSWORD) is True


@pytest.mark.parametrize("wrong", ["", "wrong password entirely", PASSWORD + "x", PASSWORD.upper(), PASSWORD[:-1]])
def test_a_wrong_password_returns_false(hasher, stored, wrong):
    assert hasher.verify(stored, wrong) is False


@pytest.mark.parametrize(
    "garbage",
    ["", "not a hash", "$argon2id$", "$argon2id$v=19$m=1,t=1,p=1$bad$bad", "$2b$12$abcdefghijklmnopqrstuv", "plaintext password"],
)
def test_a_malformed_or_foreign_hash_is_simply_not_a_match(hasher, garbage):
    assert hasher.verify(garbage, PASSWORD) is False


def test_every_hash_gets_its_own_random_salt(hasher):
    first, second = hasher.hash(PASSWORD), hasher.hash(PASSWORD)
    assert first != second
    assert hasher.verify(first, PASSWORD) and hasher.verify(second, PASSWORD)


def test_hashing_is_deliberately_slow_but_not_unusable(hasher):
    start = time.perf_counter()
    hasher.hash(PASSWORD)
    elapsed = time.perf_counter() - start
    assert 0.005 < elapsed < 5  # tens of milliseconds, never instant and never seconds


def test_a_current_hash_does_not_need_rehashing(hasher, stored):
    assert hasher.needs_rehash(stored) is False


def test_a_hash_made_with_weaker_parameters_needs_rehashing(hasher):
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    assert hasher.verify(weak, PASSWORD) is True  # still verifies...
    assert hasher.needs_rehash(weak) is True  # ...and is flagged for an upgrade


@pytest.mark.parametrize("garbage", ["", "not a hash", "$2b$12$abcdefghijklmnopqrstuv"])
def test_an_unreadable_hash_is_flagged_for_rehashing(hasher, garbage):
    assert hasher.needs_rehash(garbage) is True


def test_unicode_passwords_work(hasher):
    password = "contraseña-muy-larga-🔐-日本語"
    assert hasher.verify(hasher.hash(password), password) is True
