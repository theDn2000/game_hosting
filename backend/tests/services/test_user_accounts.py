import hashlib

import pytest

from app.domain.models import Role
from app.domain.passwords import MIN_PASSWORD_LENGTH
from app.errors import AlreadyExistsError, InvalidDataError, UserNotFoundError
from app.services.user_accounts import UserAccounts
from tests.support.in_memory import InMemoryStore, InMemoryUnitOfWork

PASSWORD = "correct horse battery staple"


class FakeHasher:
    """Deterministic and instant. Records what it was asked to hash, and how many
    database units were open at that moment (hashing must never run inside one)."""

    def __init__(self, open_units):
        self._open_units = open_units
        self.hashed = []
        self.open_during_hash = []

    def hash(self, password):
        self.hashed.append(password)
        self.open_during_hash.append(self._open_units["count"])
        return "$fake$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, password_hash, password):
        return password_hash == self.hash(password)

    def needs_rehash(self, password_hash):
        return False


class _Tracked:
    def __init__(self, inner, counter):
        self._inner, self._counter = inner, counter

    def __enter__(self):
        self._counter["count"] += 1
        self._inner.__enter__()
        self.users, self.plans, self.servers = (
            self._inner.users, self._inner.plans, self._inner.servers,
        )
        return self

    def __exit__(self, *exc):
        self._counter["count"] -= 1
        return self._inner.__exit__(*exc)

    def commit(self):
        self._inner.commit()

    def rollback(self):
        self._inner.rollback()


@pytest.fixture
def env():
    store = InMemoryStore()
    counter = {"count": 0}
    hasher = FakeHasher(counter)
    accounts = UserAccounts(lambda: _Tracked(InMemoryUnitOfWork(store), counter), hasher)
    return accounts, store, hasher


def stored(store, email):
    return next((u for u in store.users.values() if u.email == email), None)


# --- create ------------------------------------------------------------------------


def test_a_user_created_without_a_password_cannot_sign_in(env):
    accounts, store, hasher = env
    user = accounts.create_user("Ana@Example.com")
    assert user.email == "ana@example.com" and user.role is Role.USER
    assert user.password_hash is None and user.can_sign_in is False
    assert stored(store, "ana@example.com") == user
    assert hasher.hashed == []


def test_a_user_created_with_a_password_stores_only_its_hash(env):
    accounts, store, hasher = env
    user = accounts.create_user("ana@example.com", password=PASSWORD)
    assert user.can_sign_in is True
    assert user.password_hash != PASSWORD and PASSWORD not in user.password_hash
    assert hasher.hashed == [PASSWORD]
    assert stored(store, "ana@example.com").password_hash == user.password_hash


def test_an_admin_can_be_created(env):
    accounts, store, _ = env
    assert accounts.create_user("boss@example.com", Role.ADMIN, PASSWORD).role is Role.ADMIN


def test_the_password_policy_is_enforced_and_nothing_is_stored(env):
    accounts, store, hasher = env
    with pytest.raises(InvalidDataError) as excinfo:
        accounts.create_user("ana@example.com", password="x" * (MIN_PASSWORD_LENGTH - 1))
    assert not store.users and hasher.hashed == []
    assert "x" * (MIN_PASSWORD_LENGTH - 1) not in str(excinfo.value)


def test_an_invalid_email_is_rejected_before_any_hashing(env):
    accounts, store, hasher = env
    with pytest.raises(InvalidDataError):
        accounts.create_user("not-an-email", password=PASSWORD)
    assert not store.users and hasher.hashed == []


def test_an_invalid_role_is_rejected(env):
    accounts, store, _ = env
    with pytest.raises(InvalidDataError):
        accounts.create_user("ana@example.com", role="admin")  # text, not a Role
    assert not store.users


def test_a_duplicate_email_is_rejected_and_the_first_account_is_kept(env):
    accounts, store, _ = env
    first = accounts.create_user("ana@example.com", password=PASSWORD)
    with pytest.raises(AlreadyExistsError):
        accounts.create_user("ANA@example.com", role=Role.ADMIN, password="another long password")
    assert stored(store, "ana@example.com") == first


def test_the_password_is_normalised_before_hashing(env):
    accounts, _, hasher = env
    accounts.create_user("ana@example.com", password="ｐａｓｓｗｏｒｄ１２３４５６")
    assert hasher.hashed == ["password123456"]


# --- set_password -------------------------------------------------------------------


def test_set_password_replaces_the_hash(env):
    accounts, store, _ = env
    original = accounts.create_user("ana@example.com", password=PASSWORD)
    changed = accounts.set_password("ANA@example.com", "a completely different password")
    assert changed.password_hash != original.password_hash
    assert stored(store, "ana@example.com") == changed
    assert changed.id == original.id and changed.created_at == original.created_at


def test_set_password_enables_an_account_that_had_none(env):
    accounts, _, _ = env
    accounts.create_user("ana@example.com")
    assert accounts.set_password("ana@example.com", PASSWORD).can_sign_in is True


def test_a_bad_new_password_leaves_the_old_one_untouched(env):
    accounts, store, _ = env
    original = accounts.create_user("ana@example.com", password=PASSWORD)
    with pytest.raises(InvalidDataError):
        accounts.set_password("ana@example.com", "short")
    assert stored(store, "ana@example.com") == original


def test_set_password_for_an_unknown_user(env):
    accounts, _, hasher = env
    with pytest.raises(UserNotFoundError):
        accounts.set_password("nobody@example.com", PASSWORD)


# --- role and status ------------------------------------------------------------------


def test_set_role_promotes_and_demotes_and_is_idempotent(env):
    accounts, store, _ = env
    accounts.create_user("ana@example.com")
    assert accounts.set_role("ana@example.com", Role.ADMIN).role is Role.ADMIN
    assert accounts.set_role("ana@example.com", Role.ADMIN).role is Role.ADMIN
    assert accounts.set_role("ana@example.com", Role.USER).role is Role.USER
    assert stored(store, "ana@example.com").role is Role.USER


def test_changing_the_role_keeps_the_password(env):
    accounts, _, _ = env
    original = accounts.create_user("ana@example.com", password=PASSWORD)
    assert accounts.set_role("ana@example.com", Role.ADMIN).password_hash == original.password_hash


def test_set_role_rejects_a_text_role(env):
    accounts, store, _ = env
    accounts.create_user("ana@example.com")
    with pytest.raises(InvalidDataError):
        accounts.set_role("ana@example.com", "admin")
    assert stored(store, "ana@example.com").role is Role.USER


def test_disabling_and_enabling_an_account(env):
    accounts, store, _ = env
    accounts.create_user("ana@example.com", password=PASSWORD)
    disabled = accounts.set_active("ana@example.com", False)
    assert disabled.is_active is False and disabled.can_sign_in is False
    assert stored(store, "ana@example.com").is_active is False
    assert accounts.set_active("ana@example.com", True).can_sign_in is True


@pytest.mark.parametrize("operation", [
    lambda a: a.set_role("nobody@example.com", Role.ADMIN),
    lambda a: a.set_active("nobody@example.com", False),
])
def test_changes_to_an_unknown_user_raise_not_found(env, operation):
    accounts, _, _ = env
    with pytest.raises(UserNotFoundError):
        operation(accounts)


# --- listing and hashing discipline ------------------------------------------------------


def test_users_are_listed_by_email(env):
    accounts, _, _ = env
    assert accounts.list_users() == []
    for email in ("zoe@example.com", "ana@example.com"):
        accounts.create_user(email)
    assert [u.email for u in accounts.list_users()] == ["ana@example.com", "zoe@example.com"]


def test_hashing_never_happens_inside_a_database_transaction(env):
    accounts, _, hasher = env
    accounts.create_user("ana@example.com", password=PASSWORD)
    accounts.set_password("ana@example.com", "another long password")
    assert len(hasher.open_during_hash) == 2
    assert set(hasher.open_during_hash) == {0}


def test_errors_never_contain_the_password(env):
    accounts, _, _ = env
    password = "this-password-is-valid-but-the-email-is-not"
    with pytest.raises(InvalidDataError) as excinfo:
        accounts.create_user("not-an-email", password=password)
    assert password not in str(excinfo.value)
