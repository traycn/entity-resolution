"""Candidate-pair generation. Union of several blocking keys keeps recall high while
shrinking the comparison space from O(n^2) to a few pairs per record."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

import polars as pl

from resolver.normalize import (
    normalize_email,
    normalize_name,
    normalize_phone,
)

Pair = tuple[str, str]

# A block larger than this is dropped for that key (e.g. a shared "info@" mailbox);
# other keys still get a chance to recover its true pairs.
MAX_BLOCK_SIZE = 50

_SOUNDEX_CODES = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}


def soundex(name: str | None) -> str | None:
    """American Soundex of the first token's letters, e.g. 'robert' -> 'R163'."""
    if not name:
        return None
    letters = [c for c in name if "a" <= c <= "z"]
    if not letters:
        return None
    out = [letters[0].upper()]
    prev = _SOUNDEX_CODES.get(letters[0], "")
    for c in letters[1:]:
        code = _SOUNDEX_CODES.get(c, "")
        if code and code != prev:
            out.append(code)
        if c not in "hw":  # h/w do not separate equal codes
            prev = code
    return ("".join(out) + "000")[:4]


def load_sources(directory: Path) -> pl.DataFrame:
    """Read every source CSV (all columns as strings) into one frame."""
    frames = [
        pl.read_csv(p, infer_schema=False) for p in sorted(directory.glob("*.csv"))
    ]
    return pl.concat(frames)


def add_blocking_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add normalized `first_n`, `last_n`, `email_n`, `phone_n` used by the keys."""
    return df.with_columns(
        pl.col("first_name")
        .map_elements(
            lambda s: normalize_name(s, is_first_name=True), return_dtype=pl.String
        )
        .alias("first_n"),
        pl.col("last_name")
        .map_elements(normalize_name, return_dtype=pl.String)
        .alias("last_n"),
        pl.col("email")
        .map_elements(normalize_email, return_dtype=pl.String)
        .alias("email_n"),
        pl.col("phone")
        .map_elements(normalize_phone, return_dtype=pl.String)
        .alias("phone_n"),
    )


def _email_local(row: dict[str, str | None]) -> list[str]:
    email = row["email_n"]
    return [f"e:{email.split('@', 1)[0]}"] if email else []


def _phone_last7(row: dict[str, str | None]) -> list[str]:
    phone = row["phone_n"]
    return [f"p:{phone[-7:]}"] if phone else []


def _name_zip(row: dict[str, str | None]) -> list[str]:
    """Soundex of both names + zip. Both orders are emitted so a swapped first/last
    name still lands in the same block."""
    zip_code = (row["zip"] or "").strip()
    if not zip_code:
        return []
    keys = []
    for name in (row["last_n"], row["first_n"]):
        code = soundex((name or "").split(" ")[0] if name else None)
        if code:
            keys.append(f"n:{code}|{zip_code}")
    return keys


KEYS: dict[str, Callable[[dict[str, str | None]], list[str]]] = {
    "email_local": _email_local,
    "phone_last7": _phone_last7,
    "name_soundex_zip": _name_zip,
}


def _pairs_from_blocks(blocks: Iterable[list[str]]) -> set[Pair]:
    pairs: set[Pair] = set()
    for ids in blocks:
        if len(ids) < 2 or len(ids) > MAX_BLOCK_SIZE:
            continue
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pairs.add((a, b) if a < b else (b, a))
    return pairs


def candidate_pairs_by_key(df: pl.DataFrame) -> dict[str, set[Pair]]:
    """Candidate pairs produced by each blocking key (before the union)."""
    rows = df.to_dicts()
    result: dict[str, set[Pair]] = {}
    for name, key_fn in KEYS.items():
        blocks: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            for key in set(key_fn(row)):
                blocks[key].append(str(row["record_id"]))
        result[name] = _pairs_from_blocks(blocks.values())
    return result


def candidate_pairs(df: pl.DataFrame) -> set[Pair]:
    """Union of the pairs from every blocking key. `df` needs the normalized columns."""
    union: set[Pair] = set()
    for pairs in candidate_pairs_by_key(df).values():
        union |= pairs
    return union
