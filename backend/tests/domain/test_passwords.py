import pytest

from app.domain.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    normalize_password,
    validate_password,
)
from app.errors import InvalidDataError

GOOD = "correct horse battery staple"


def test_a_long_enough_password_is_accepted_and_returned():
    assert validate_password(GOOD) == GOOD


def test_length_boundaries():
    assert validate_password("a" * MIN_PASSWORD_LENGTH)
    assert validate_password("a" * MAX_PASSWORD_LENGTH)
    for bad in ("a" * (MIN_PASSWORD_LENGTH - 1), "a" * (MAX_PASSWORD_LENGTH + 1), ""):
        with pytest.raises(InvalidDataError):
            validate_password(bad)


def test_there_are_no_composition_rules():
    # Only lowercase letters, no digit and no symbol: accepted, as NIST advises.
    assert validate_password("lowercaseonlyletters")
    assert validate_password("1" * MIN_PASSWORD_LENGTH)


def test_spaces_and_unicode_are_allowed():
    assert validate_password("una contraseña muy larga y segura 🔐")


def test_the_password_is_unicode_normalised_before_the_length_check():
    full_width = "ｐａｓｓｗｏｒｄ１２３４５６"  # 14 full-width characters
    assert normalize_password(full_width) == "password123456"
    assert validate_password(full_width) == "password123456"
    # Twelve ligature characters expand to more characters; the normalised form counts.
    assert len(normalize_password("ﬁ" * 6)) == 12
    assert validate_password("ﬁ" * 6) == "fi" * 6


def test_the_same_password_typed_two_ways_normalises_identically():
    composed = "contraseña-con-eñe"  # ñ as one character
    decomposed = "contraseña-con-eñe"  # n + combining tilde
    assert composed != decomposed
    assert validate_password(composed) == validate_password(decomposed)


@pytest.mark.parametrize("char", ["\x00", "\n", "\t", "\r", "\x1b", "​"])
def test_control_characters_are_rejected(char):
    with pytest.raises(InvalidDataError):
        validate_password("a" * 10 + char + "b" * 5)


@pytest.mark.parametrize("value", [None, 123456789012, b"bytes-password!!", ["x"] * 12])
def test_only_text_is_accepted(value):
    with pytest.raises(InvalidDataError):
        validate_password(value)


@pytest.mark.parametrize("password", ["short", "x" * 200, "has\nnewline-and-length"])
def test_errors_never_contain_the_password(password):
    with pytest.raises(InvalidDataError) as excinfo:
        validate_password(password)
    assert password not in str(excinfo.value)
