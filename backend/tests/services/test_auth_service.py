import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import Role, User
from app.errors import AuthenticationError, TooManyAttemptsError
from app.services.auth import (
    AuthService,
    AuthSettings,
    csrf_token_for,
    csrf_token_matches,
    hash_email,
    hash_token,
)
from app.services.user_accounts import UserAccounts
from tests.support.in_memory import InMemoryStore, InMemoryUnitOfWork

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
EMAIL = "ana@example.com"
CLIENT = "203.0.113.7"


class FakeHasher:
    """Instant and deterministic; records every verification."""

    def __init__(self, version=1):
        self.version = version
        self.verify_calls = []
        self.hash_calls = []
        self.on_verify = None

    def hash(self, password):
        self.hash_calls.append(password)
        return f"$fake{self.version}$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, password_hash, password):
        self.verify_calls.append(password_hash)
        if self.on_verify:
            self.on_verify()
        return password_hash.split("$", 2)[2] == hashlib.sha256(password.encode()).hexdigest()

    def needs_rehash(self, password_hash):
        return not password_hash.startswith(f"$fake{self.version}$")


class AcceptEverything(FakeHasher):
    """A broken hasher that says yes to any password: the service itself must
    still refuse accounts that have nothing to verify against."""

    def verify(self, password_hash, password):
        self.verify_calls.append(password_hash)
        return True


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class Tokens:
    """Predictable 43-character tokens that still look like real ones."""

    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1
        return f"{self.count:043d}"


class Env:
    def __init__(self, settings=None, hasher=None):
        self.store = InMemoryStore()
        self.clock = Clock()
        self.hasher = hasher or FakeHasher()
        self.tokens = Tokens()

        def uow_factory():
            return InMemoryUnitOfWork(self.store)

        self.uow_factory = uow_factory
        self.accounts = UserAccounts(uow_factory, self.hasher, clock=self.clock)
        self.auth = AuthService(
            uow_factory, self.hasher, settings=settings, clock=self.clock, token_factory=self.tokens
        )

    def add_user(self, email=EMAIL, password=PASSWORD, role=Role.USER, active=True):
        user = self.accounts.create_user(email, role, password)
        if not active:
            user = self.accounts.set_active(email, False)
        return user

    def sessions(self):
        return list(self.store.sessions.values())

    def failures(self):
        return list(self.store.login_failures.values())

    def fail(self, email=EMAIL, client=CLIENT, count=1, password="wrong password here"):
        for _ in range(count):
            with pytest.raises(AuthenticationError):
                self.auth.sign_in(email, password, client)


@pytest.fixture
def env():
    environment = Env()
    environment.add_user()
    return environment


# --- a successful sign-in ---------------------------------------------------------------


def test_signing_in_returns_a_session_token_and_the_user(env):
    result = env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert len(result.session_token) == 43
    assert result.user.email == EMAIL and result.user.role is Role.USER
    assert result.expires_at == T0 + timedelta(days=7)
    assert result.csrf_token == csrf_token_for(result.session_token)


def test_the_database_stores_only_a_hash_of_the_token(env):
    result = env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    (session,) = env.sessions()
    assert session.token_hash == hash_token(result.session_token) != result.session_token
    assert result.session_token not in repr(env.store.__dict__)
    assert result.csrf_token not in repr(env.store.__dict__)


def test_every_sign_in_gets_a_new_unrelated_token(env):
    first = env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    second = env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert first.session_token != second.session_token and len(env.sessions()) == 2


def test_the_real_token_generator_makes_43_url_safe_characters():
    env = Env()
    env.add_user()
    token = AuthService(env.uow_factory, env.hasher, clock=env.clock).sign_in(EMAIL, PASSWORD, CLIENT).session_token
    assert len(token) == 43 and set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_the_email_is_normalised_and_the_password_unicode_normalised(env):
    env.add_user("eñe@example.com", "contraseña-con-eñe-larga")
    decomposed = "contraseña-con-eñe-larga"
    assert env.auth.sign_in("  EÑE@Example.COM ", decomposed, CLIENT).user.email == "eñe@example.com"


def test_a_successful_sign_in_forgets_earlier_failures_of_that_account_and_client(env):
    env.fail(count=3)
    assert len(env.failures()) == 3
    env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert env.failures() == []


def test_a_successful_sign_in_keeps_other_clients_failures(env):
    env.fail(client="198.51.100.1", count=2)
    env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert len(env.failures()) == 2


def test_only_the_newest_sessions_stay_active(env):
    env = Env(settings=AuthSettings(max_sessions_per_user=3))
    env.add_user()
    tokens = []
    for _ in range(5):
        tokens.append(env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token)
        env.clock.advance(seconds=1)
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(tokens[0])
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(tokens[1])
    for token in tokens[2:]:
        assert env.auth.authenticate(token)


def test_an_outdated_hash_is_upgraded_at_sign_in(env):
    env.hasher.version = 2  # the "current" parameters are now stronger
    old_hash = next(iter(env.store.users.values())).password_hash
    assert old_hash.startswith("$fake1$")
    env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    new_hash = next(iter(env.store.users.values())).password_hash
    assert new_hash.startswith("$fake2$") and env.hasher.verify(new_hash, PASSWORD)


def test_a_current_hash_is_not_rewritten(env):
    before = next(iter(env.store.users.values())).password_hash
    env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert next(iter(env.store.users.values())).password_hash == before


# --- failures look the same --------------------------------------------------------------


def failing_cases(env):
    env.add_user("nopassword@example.com", None)
    env.add_user("disabled@example.com", active=False)
    return {
        "wrong password": (EMAIL, "not the password at all"),
        "unknown account": ("nobody@example.com", PASSWORD),
        "no password set": ("nopassword@example.com", PASSWORD),
        "disabled account": ("disabled@example.com", PASSWORD),
    }


def test_every_kind_of_failure_raises_the_same_error_with_the_same_message(env):
    messages = set()
    for email, password in failing_cases(env).values():
        with pytest.raises(AuthenticationError) as excinfo:
            env.auth.sign_in(email, password, CLIENT)
        messages.add(str(excinfo.value))
    assert messages == {"Invalid credentials or session."}


def test_every_kind_of_failure_costs_exactly_one_password_verification(env):
    for name, (email, password) in failing_cases(env).items():
        env.hasher.verify_calls.clear()
        with pytest.raises(AuthenticationError):
            env.auth.sign_in(email, password, CLIENT)
        assert len(env.hasher.verify_calls) == 1, name


def test_unknown_and_passwordless_accounts_are_checked_against_a_decoy_hash(env):
    env.add_user("nopassword@example.com", None)
    real_hash = next(u for u in env.store.users.values() if u.email == EMAIL).password_hash
    for email in ("nobody@example.com", "nopassword@example.com"):
        env.hasher.verify_calls.clear()
        with pytest.raises(AuthenticationError):
            env.auth.sign_in(email, PASSWORD, CLIENT)
        (checked,) = env.hasher.verify_calls
        assert checked != real_hash


def test_the_decoy_is_created_once_and_only_when_needed(env):
    env.hasher.hash_calls.clear()
    env.auth.sign_in(EMAIL, PASSWORD, CLIENT)  # a real account: no decoy needed
    assert env.hasher.hash_calls == []
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            env.auth.sign_in("nobody@example.com", PASSWORD, CLIENT)
    assert len(env.hasher.hash_calls) == 1


def test_a_hasher_that_accepts_everything_still_cannot_sign_in_a_missing_or_blocked_account():
    env = Env(hasher=AcceptEverything())
    env.add_user("nopassword@example.com", None)
    env.add_user("disabled@example.com", active=False)
    for email in ("nobody@example.com", "nopassword@example.com", "disabled@example.com"):
        with pytest.raises(AuthenticationError):
            env.auth.sign_in(email, "anything at all, even the decoy", CLIENT)
    assert env.sessions() == []


@pytest.mark.parametrize(
    "email, password",
    [
        ("x" * 2000 + "@example.com", PASSWORD),
        (EMAIL, "p" * 2000),
        (None, PASSWORD),
        (EMAIL, None),
        (123, PASSWORD),
        (EMAIL, 123456789012),
        (EMAIL, b"bytes-password-here"),
    ],
)
def test_oversized_or_non_text_input_is_refused_without_any_hashing(env, email, password):
    env.hasher.verify_calls.clear()
    with pytest.raises(AuthenticationError):
        env.auth.sign_in(email, password, CLIENT)
    assert env.hasher.verify_calls == []
    assert len(env.failures()) == 1  # still counted, so it cannot be used to probe for free


def test_a_failure_records_a_hash_of_the_email_and_never_the_email(env):
    env.fail(email="  ANA@Example.com ")
    (failure,) = env.failures()
    assert failure.email_hash == hash_email(EMAIL)
    assert failure.client_key == CLIENT
    assert "ana" not in repr(failure).lower()


@pytest.mark.parametrize("client", [None, "", "has space", "x" * 100, 5, "ñ"])
def test_an_unusable_client_key_is_treated_as_unknown_and_still_works(env, client):
    assert env.auth.sign_in(EMAIL, PASSWORD, client).user.email == EMAIL
    env.fail(client=client)
    assert env.failures()[0].client_key == "unknown"


# --- races ----------------------------------------------------------------------------------


def test_a_password_changed_while_verifying_does_not_produce_a_session(env):
    env.hasher.on_verify = lambda: env.accounts.set_password(EMAIL, "a brand new long password")
    with pytest.raises(AuthenticationError):
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert env.sessions() == []


def test_an_account_disabled_while_verifying_does_not_produce_a_session(env):
    env.hasher.on_verify = lambda: env.accounts.set_active(EMAIL, False)
    with pytest.raises(AuthenticationError):
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert env.sessions() == []


def test_attempts_on_a_disabled_account_count_as_failures_even_with_the_right_password(env):
    env.add_user("disabled@example.com", active=False)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            env.auth.sign_in("disabled@example.com", PASSWORD, CLIENT)
    assert len(env.failures()) == 5
    # So probing for disabled accounts is throttled like any other guessing.
    with pytest.raises(TooManyAttemptsError):
        env.auth.sign_in("disabled@example.com", PASSWORD, CLIENT)


# --- throttling --------------------------------------------------------------------------------


def test_five_failures_from_one_client_for_one_account_block_even_the_right_password(env):
    env.fail(count=5)
    with pytest.raises(TooManyAttemptsError) as excinfo:
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert excinfo.value.retry_after == 15 * 60


def test_the_wait_shrinks_as_time_passes_and_ends_with_the_window(env):
    env.fail(count=5)
    env.clock.advance(seconds=100)
    with pytest.raises(TooManyAttemptsError) as excinfo:
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert excinfo.value.retry_after == 800
    env.clock.advance(seconds=800)
    assert env.auth.sign_in(EMAIL, PASSWORD, CLIENT).user.email == EMAIL


def test_the_wait_counts_from_the_oldest_failure_that_must_expire(env):
    # Five failures one minute apart: the block ends when the FIRST one leaves the
    # 15-minute window, not the last.
    for _ in range(5):
        env.fail()
        env.clock.advance(minutes=1)
    with pytest.raises(TooManyAttemptsError) as excinfo:
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert excinfo.value.retry_after == 10 * 60  # 15 minutes after the first, 5 minutes ago
    env.clock.advance(minutes=10)
    assert env.auth.sign_in(EMAIL, PASSWORD, CLIENT)


def test_the_block_is_checked_before_any_password_work_and_leaves_no_trace(env):
    env.fail(count=5)
    env.hasher.verify_calls.clear()
    rows = len(env.failures())
    for _ in range(3):
        with pytest.raises(TooManyAttemptsError):
            env.auth.sign_in(EMAIL, "wrong again and again", CLIENT)
    assert env.hasher.verify_calls == []
    assert len(env.failures()) == rows  # blocked attempts neither extend the lock nor grow the table


def test_another_client_is_not_blocked_by_someone_elses_failures(env):
    env.fail(count=5)
    assert env.auth.sign_in(EMAIL, PASSWORD, "198.51.100.9").user.email == EMAIL


def test_another_account_from_the_same_client_is_not_blocked(env):
    env.add_user("bob@example.com", PASSWORD)
    env.fail(count=5)
    assert env.auth.sign_in("bob@example.com", PASSWORD, CLIENT).user.email == "bob@example.com"


def test_a_client_that_tries_many_accounts_is_blocked_as_a_whole(env):
    for index in range(30):
        env.fail(email=f"user{index}@example.com", count=1)
    with pytest.raises(TooManyAttemptsError):
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)


def test_an_account_attacked_from_many_clients_is_blocked_everywhere(env):
    for index in range(20):
        env.fail(client=f"198.51.100.{index}", count=1)
    with pytest.raises(TooManyAttemptsError):
        env.auth.sign_in(EMAIL, PASSWORD, "192.0.2.200")


def test_failures_older_than_the_window_stop_counting(env):
    env.fail(count=4)
    env.clock.advance(minutes=16)
    env.fail(count=4)  # 4 old ones no longer count: 4 recent ones are still below the limit
    assert env.auth.sign_in(EMAIL, PASSWORD, CLIENT).user.email == EMAIL


def test_old_failures_are_cleaned_up_as_a_side_effect(env):
    env.fail(count=3)
    env.clock.advance(minutes=20)
    env.fail(count=1)
    assert len(env.failures()) == 1


def test_an_unknown_account_is_throttled_exactly_like_a_real_one(env):
    for email in (EMAIL, "ghost@example.com"):
        env.fail(email=email, client="198.51.100.77", count=5)
    waits = []
    for email in (EMAIL, "ghost@example.com"):
        with pytest.raises(TooManyAttemptsError) as excinfo:
            env.auth.sign_in(email, PASSWORD, "198.51.100.77")
        waits.append(excinfo.value.retry_after)
    assert waits[0] == waits[1]


def test_the_limits_come_from_the_settings():
    env = Env(settings=AuthSettings(max_failures_per_account_and_client=2, throttle_window=timedelta(minutes=1)))
    env.add_user()
    env.fail(count=2)
    with pytest.raises(TooManyAttemptsError) as excinfo:
        env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert excinfo.value.retry_after == 60


# --- authenticating a request ----------------------------------------------------------------------


def test_a_valid_token_authenticates_as_the_user(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    authenticated = env.auth.authenticate(token)
    assert authenticated.user.email == EMAIL
    assert authenticated.actor.user_id == authenticated.user.id and authenticated.actor.is_admin is False
    assert authenticated.csrf_token == csrf_token_for(token)


@pytest.mark.parametrize(
    "token",
    [None, "", 5, b"x" * 43, "short", "a" * 42, "a" * 44, "é" * 43, "a b" + "c" * 40, "a" * 43, "../" + "a" * 40],
)
def test_malformed_or_unknown_tokens_are_refused(env, token):
    env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(token)


def test_a_session_ends_at_its_absolute_limit_even_while_in_use():
    env = Env(settings=AuthSettings(session_lifetime=timedelta(hours=1)))
    env.add_user()
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.clock.advance(minutes=59)
    assert env.auth.authenticate(token)
    env.clock.advance(minutes=1)
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(token)


def test_a_session_ends_after_the_idle_timeout(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.clock.advance(hours=7, minutes=59)
    assert env.auth.authenticate(token)  # this activity restarts the idle clock
    env.clock.advance(hours=7, minutes=59)
    assert env.auth.authenticate(token)
    env.clock.advance(hours=8)
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(token)


def test_activity_is_written_at_most_once_per_interval(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    (session,) = env.sessions()
    env.clock.advance(seconds=30)
    env.auth.authenticate(token)
    assert env.sessions()[0].last_seen_at == session.last_seen_at  # no write yet
    env.clock.advance(seconds=31)
    env.auth.authenticate(token)
    assert env.sessions()[0].last_seen_at == T0 + timedelta(seconds=61)


def test_a_disabled_account_cannot_use_an_existing_session(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.accounts.set_active(EMAIL, False)
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(token)


def test_an_account_disabled_behind_the_services_back_cannot_use_its_session(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    user = next(iter(env.store.users.values()))
    env.store.users[user.id] = user.with_active(False)  # e.g. a manual database edit
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(token)


def test_a_role_change_applies_to_the_next_request(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    assert env.auth.authenticate(token).actor.is_admin is False
    env.accounts.set_role(EMAIL, Role.ADMIN)
    assert env.auth.authenticate(token).actor.is_admin is True
    env.accounts.set_role(EMAIL, Role.USER)
    assert env.auth.authenticate(token).actor.is_admin is False


def test_changing_the_password_signs_the_user_out_everywhere(env):
    tokens = [env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token for _ in range(3)]
    env.accounts.set_password(EMAIL, "a brand new long password")
    for token in tokens:
        with pytest.raises(AuthenticationError):
            env.auth.authenticate(token)
    assert env.auth.sign_in(EMAIL, "a brand new long password", CLIENT)


def test_one_users_sessions_survive_another_users_password_change(env):
    env.add_user("bob@example.com")
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.accounts.set_password("bob@example.com", "a brand new long password")
    assert env.auth.authenticate(token)


# --- signing out ------------------------------------------------------------------------------------


def test_signing_out_ends_only_that_session(env):
    first = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    second = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.auth.sign_out(first)
    with pytest.raises(AuthenticationError):
        env.auth.authenticate(first)
    assert env.auth.authenticate(second)


def test_signing_out_twice_or_with_rubbish_never_fails(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.auth.sign_out(token)
    env.auth.sign_out(token)
    for rubbish in (None, "", 5, "short", "a" * 43, "é" * 43):
        env.auth.sign_out(rubbish)


def test_signing_out_keeps_the_first_revocation_time(env):
    token = env.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    env.auth.sign_out(token)
    first = env.sessions()[0].revoked_at
    env.clock.advance(minutes=5)
    env.auth.sign_out(token)
    assert env.sessions()[0].revoked_at == first


# --- CSRF tokens -------------------------------------------------------------------------------------


def test_the_csrf_token_is_stable_per_session_and_different_between_sessions():
    a, b = "a" * 43, "b" * 43
    assert csrf_token_for(a) == csrf_token_for(a) != csrf_token_for(b)


def test_the_csrf_token_reveals_neither_the_session_token_nor_its_stored_hash():
    token = "S3cr3tT0k3n" + "x" * 32
    csrf = csrf_token_for(token)
    assert token not in csrf and hash_token(token) not in csrf and csrf != token


def test_the_csrf_check_accepts_only_the_right_value_for_that_session():
    token = "a" * 43
    other = "b" * 43
    assert csrf_token_matches(token, csrf_token_for(token)) is True
    assert csrf_token_matches(token, csrf_token_for(other)) is False
    for wrong in (None, "", 5, token, hash_token(token), csrf_token_for(token) + "x", csrf_token_for(token)[:-1]):
        assert csrf_token_matches(token, wrong) is False


def test_the_csrf_check_refuses_a_malformed_session_token():
    assert csrf_token_matches("short", csrf_token_for("short")) is False
    assert csrf_token_matches(None, "anything") is False


def test_sign_in_and_authenticate_agree_on_the_csrf_token(env):
    signed_in = env.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert env.auth.authenticate(signed_in.session_token).csrf_token == signed_in.csrf_token
    assert csrf_token_matches(signed_in.session_token, signed_in.csrf_token)
