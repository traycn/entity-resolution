"""Field normalizers. Each takes a raw value and returns a canonical value or None.

Implement in Milestone 2. Keep every function pure and deterministic.
"""

from __future__ import annotations

NICKNAMES: dict[str, str] = {
    "bob": "robert",
    "rob": "robert",
    "bill": "william",
    "liz": "elizabeth",
    "beth": "elizabeth",
    "tony": "anthony",
    # extend in M2 (load from data/nicknames.csv)
}

STREET_SUFFIXES: dict[str, str] = {
    "st": "street",
    "ave": "avenue",
    "blvd": "boulevard",
    "rd": "road",
    # extend in M2
}


def normalize_name(raw: str | None) -> str | None:
    """Lowercase, strip accents for matching, collapse whitespace, map nicknames."""
    raise NotImplementedError


def normalize_email(raw: str | None) -> str | None:
    """Lowercase; for gmail/googlemail remove dots and +tags in the local part."""
    raise NotImplementedError


def normalize_phone(raw: str | None, default_region: str = "US") -> str | None:
    """Return E.164 (e.g. +13105550100) using `phonenumbers`, or None if invalid."""
    raise NotImplementedError


def normalize_address(raw: str | None) -> str | None:
    """Lowercase, expand suffixes, drop punctuation, standardize unit markers."""
    raise NotImplementedError


def normalize_date(raw: str | None) -> str | None:
    """Parse common formats and return ISO 8601 (YYYY-MM-DD)."""
    raise NotImplementedError
