"""Table-driven tests for the field normalizers."""

import pytest

from resolver.normalize import (
    normalize_address,
    normalize_date,
    normalize_email,
    normalize_name,
    normalize_phone,
)

NAME_CASES = [
    ("Robert", "robert"),
    ("Bob", "robert"),
    ("  BOB  ", "robert"),
    ("Liz", "elizabeth"),
    ("Mary   Ann", "mary ann"),
    ("Mary-Ann", "mary ann"),
    ("O'Brien", "obrien"),
    ("O’Neil", "oneil"),
    ("José", "jose"),
    ("Zoë", "zoe"),
    ("François", "francois"),
    ("Müller", "muller"),
    ("Łukasz", "lukasz"),
    ("Weiß", "weiss"),
    ("Søren", "soren"),
    ("Dvořák", "dvorak"),
    ("Chris", "christopher"),
    ("Smith.", "smith"),
    ("", None),
    ("   ", None),
    (None, None),
    ("---", None),
]

EMAIL_CASES = [
    ("Bob@Example.COM", "bob@example.com"),
    ("  bob@example.com  ", "bob@example.com"),
    ("john.smith@gmail.com", "johnsmith@gmail.com"),
    ("johnsmith+news@gmail.com", "johnsmith@gmail.com"),
    ("j.o.h.n+a+b@gmail.com", "john@gmail.com"),
    ("John.Smith@GoogleMail.com", "johnsmith@gmail.com"),
    ("john.smith@yahoo.com", "john.smith@yahoo.com"),
    ("john+tag@yahoo.com", "john+tag@yahoo.com"),
    ("a.b@outlook.com", "a.b@outlook.com"),
    ("user@EXAMPLE.org", "user@example.org"),
    ("not-an-email", None),
    ("missing@", None),
    ("@nolocal.com", None),
    ("two@@example.com", None),
    ("spaces in@example.com", None),
    ("+tag@gmail.com", None),
    ("", None),
    (None, None),
]

PHONE_CASES = [
    ("310-201-8960", "+13102018960"),
    ("(310) 201-8960", "+13102018960"),
    ("310.201.8960", "+13102018960"),
    ("310 201 8960", "+13102018960"),
    ("3102018960", "+13102018960"),
    ("13102018960", "+13102018960"),
    ("1-310-201-8960", "+13102018960"),
    ("+1 310-201-8960", "+13102018960"),
    ("+13102018960", "+13102018960"),
    ("(310)201-8960", "+13102018960"),
    ("  310-201-8960  ", "+13102018960"),
    ("+44 20 7946 0958", "+442079460958"),
    ("123", None),
    ("000-000-0000", None),
    ("abcdefghij", None),
    ("", None),
    (None, None),
]

ADDRESS_CASES = [
    ("851 Davis Path", "851 davis path"),
    ("123 Main St", "123 main street"),
    ("123 Main St.", "123 main street"),
    ("123 MAIN STREET", "123 main street"),
    ("45 Oak Ave", "45 oak avenue"),
    ("9 Sunset Blvd", "9 sunset boulevard"),
    ("77 Elm Rd, Apt 4", "77 elm road apartment 4"),
    ("77 Elm Rd Apt. 4", "77 elm road apartment 4"),
    ("77 Elm Road Apartment 4", "77 elm road apartment 4"),
    ("12 Pine Dr Ste 300", "12 pine drive suite 300"),
    ("12 Pine Drive Suite 300", "12 pine drive suite 300"),
    ("5 Lake Ln #12", "5 lake lane unit 12"),
    ("5 Lake Ln Unit 12", "5 lake lane unit 12"),
    ("100 N Main St", "100 north main street"),
    ("100 North Main Street", "100 north main street"),
    ("8 St Marks Pl", "8 saint marks place"),
    ("8 Saint Marks Pl", "8 saint marks place"),
    ("  1  Park   Pkwy ", "1 park parkway"),
    ("14 Rue Saint-Honoré", "14 rue sainthonore"),
    ("", None),
    (None, None),
]

DATE_CASES = [
    ("1957-05-20", "1957-05-20"),
    ("05/20/1957", "1957-05-20"),
    ("5/2/1957", "1957-05-02"),
    ("05/20/57", "1957-05-20"),
    ("05/20/04", "2004-05-20"),
    ("05/20/45", "1945-05-20"),
    ("20-May-1957", "1957-05-20"),
    ("May 20, 1957", "1957-05-20"),
    ("May 20 1957", "1957-05-20"),
    ("20 May 1957", "1957-05-20"),
    ("20  May   1957", "1957-05-20"),
    ("1957/05/20", "1957-05-20"),
    ("19570520", "1957-05-20"),
    ("05-20-1957", "1957-05-20"),
    ("  1957-05-20  ", "1957-05-20"),
    ("February 29, 2000", "2000-02-29"),
    ("02/29/1999", None),
    ("13/45/2000", None),
    ("not a date", None),
    ("", None),
    (None, None),
]


@pytest.mark.parametrize(("raw", "expected"), NAME_CASES)
def test_normalize_name(raw: str | None, expected: str | None) -> None:
    assert normalize_name(raw, is_first_name=True) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Will", "will"),
        ("Pat", "pat"),
        ("Bob", "bob"),
        ("Jack", "jack"),
        ("Mike", "mike"),
        ("O'Brien", "obrien"),
        ("Müller", "muller"),
        ("", None),
        (None, None),
    ],
)
def test_surname_keeps_nickname_like_values(
    raw: str | None, expected: str | None
) -> None:
    assert normalize_name(raw) == expected
    assert normalize_name(raw, is_first_name=False) == expected


def test_same_value_differs_by_name_part() -> None:
    assert normalize_name("Will", is_first_name=True) == "william"
    assert normalize_name("Will", is_first_name=False) == "will"


@pytest.mark.parametrize(("raw", "expected"), EMAIL_CASES)
def test_normalize_email(raw: str | None, expected: str | None) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), PHONE_CASES)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), ADDRESS_CASES)
def test_normalize_address(raw: str | None, expected: str | None) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), DATE_CASES)
def test_normalize_date(raw: str | None, expected: str | None) -> None:
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "fn",
    [
        normalize_name,
        normalize_email,
        normalize_phone,
        normalize_address,
        normalize_date,
    ],
)
def test_idempotent_on_own_output(fn) -> None:  # type: ignore[no-untyped-def]
    samples = [
        "Bob",
        "a.b+c@gmail.com",
        "(310) 201-8960",
        "5 Lake Ln #12",
        "May 20, 1957",
    ]
    for s in samples:
        once = fn(s)
        if once is not None:
            assert fn(once) == once
