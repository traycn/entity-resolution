"""Field normalizers. Each takes a raw value and returns a canonical value or None.

Keep every function pure and deterministic.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

import phonenumbers
from email_validator import EmailNotValidError, validate_email

# Nickname -> canonical given name. Covers every nickname the generator can emit.
NICKNAMES: dict[str, str] = {
    "bob": "robert", "rob": "robert", "bobby": "robert",
    "bill": "william", "will": "william", "billy": "william",
    "liz": "elizabeth", "beth": "elizabeth", "betty": "elizabeth",
    "tony": "anthony",
    "jim": "james", "jimmy": "james",
    "jack": "john", "johnny": "john",
    "rick": "richard", "dick": "richard",
    "mike": "michael", "mikey": "michael",
    "kate": "katherine", "kathy": "katherine", "katie": "katherine",
    "maggie": "margaret", "peggy": "margaret",
    "jen": "jennifer", "jenny": "jennifer",
    "joe": "joseph", "joey": "joseph",
    "tom": "thomas", "tommy": "thomas",
    "chris": "christopher",
    "dan": "daniel", "danny": "daniel",
    "pat": "patricia", "patty": "patricia",
    "sam": "samuel",
    "alex": "alexander",
    "sue": "susan", "suzy": "susan",
    "chuck": "charles", "charlie": "charles",
    "ed": "edward", "eddie": "edward",
    "matt": "matthew",
    "deb": "deborah", "debbie": "deborah",
    "steve": "stephen",
}  # fmt: skip

STREET_SUFFIXES: dict[str, str] = {
    "st": "street", "ave": "avenue", "av": "avenue", "blvd": "boulevard",
    "rd": "road", "dr": "drive", "ln": "lane", "ct": "court", "pl": "place",
    "pkwy": "parkway", "cir": "circle", "ter": "terrace", "hwy": "highway",
    "trl": "trail", "sq": "square",
}  # fmt: skip

DIRECTIONALS: dict[str, str] = {
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
}  # fmt: skip

UNIT_MARKERS: dict[str, str] = {
    "apt": "apartment", "apartment": "apartment",
    "ste": "suite", "suite": "suite",
    "unit": "unit", "fl": "floor", "bldg": "building", "rm": "room",
}  # fmt: skip

# Letters NFKD does not decompose.
_TRANSLIT = str.maketrans({"ß": "ss", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE"})  # fmt: skip

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}

# Two-digit years above this pivot are 19xx, the rest 20xx (DOBs are never in the future).
_TWO_DIGIT_YEAR_PIVOT = 30

_DATE_FORMATS_4Y = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%m/%d/%Y", "%m-%d-%Y", "%d-%b-%Y",
    "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
)  # fmt: skip
_DATE_FORMATS_2Y = ("%m/%d/%y", "%m-%d-%y")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.translate(_TRANSLIT))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_name(raw: str | None, *, is_first_name: bool = False) -> str | None:
    """Lowercase, strip accents for matching, collapse whitespace.

    Nicknames are mapped to canonical given names only when `is_first_name` is True,
    so a surname like "Will" or "Pat" is left alone.
    """
    if raw is None:
        return None
    text = _strip_accents(raw).lower().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not text:
        return None
    if not is_first_name:
        return text
    return " ".join(NICKNAMES.get(tok, tok) for tok in text.split())


def normalize_email(raw: str | None) -> str | None:
    """Lowercase; for gmail/googlemail remove dots and +tags in the local part."""
    if raw is None or not raw.strip():
        return None
    try:
        info = validate_email(raw.strip(), check_deliverability=False)
    except EmailNotValidError:
        return None
    local, domain = info.local_part.lower(), info.domain.lower()
    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
        if not local:
            return None
    return f"{local}@{domain}"


def normalize_phone(raw: str | None, default_region: str = "US") -> str | None:
    """Return E.164 (e.g. +13105550100) using `phonenumbers`, or None if invalid."""
    if raw is None or not raw.strip():
        return None
    try:
        number = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_address(raw: str | None) -> str | None:
    """Lowercase, expand suffixes, drop punctuation, standardize unit markers."""
    if raw is None:
        return None
    text = _strip_accents(raw).lower().replace("#", " unit ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    tokens = text.split()
    out: list[str] = []
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        leading = i == 0 or (i == 1 and tokens[0].isdigit())
        if tok == "st" and leading and nxt is not None and nxt not in UNIT_MARKERS:
            out.append("saint")  # "St Marks Pl", not "Street Marks Pl"
        elif tok in UNIT_MARKERS:
            out.append(UNIT_MARKERS[tok])
        elif tok in STREET_SUFFIXES and i > 0:
            out.append(STREET_SUFFIXES[tok])
        elif tok in DIRECTIONALS and i > 0:
            out.append(DIRECTIONALS[tok])
        else:
            out.append(tok)
    return " ".join(out) or None


def normalize_date(raw: str | None) -> str | None:
    """Parse common formats and return ISO 8601 (YYYY-MM-DD).

    Slash/dash dates are read month-first (US). Two-digit years use a fixed pivot.
    """
    if raw is None or not raw.strip():
        return None
    text = re.sub(r"\s+", " ", raw.strip())
    for fmt in _DATE_FORMATS_4Y:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    for fmt in _DATE_FORMATS_2Y:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        year = parsed.year % 100
        year += 1900 if year > _TWO_DIGIT_YEAR_PIVOT else 2000
        return parsed.replace(year=year).date().isoformat()
    return None
