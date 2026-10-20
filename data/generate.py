"""Synthetic customer data with known duplicates (Milestone 1).

    python data/generate.py --n 50000 --dup-rate 0.25 --seed 42

Writes data/sources/{crm,ads,web}.csv and data/truth.csv
(record_id, entity_id, dup_type).

`--n` is the total number of records written; `--dup-rate` is the share of those
records that are duplicates of another record (so n * dup_rate duplicates, spread
over n * (1 - dup_rate) real-world entities). Output is a pure function of the
arguments: the same seed gives byte-identical files.

`dup_type` is "original" (the first record of an entity), "dup" (an ordinary
corrupted duplicate), "hard_dup" (about 2% of duplicates), "drift_chain" (all three
records of an identity-drift entity, see below) or "household_same_name" (the single
record of a same-name child, see below).

Safety invariant: an ordinary duplicate never carries a *different* email AND a
*different* phone from its entity, since that would trip the project's
conflicting-identifier rule. "hard_dup" records deliberately break this: they carry
a new email AND a new phone (both always present), so the safety rule must refuse
to merge them. They are the known recall cost of that rule and are expected to end
up NO_MATCH or REVIEW. Households (distinct people sharing a surname and address)
are generated as hard negatives; about 5% of them also share the household phone or
email with a family member who has a different first name, so an exact identifier
match alone is not proof of the same person.

Same-name households (Jr./Sr.): about 2% of household members are the host's child
(or, when the host is too young, parent) with the host's first AND last name, the
host's phone or email, their own other identifier, and a birth date 18-40 years
apart, never after 2007. The child is a separate entity with
exactly one record (no duplicates, never a drift chain), labelled
"household_same_name"; every pair of it with the parent's records is a non-duplicate.

Identity drift: about 2% of entities get exactly three records, in time order, where
the phone changes between records 1 -> 2 and the email changes between 2 -> 3 (both
always present). Neighbouring records share an identifier, but records 1 and 3 share
neither, so they conflict and the clustering's conflict check must keep them apart
even though they are only linked through record 2. Every record of such an entity is
labelled "drift_chain".
"""

from __future__ import annotations

import csv
import random
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer
from faker import Faker

SOURCES = ("crm", "ads", "web")
COLUMNS = [
    "record_id", "source", "first_name", "last_name", "email", "email_verified",
    "phone", "address", "city", "state", "zip", "dob", "updated_at",
]  # fmt: skip

# Per-source quirks: P(email verified), P(field missing) for phone / email.
SOURCE_PROFILE = {
    "crm": {"verified": 0.90, "no_phone": 0.05, "no_email": 0.03},
    "ads": {"verified": 0.40, "no_phone": 0.45, "no_email": 0.05},
    "web": {"verified": 0.15, "no_phone": 0.20, "no_email": 0.08},
}

NICKNAMES = {
    "robert": ["bob", "rob", "bobby"], "william": ["bill", "will", "billy"],
    "elizabeth": ["liz", "beth", "betty"], "anthony": ["tony"],
    "james": ["jim", "jimmy"], "john": ["jack", "johnny"],
    "richard": ["rick", "dick"], "michael": ["mike", "mikey"],
    "katherine": ["kate", "kathy", "katie"], "margaret": ["maggie", "peggy"],
    "jennifer": ["jen", "jenny"], "joseph": ["joe", "joey"],
    "thomas": ["tom", "tommy"], "christopher": ["chris"],
    "daniel": ["dan", "danny"], "patricia": ["pat", "patty"],
    "samuel": ["sam"], "alexander": ["alex"], "susan": ["sue", "suzy"],
    "charles": ["chuck", "charlie"], "edward": ["ed", "eddie"],
    "matthew": ["matt"], "deborah": ["deb", "debbie"], "stephen": ["steve"],
}  # fmt: skip

SUFFIX_ABBREV = {
    "street": "St", "avenue": "Ave", "boulevard": "Blvd", "road": "Rd",
    "drive": "Dr", "lane": "Ln", "court": "Ct", "place": "Pl",
    "parkway": "Pkwy", "circle": "Cir", "terrace": "Ter", "way": "Way",
    "apartment": "Apt", "suite": "Ste",
}  # fmt: skip

# Valid US area codes so `phonenumbers` accepts the numbers in M2.
AREA_CODES = [
    "201", "212", "213", "305", "310", "312", "404", "415", "425", "469",
    "503", "512", "602", "617", "646", "702", "713", "718", "786", "808",
    "818", "857", "919", "954", "973",
]  # fmt: skip

EMAIL_DOMAINS = [
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "example.com",
]
GMAIL_PROB = 0.40
NAME_LOCALES = [
    ("en_US", 0.70),
    ("es_ES", 0.10),
    ("fr_FR", 0.08),
    ("de_DE", 0.07),
    ("pl_PL", 0.05),
]

# Corruption rates for duplicate records.
P_NAME_TYPO = 0.25
P_NICKNAME = 0.30
P_SWAP_NAMES = 0.06
P_EMAIL_FORMAT = 0.30  # gmail dot/plus/googlemail: same normalized email
P_EMAIL_CHANGED = 0.30  # genuinely different email (second address)
P_PHONE_CHANGED = 0.25  # genuinely different phone (old number)
P_ADDR_ABBREV = 0.50
P_ADDR_TYPO = 0.08
P_ADDR_MISSING = 0.12
P_ZIP_MISSING = 0.10
P_DOB_MISSING = 0.20
P_CASE_NOISE = 0.15
P_HARD_DUP = 0.02  # share of duplicates that differ on BOTH email and phone
P_HOUSEHOLD = 0.03  # share of entities that are a second person at an existing address
P_SHARED_ID = 0.05  # share of household members that reuse the host's phone or email
LATEST_DOB = date(2007, 12, 31)  # everyone is an adult on the 2025 reference date
P_SAME_NAME = 0.02  # share of household members who are the host's same-name Jr.
P_DRIFT = 0.02  # share of entities with a 3-record phone-then-email drift chain
DRIFT_WINDOW_DAYS = 300  # each chain step is dated in its own window, oldest first


def _weighted(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


def _typo(rng: random.Random, s: str) -> str:
    """One edit: delete, substitute, transpose, or double a letter."""
    if len(s) < 3:
        return s
    i = rng.randrange(1, len(s) - 1)
    op = rng.choice(["delete", "sub", "swap", "double"])
    if op == "delete":
        return s[:i] + s[i + 1 :]
    if op == "sub":
        return s[:i] + rng.choice("abcdefghijklmnopqrstuvwxyz") + s[i + 1 :]
    if op == "swap":
        return s[:i] + s[i + 1] + s[i] + s[i + 2 :]
    return s[:i] + s[i] + s[i:]


class Entity:
    """The true, clean values of one real-world customer."""

    __slots__ = (
        "city",
        "dob",
        "eid",
        "email",
        "first",
        "last",
        "phone",
        "same_name_as",
        "state",
        "street",
        "zip",
    )

    def __init__(self, eid: str, first: str, last: str, email: str, phone: str,
                 street: str, city: str, state: str, zip_: str, dob: date) -> None:  # fmt: skip
        self.eid, self.first, self.last, self.email, self.phone = (
            eid,
            first,
            last,
            email,
            phone,
        )
        self.street, self.city, self.state, self.zip, self.dob = (
            street,
            city,
            state,
            zip_,
            dob,
        )
        self.same_name_as: str | None = None  # eid of the same-name parent (Jr./Sr.)


class Generator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.name_fakes = {loc: Faker(loc) for loc, _ in NAME_LOCALES}
        for i, f in enumerate(self.name_fakes.values()):
            f.seed_instance(seed + 1 + i)
        self.used_emails: set[str] = set()
        self.used_phones: set[str] = set()
        self.alt_kind: dict[str, str] = {}
        self.alt_value: dict[str, str] = {}

    # ---- entities -------------------------------------------------------------------

    def _person_name(self) -> tuple[str, str]:
        f = self.name_fakes[_weighted(self.rng, NAME_LOCALES)]
        first = f.first_name().strip()
        last = f.last_name().strip()
        # Prefer a nickname-able first name for a chunk of en_US-style entities.
        if self.rng.random() < 0.15:
            first = self.rng.choice(sorted(NICKNAMES)).title()
        return first, last

    def _unique_email(self, first: str, last: str) -> str:
        ascii_f = re.sub(r"[^a-z]", "", _ascii(first.lower())) or "x"
        ascii_l = re.sub(r"[^a-z]", "", _ascii(last.lower())) or "y"
        domain = (
            "gmail.com"
            if self.rng.random() < GMAIL_PROB
            else self.rng.choice(EMAIL_DOMAINS)
        )
        while True:
            style = self.rng.choice(
                ["{f}.{l}", "{f}{l}", "{f}_{l}", "{f}{i}{l}", "{l}.{f}"]
            )
            local = style.format(f=ascii_f, l=ascii_l, i=ascii_f[0])
            if self.rng.random() < 0.6:
                local += str(self.rng.randint(1, 99))
            email = f"{local}@{domain}"
            key = _gmail_key(email)
            if key not in self.used_emails:
                self.used_emails.add(key)
                return email

    def _unique_phone(self) -> str:
        while True:
            p = (
                f"+1{self.rng.choice(AREA_CODES)}"
                f"{self.rng.randint(2, 9)}{self.rng.randint(0, 99):02d}{self.rng.randint(0, 9999):04d}"
            )
            if p not in self.used_phones:
                self.used_phones.add(p)
                return p

    def make_entity(self, idx: int, household_of: Entity | None = None) -> Entity:
        first, last = self._person_name()
        shared: str | None = None  # "phone" / "email" reused from the household host
        same_name = False  # the host's same-name child (Jr.)
        if household_of is not None:
            last = household_of.last
            street, city, state, zip_ = (household_of.street, household_of.city,
                                         household_of.state, household_of.zip)  # fmt: skip
            if self.rng.random() < P_SAME_NAME:
                # Same-name child: shares the parent's first name and one identifier,
                # born 18-40 years later.
                first, same_name = household_of.first, True
                shared = self.rng.choice(["phone", "email"])
            elif self.rng.random() < P_SHARED_ID:
                shared = self.rng.choice(["phone", "email"])
                while first.lower() == household_of.first.lower():
                    first, _ = self._person_name()
        else:
            street = self.fake.street_address()
            city, state, zip_ = (
                self.fake.city(),
                self.fake.state_abbr(),
                self.fake.zipcode(),
            )
        dob = date(1940, 1, 1) + timedelta(days=self.rng.randrange(0, 365 * 65))
        if household_of is not None and same_name:
            # 18-40 years apart; the relative is the older one (Sr.) when a younger
            # one would be born after LATEST_DOB.
            gap = timedelta(
                days=365 * self.rng.randint(18, 40) + self.rng.randrange(0, 365)
            )
            dob = household_of.dob + gap
            if dob > LATEST_DOB:
                dob = household_of.dob - gap
        email = (
            household_of.email
            if household_of is not None and shared == "email"
            else self._unique_email(first, last)
        )
        phone = (
            household_of.phone
            if household_of is not None and shared == "phone"
            else self._unique_phone()
        )
        entity = Entity(
            f"E{idx:06d}", first, last, email, phone, street, city, state, zip_, dob,
        )  # fmt: skip
        if household_of is not None and same_name:
            entity.same_name_as = household_of.eid
        return entity

    # ---- records --------------------------------------------------------------------

    def make_drift_record(self, rid: str, e: Entity, source: str, step: int,
                          ref_updated: date) -> dict[str, str]:  # fmt: skip
        """Record `step` (0, 1, 2) of e's drift chain: step 1 has a new phone, step 2
        additionally a new email. Email and phone are always present."""
        row = self.make_record(
            rid, e, source, duplicate=step > 0, ref_updated=ref_updated
        )
        phone, email = e.phone, e.email
        if step >= 1:
            phone = self.alt_value.setdefault(f"{e.eid}:phone", self._unique_phone())
        if step == 2:
            email = self.alt_value.setdefault(
                f"{e.eid}:email", self._unique_email(e.first, e.last)
            )
        newest = ref_updated - timedelta(days=(2 - step) * DRIFT_WINDOW_DAYS)
        updated = newest - timedelta(days=self.rng.randrange(0, DRIFT_WINDOW_DAYS))
        row["email"] = email
        row["phone"] = self._format_phone(phone, source)
        row["updated_at"] = updated.isoformat()
        return row

    def _format_phone(self, e164: str, source: str) -> str:
        a, b, c = e164[2:5], e164[5:8], e164[8:]
        styles = {
            "crm": [f"({a}) {b}-{c}", f"{a}-{b}-{c}"],
            "ads": [e164, f"1{a}{b}{c}", f"{a}{b}{c}"],
            "web": [f"{a}.{b}.{c}", f"{a} {b} {c}", f"+1 {a}-{b}-{c}", f"({a}){b}-{c}"],
        }
        return self.rng.choice(styles[source])

    def _format_dob(self, d: date, source: str) -> str:
        styles = {
            "crm": [d.isoformat()],
            "ads": [d.strftime("%m/%d/%Y"), d.strftime("%m/%d/%y")],
            "web": [d.strftime("%d-%b-%Y"), d.strftime("%B %d, %Y"), d.isoformat()],
        }
        return self.rng.choice(styles[source])

    def _abbreviate(self, street: str) -> str:
        def sub(m: re.Match[str]) -> str:
            return SUFFIX_ABBREV[m.group(0).lower()]

        pattern = r"\b(" + "|".join(SUFFIX_ABBREV) + r")\b"
        out = re.sub(pattern, sub, street, flags=re.IGNORECASE)
        return out.replace(".", "") if self.rng.random() < 0.5 else out

    def _gmail_variant(self, email: str) -> str:
        local, domain = email.split("@")
        if domain not in ("gmail.com", "googlemail.com"):
            return email
        base = local.replace(".", "")
        choice = self.rng.choice(["dots", "plus", "googlemail", "plus_dots"])
        if choice in ("dots", "plus_dots") and len(base) > 3:
            i = self.rng.randrange(1, len(base) - 1)
            base = base[:i] + "." + base[i:]
        if choice in ("plus", "plus_dots"):
            base += "+" + self.rng.choice(["shop", "news", "promo", "web", "2023"])
        if choice == "googlemail":
            domain = "googlemail.com"
        return f"{base}@{domain}"

    def make_record(self, rid: str, e: Entity, source: str, *, duplicate: bool,
                    ref_updated: date, hard: bool = False) -> dict[str, str]:  # fmt: skip
        rng = self.rng
        prof = SOURCE_PROFILE[source]
        first, last, email, phone = e.first, e.last, e.email, e.phone
        street, zip_ = e.street, e.zip
        dob: str = self._format_dob(e.dob, source)

        email_missing = rng.random() < prof["no_email"]
        phone_missing = rng.random() < prof["no_phone"]

        if duplicate:
            if rng.random() < P_NICKNAME and first.lower() in NICKNAMES:
                first = rng.choice(NICKNAMES[first.lower()]).title()
            if rng.random() < P_NAME_TYPO:
                if rng.random() < 0.5:
                    first = _typo(rng, first)
                else:
                    last = _typo(rng, last)
            if rng.random() < P_SWAP_NAMES:
                first, last = last, first
            if rng.random() < P_CASE_NOISE:
                first, last = (
                    rng.choice([str.upper, str.lower])(first),
                    rng.choice([str.upper, str.lower])(last),
                )
            if rng.random() < P_CASE_NOISE:
                first = f" {first} "

            # Each entity may vary only ONE hard identifier, always to the same alternate
            # value, so no two of its records can differ on both email and phone.
            kind = self.alt_kind.setdefault(e.eid, rng.choice(["email", "phone"]))
            if kind == "email" and rng.random() < P_EMAIL_CHANGED:
                email = self.alt_value.setdefault(
                    e.eid, self._unique_email(e.first, e.last)
                )
            elif rng.random() < P_EMAIL_FORMAT:
                email = self._gmail_variant(email)
            if kind == "phone" and rng.random() < P_PHONE_CHANGED:
                phone = self.alt_value.setdefault(e.eid, self._unique_phone())

            if rng.random() < P_ADDR_ABBREV:
                street = self._abbreviate(street)
            if rng.random() < P_ADDR_TYPO:
                street = _typo(rng, street)
            if rng.random() < P_ADDR_MISSING:
                street = ""
            if rng.random() < P_ZIP_MISSING:
                zip_ = ""
            if rng.random() < P_DOB_MISSING:
                dob = ""

        if hard:
            email = self._unique_email(e.first, e.last)
            phone = self._unique_phone()
            email_missing = phone_missing = False

        if rng.random() < 0.3:
            email = email.upper() if source == "web" else email
        updated = ref_updated - timedelta(days=rng.randrange(0, 900))
        verified = (not email_missing) and rng.random() < prof["verified"]
        return {
            "record_id": rid,
            "source": source,
            "first_name": first,
            "last_name": last,
            "email": "" if email_missing else email,
            "email_verified": "true" if verified else "false",
            "phone": "" if phone_missing else self._format_phone(phone, source),
            "address": street,
            "city": e.city,
            "state": e.state,
            "zip": zip_,
            "dob": dob,
            "updated_at": updated.isoformat(),
        }


def _ascii(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _gmail_key(email: str) -> str:
    local, domain = email.lower().split("@")
    if domain in ("gmail.com", "googlemail.com"):
        return local.split("+")[0].replace(".", "") + "@gmail.com"
    return f"{local}@{domain}"


def generate(
    n: int, dup_rate: float, seed: int
) -> tuple[dict[str, list[dict[str, str]]], list[tuple[str, str, str]]]:
    """Return ({source: rows}, truth rows). Pure function of (n, dup_rate, seed)."""
    if n < 1 or not 0.0 <= dup_rate < 1.0:
        raise ValueError("need n >= 1 and 0 <= dup_rate < 1")
    g = Generator(seed)
    rng = g.rng
    n_dups = round(n * dup_rate)
    n_entities = n - n_dups

    entities: list[Entity] = []
    for i in range(n_entities):
        host = rng.choice(entities) if entities and rng.random() < P_HOUSEHOLD else None
        entities.append(g.make_entity(i + 1, household_of=host))

    # Drift entities take 3 records each: their original plus 2 duplicate slots.
    # Same-name children get exactly one record and no duplicates, so every pair that
    # involves one is a parent/child non-duplicate.
    eligible = [i for i, e in enumerate(entities) if e.same_name_as is None]
    n_drift = min(round(n_entities * P_DRIFT), n_dups // 2)
    drift = {entities[i].eid for i in sorted(rng.sample(eligible, n_drift))}
    regular = [e for e in entities if e.eid not in drift and e.same_name_as is None]

    # (entity, dup_type, source, drift step) plan, then shuffled into record-id order.
    plan: list[tuple[Entity, str, str, int]] = []
    first_source: dict[str, str] = {}
    for e in entities:
        s = rng.choice(SOURCES)
        first_source[e.eid] = s
        if e.eid in drift:
            plan.append((e, "drift_chain", s, 0))
            plan.extend((e, "drift_chain", rng.choice(SOURCES), k) for k in (1, 2))
        elif e.same_name_as is not None:
            plan.append((e, "household_same_name", s, 0))
        else:
            plan.append((e, "original", s, 0))
    for _ in range(n_dups - 2 * n_drift):
        e = rng.choice(regular)
        others = [s for s in SOURCES if s != first_source[e.eid]]
        kind = "hard_dup" if rng.random() < P_HARD_DUP else "dup"
        source = rng.choice(others) if rng.random() < 0.8 else rng.choice(SOURCES)
        plan.append((e, kind, source, 0))
    rng.shuffle(plan)

    ref = date(2025, 12, 31)
    by_source: dict[str, list[dict[str, str]]] = {s: [] for s in SOURCES}
    truth: list[tuple[str, str, str]] = []
    for i, (e, kind, source, step) in enumerate(plan, start=1):
        rid = f"R{i:07d}"
        if kind == "drift_chain":
            row = g.make_drift_record(rid, e, source, step, ref)
        else:
            row = g.make_record(
                rid,
                e,
                source,
                duplicate=kind in ("dup", "hard_dup"),
                ref_updated=ref,
                hard=kind == "hard_dup",
            )
        by_source[source].append(row)
        truth.append((rid, e.eid, kind))
    return by_source, truth


def write_outputs(out_dir: Path, truth_path: Path, by_source: dict[str, list[dict[str, str]]],
                  truth: list[tuple[str, str, str]]) -> None:  # fmt: skip
    out_dir.mkdir(parents=True, exist_ok=True)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    for source, rows in by_source.items():
        with (out_dir / f"{source}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    with truth_path.open("w", newline="", encoding="utf-8") as fh:
        w2 = csv.writer(fh, lineterminator="\n")
        w2.writerow(["record_id", "entity_id", "dup_type"])
        w2.writerows(truth)


app = typer.Typer(add_completion=False)


@app.command()
def main(
    n: Annotated[int, typer.Option(help="Total records to write.")] = 50_000,
    dup_rate: Annotated[
        float, typer.Option(help="Share of records that duplicate another.")
    ] = 0.25,
    seed: Annotated[int, typer.Option(help="RNG seed; same seed, same output.")] = 42,
    out: Annotated[Path, typer.Option(help="Directory for source CSVs.")] = Path(
        "data/sources"
    ),
    truth: Annotated[Path, typer.Option(help="Ground-truth CSV path.")] = Path(
        "data/truth.csv"
    ),
) -> None:
    by_source, truth_rows = generate(n, dup_rate, seed)
    write_outputs(out, truth, by_source, truth_rows)
    n_entities = len({e for _, e, _ in truth_rows})
    typer.echo(
        f"{len(truth_rows)} records, {n_entities} entities, {len(truth_rows) - n_entities} duplicates "
        f"({sum(t == 'hard_dup' for _, _, t in truth_rows)} hard_dup, "
        f"{sum(t == 'drift_chain' for _, _, t in truth_rows) // 3} drift chains, "
        f"{sum(t == 'household_same_name' for _, _, t in truth_rows)} same-name children)"
    )
    for s, rows in by_source.items():
        typer.echo(f"  {s}: {len(rows)} -> {out / (s + '.csv')}")
    typer.echo(f"  truth -> {truth}")


if __name__ == "__main__":
    app()
