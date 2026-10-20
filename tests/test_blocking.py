import polars as pl
import pytest

from resolver.blocking import (
    add_blocking_columns,
    candidate_pairs,
    candidate_pairs_by_key,
    soundex,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("robert", "R163"),
        ("rupert", "R163"),
        ("rubin", "R150"),
        ("ashcraft", "A261"),  # h does not separate equal codes
        ("tymczak", "T522"),
        ("pfister", "P236"),  # first letter's code is not repeated
        ("honeyman", "H555"),
        ("lee", "L000"),  # padded
        ("smith", "S530"),
        ("smyth", "S530"),
        ("", None),
        (None, None),
        ("123", None),
    ],
)
def test_soundex(name: str | None, expected: str | None) -> None:
    assert soundex(name) == expected


def _frame(*records: dict[str, str]) -> pl.DataFrame:
    base = {"email": "", "phone": "", "zip": "90210"}
    rows = [{**base, **r} for r in records]
    df = pl.DataFrame(rows).with_columns(pl.col("email", "phone").replace("", None))
    return add_blocking_columns(df)


def test_swapped_first_last_name_share_a_block() -> None:
    df = _frame(
        {"record_id": "A", "first_name": "John", "last_name": "Smith"},
        {"record_id": "B", "first_name": "Smith", "last_name": "John"},
    )
    assert ("A", "B") in candidate_pairs_by_key(df)["name_soundex_zip"]


def test_nickname_pair_lands_in_same_block() -> None:
    # Different surnames, so only the (nickname-normalized) first name can link them.
    df = _frame(
        {"record_id": "A", "first_name": "Bob", "last_name": "Jones"},
        {"record_id": "B", "first_name": "Robert", "last_name": "Smith"},
    )
    assert ("A", "B") in candidate_pairs(df)


def test_different_zip_and_name_not_paired() -> None:
    df = _frame(
        {"record_id": "A", "first_name": "Bob", "last_name": "Jones"},
        {"record_id": "B", "first_name": "Alice", "last_name": "Wong", "zip": "10001"},
    )
    assert candidate_pairs(df) == set()


def test_email_and_phone_keys_use_normalized_values() -> None:
    df = _frame(
        {
            "record_id": "A",
            "first_name": "Ann",
            "last_name": "Lee",
            "zip": "1",
            "email": "j.doe+x@gmail.com",
            "phone": "(310) 555-0123",
        },
        {
            "record_id": "B",
            "first_name": "Zed",
            "last_name": "Qux",
            "zip": "2",
            "email": "jdoe@googlemail.com",
            "phone": "310-555-0123",
        },
    )
    by_key = candidate_pairs_by_key(df)
    assert ("A", "B") in by_key["email_local"]
    assert ("A", "B") in by_key["phone_last7"]
