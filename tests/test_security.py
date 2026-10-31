import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.names import is_valid_display_name, name_key
from app.security.passwords import hash_password, is_valid_password, verify_password
from app.security.tokens import hash_token, new_share_token, new_token


@pytest.mark.parametrize("password", ["abc", "ABC", "123", "!!!", "a" * 128, "पासवर्ड"])
def test_password_rule_accepts_any_three_to_128_non_space_characters(password: str) -> None:
    assert is_valid_password(password)


@pytest.mark.parametrize("password", ["", "ab", "a b", " abc", "abc\n", "a\u00a0bc", "a" * 129])
def test_password_rule_rejects_short_long_or_whitespace(password: str) -> None:
    assert not is_valid_password(password)


@given(st.text(min_size=3, max_size=128).filter(lambda s: not any(c.isspace() for c in s)))
def test_any_whitespace_free_text_of_valid_length_is_a_valid_password(password: str) -> None:
    assert is_valid_password(password)


def test_verify_password_matches_only_the_right_password() -> None:
    stored = hash_password("abc")

    assert verify_password(stored, "abc")
    assert not verify_password(stored, "abd")
    assert not verify_password(None, "abc")


def test_tokens_are_random_and_stored_as_sha256() -> None:
    token = new_token()

    assert len(token) >= 43
    assert token != new_token()
    assert len(hash_token(token)) == 64
    assert len(new_share_token()) == 22


@pytest.mark.parametrize(
    ("raw", "key"),
    [("Riya", "riya"), ("  RIYA  ", "riya"), ("Riya   S", "riya s"), ("a\tb", "a b")],
)
def test_name_key_ignores_case_and_extra_spaces(raw: str, key: str) -> None:
    assert name_key(raw) == key


@pytest.mark.parametrize(
    ("name", "ok"), [("", False), ("   ", False), ("A", True), ("x" * 14, True), ("x" * 15, False)]
)
def test_display_names_are_one_to_fourteen_characters(name: str, ok: bool) -> None:
    assert is_valid_display_name(name) is ok
