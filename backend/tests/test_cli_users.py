import io

import pytest

import cli
from app.domain.models import Role, User
from app.errors import (
    AlreadyExistsError,
    DatabaseUnavailableError,
    InvalidDataError,
    UserNotFoundError,
)

PASSWORD = "correct horse battery staple"
HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aGFzaGhhc2hoYXNo"


class FakeAccounts:
    def __init__(self, error=None, users=()):
        self.error, self.users, self.calls = error, list(users), []

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def create_user(self, email, role=Role.USER, password=None):
        self.calls.append(("create", email, role, password))
        self._maybe_fail()
        user = User.new(email, role=role)
        return user.with_password_hash(HASH) if password is not None else user

    def set_password(self, email, password):
        self.calls.append(("set_password", email, password))
        self._maybe_fail()
        return User.new(email).with_password_hash(HASH)

    def set_role(self, email, role):
        self.calls.append(("set_role", email, role))
        self._maybe_fail()
        return User.new(email, role=role)

    def set_active(self, email, is_active):
        self.calls.append(("set_active", email, is_active))
        self._maybe_fail()
        return User.new(email).with_active(is_active)

    def list_users(self):
        self.calls.append(("list",))
        self._maybe_fail()
        return self.users


class FakeAdmin:
    def __init__(self, accounts=None):
        self.accounts = accounts or FakeAccounts()
        self.closed = 0

    def close(self):
        self.closed += 1


class PasswordSource:
    """Stands in for the real prompt: records how it was used."""

    def __init__(self, password=PASSWORD):
        self.password, self.requests = password, []

    def __call__(self, args):
        self.requests.append(args)
        return self.password


def run(argv, admin=None, source=None):
    admin = admin or FakeAdmin()
    source = source or PasswordSource()
    return cli.main(argv, admin=admin, read_password=source), admin, source


def assert_no_secret(captured):
    assert PASSWORD not in captured.out and PASSWORD not in captured.err
    assert HASH not in captured.out and HASH not in captured.err


# --- add ----------------------------------------------------------------------------


def test_add_asks_for_a_password_and_creates_a_regular_account(capsys):
    code, admin, source = run(["users", "add", "--email", "ana@example.com"])
    out = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert admin.accounts.calls == [("create", "ana@example.com", Role.USER, PASSWORD)]
    assert len(source.requests) == 1 and source.requests[0].password_stdin is False
    assert "User 'ana@example.com' created." in out.out
    assert "role: user" in out.out and "can sign in: yes" in out.out
    assert_no_secret(out)


def test_add_with_no_password_never_asks_for_one(capsys):
    code, admin, source = run(["users", "add", "--email", "ana@example.com", "--no-password"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert source.requests == []
    assert admin.accounts.calls == [("create", "ana@example.com", Role.USER, None)]
    assert "can sign in: no" in out


def test_add_an_admin(capsys):
    code, admin, _ = run(["users", "add", "--email", "boss@example.com", "--role", "admin"])
    assert code == cli.EXIT_OK
    assert admin.accounts.calls == [("create", "boss@example.com", Role.ADMIN, PASSWORD)]
    assert "role: admin" in capsys.readouterr().out


def test_add_can_read_the_password_from_standard_input(capsys):
    code, _, source = run(["users", "add", "--email", "a@b.co", "--password-stdin"])
    assert code == cli.EXIT_OK
    assert source.requests[0].password_stdin is True


def test_the_two_password_options_exclude_each_other():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["users", "add", "--email", "a@b.co", "--no-password", "--password-stdin"], admin=FakeAdmin())
    assert excinfo.value.code == 2


def test_there_is_no_way_to_pass_a_password_as_an_argument():
    for argv in (
        ["users", "add", "--email", "a@b.co", "--password", PASSWORD],
        ["users", "set-password", "--email", "a@b.co", "--password", PASSWORD],
        ["users", "add", "--email", "a@b.co", "--password=" + PASSWORD],
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv, admin=FakeAdmin())
        assert excinfo.value.code == 2


# --- set-password / set-role / disable / enable -----------------------------------------


def test_set_password(capsys):
    code, admin, source = run(["users", "set-password", "--email", "ana@example.com"])
    out = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert admin.accounts.calls == [("set_password", "ana@example.com", PASSWORD)]
    assert len(source.requests) == 1
    assert "Password updated for 'ana@example.com'." in out.out
    assert_no_secret(out)


def test_set_role(capsys):
    code, admin, _ = run(["users", "set-role", "--email", "ana@example.com", "--role", "admin"])
    assert code == cli.EXIT_OK
    assert admin.accounts.calls == [("set_role", "ana@example.com", Role.ADMIN)]
    assert "Role of 'ana@example.com' is now admin." in capsys.readouterr().out


@pytest.mark.parametrize("action, flag, word", [("disable", False, "disabled"), ("enable", True, "active")])
def test_disable_and_enable(capsys, action, flag, word):
    code, admin, source = run(["users", action, "--email", "ana@example.com"])
    assert code == cli.EXIT_OK
    assert admin.accounts.calls == [("set_active", "ana@example.com", flag)]
    assert source.requests == []
    assert f"Account 'ana@example.com' is now {word}." in capsys.readouterr().out


# --- list ----------------------------------------------------------------------------------


def test_list_shows_a_table_and_never_a_hash(capsys):
    users = [
        User.new("ana@example.com").with_password_hash(HASH),
        User.new("zoe@example.com", role=Role.ADMIN),
    ]
    code, _, _ = run(["users", "list"], FakeAdmin(FakeAccounts(users=users)))
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert code == cli.EXIT_OK
    assert lines[0].split() == ["EMAIL", "ROLE", "ACTIVE", "PASSWORD", "ID"]
    assert lines[1].split()[:4] == ["ana@example.com", "user", "yes", "set"]
    assert lines[2].split()[:4] == ["zoe@example.com", "admin", "yes", "not"]
    assert_no_secret(out)


def test_list_when_empty(capsys):
    code, _, _ = run(["users", "list"])
    assert code == cli.EXIT_OK
    assert capsys.readouterr().out == "(no users)\n"


# --- errors and usage -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error, exit_code",
    [
        (AlreadyExistsError("user", "email"), cli.EXIT_ALREADY_EXISTS),
        (UserNotFoundError("ana@example.com"), cli.EXIT_NOT_FOUND),
        (InvalidDataError("The password must be between 12 and 128 characters long."), cli.EXIT_INVALID_DATA),
        (DatabaseUnavailableError("db down"), cli.EXIT_DATABASE_UNAVAILABLE),
    ],
)
def test_errors_become_messages_and_exit_codes_without_leaking_the_password(capsys, error, exit_code):
    admin = FakeAdmin(FakeAccounts(error=error))
    for argv in (
        ["users", "add", "--email", "ana@example.com"],
        ["users", "set-password", "--email", "ana@example.com"],
        ["users", "set-role", "--email", "ana@example.com", "--role", "user"],
        ["users", "disable", "--email", "ana@example.com"],
        ["users", "list"],
    ):
        code, _, _ = run(argv, admin)
        captured = capsys.readouterr()
        assert code == exit_code
        assert captured.out == "" and captured.err == f"error: {error}\n"
        assert_no_secret(captured)


@pytest.mark.parametrize(
    "argv",
    [
        ["users"],
        ["users", "add"],
        ["users", "add", "--email", "a@b.co", "--role", "superuser"],
        ["users", "set-role", "--email", "a@b.co"],
        ["users", "set-role", "--email", "a@b.co", "--role", "root"],
        ["users", "set-password"],
        ["users", "disable"],
        ["users", "enable"],
    ],
)
def test_invalid_usage_exits_with_2_without_touching_anything(argv):
    admin = FakeAdmin()
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv, admin=admin, read_password=PasswordSource())
    assert excinfo.value.code == 2
    assert admin.accounts.calls == []


# --- the real password reader -------------------------------------------------------------------


class Args:
    def __init__(self, password_stdin):
        self.password_stdin = password_stdin


def test_standard_input_is_read_one_line_without_the_newline(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\nsecond line ignored\n"))
    assert cli._read_password(Args(True)) == PASSWORD


def test_windows_line_endings_are_stripped_too(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\r\n"))
    assert cli._read_password(Args(True)) == PASSWORD


def test_a_password_with_spaces_inside_is_kept_intact(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  two  spaces  inside  \n"))
    assert cli._read_password(Args(True)) == "  two  spaces  inside  "


def test_empty_standard_input_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(InvalidDataError):
        cli._read_password(Args(True))


def test_the_prompt_asks_twice_and_uses_a_hidden_input(monkeypatch):
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return PASSWORD

    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)
    assert cli._read_password(Args(False)) == PASSWORD
    assert len(prompts) == 2


def test_mismatching_confirmation_is_rejected_without_echoing_either_password(monkeypatch):
    answers = iter([PASSWORD, "a different password entirely"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))
    with pytest.raises(InvalidDataError) as excinfo:
        cli._read_password(Args(False))
    assert PASSWORD not in str(excinfo.value) and "different" not in str(excinfo.value)


def test_a_mismatch_through_main_exits_with_invalid_data(monkeypatch, capsys):
    answers = iter([PASSWORD, "something else, also long"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))
    admin = FakeAdmin()
    code = cli.main(["users", "add", "--email", "ana@example.com"], admin=admin)
    captured = capsys.readouterr()
    assert code == cli.EXIT_INVALID_DATA
    assert admin.accounts.calls == []
    assert_no_secret(captured)
