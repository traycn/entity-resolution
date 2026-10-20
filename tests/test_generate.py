"""Milestone 1 acceptance: determinism, truth coverage, realistic and safe duplicates."""

from __future__ import annotations

import importlib.util
import re
from collections import defaultdict
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "generate", Path(__file__).parents[1] / "data" / "generate.py"
)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


@pytest.fixture(scope="module")
def dataset():
    return gen.generate(n=3000, dup_rate=0.25, seed=42)


def test_same_seed_identical_files(tmp_path):
    for name in ("a", "b"):
        by_source, truth = gen.generate(500, 0.25, 7)
        gen.write_outputs(
            tmp_path / name / "sources", tmp_path / name / "truth.csv", by_source, truth
        )
    for rel in ("sources/crm.csv", "sources/ads.csv", "sources/web.csv", "truth.csv"):
        assert (tmp_path / "a" / rel).read_bytes() == (
            tmp_path / "b" / rel
        ).read_bytes()


def test_different_seed_differs():
    a = gen.generate(200, 0.25, 1)[0]["crm"]
    b = gen.generate(200, 0.25, 2)[0]["crm"]
    assert a != b


def test_truth_covers_every_record_exactly_once(dataset):
    by_source, truth = dataset
    record_ids = [r["record_id"] for rows in by_source.values() for r in rows]
    assert len(record_ids) == len(set(record_ids)) == 3000
    assert sorted(rid for rid, _, _ in truth) == sorted(record_ids)


def test_dup_rate_and_all_sources_used(dataset):
    by_source, truth = dataset
    entities = {e for _, e, _ in truth}
    assert len(truth) - len(entities) == round(3000 * 0.25)
    assert all(by_source[s] for s in gen.SOURCES)


def _conflicts(a, b):
    """True if both emails and both phones are present and each pair differs."""
    if not (a["email"] and b["email"] and a["phone"] and b["phone"]):
        return False
    email_differs = gen._gmail_key(a["email"]) != gen._gmail_key(b["email"])
    phone_differs = (
        re.sub(r"\D", "", a["phone"])[-10:] != re.sub(r"\D", "", b["phone"])[-10:]
    )
    return email_differs and phone_differs


def test_only_hard_dups_conflict_on_hard_identifiers(dataset):
    by_source, truth = dataset
    rows = {r["record_id"]: r for rs in by_source.values() for r in rs}
    kinds = {rid: kind for rid, _, kind in truth}
    groups = defaultdict(list)
    for rid, eid, _ in truth:
        groups[eid].append(rows[rid])
    for recs in groups.values():
        for i, a in enumerate(recs):
            for b in recs[i + 1 :]:
                if _conflicts(a, b):
                    pair = (kinds[a["record_id"]], kinds[b["record_id"]])
                    assert "hard_dup" in pair or pair == ("drift_chain",) * 2, pair


def test_hard_dups_are_about_two_percent_and_really_conflict(dataset):
    by_source, truth = dataset
    rows = {r["record_id"]: r for rs in by_source.values() for r in rs}
    n_dups = sum(k not in ("original", "household_same_name") for _, _, k in truth)
    hard = [(rid, eid) for rid, eid, k in truth if k == "hard_dup"]
    assert 0.005 < len(hard) / n_dups < 0.04
    kinds = {k for _, _, k in truth}
    assert {"original", "dup", "hard_dup", "drift_chain"} <= kinds
    assert kinds <= {
        "original",
        "dup",
        "hard_dup",
        "drift_chain",
        "household_same_name",
    }
    originals = {eid: rows[rid] for rid, eid, k in truth if k == "original"}
    for rid, eid in hard:
        assert rows[rid]["email"] and rows[rid]["phone"]
        o = originals[eid]
        if o["email"] and o["phone"]:
            assert _conflicts(rows[rid], o)


def _same_phone(a, b):
    return re.sub(r"\D", "", a["phone"])[-10:] == re.sub(r"\D", "", b["phone"])[-10:]


def test_drift_chains_link_ends_only_through_the_middle(dataset):
    by_source, truth = dataset
    rows = {r["record_id"]: r for rs in by_source.values() for r in rs}
    chains = defaultdict(list)
    for rid, eid, kind in truth:
        if kind == "drift_chain":
            chains[eid].append(rows[rid])
    n_entities = len({e for _, e, _ in truth})
    assert 0.01 < len(chains) / n_entities < 0.03
    other_kinds = {k for _, e, k in truth if e in chains and k != "drift_chain"}
    assert other_kinds == set()  # exactly 3 records, all labelled drift_chain
    for recs in chains.values():
        assert len(recs) == 3
        assert all(r["email"] and r["phone"] for r in recs)
        first, middle, last = sorted(recs, key=lambda r: r["updated_at"])
        assert first["updated_at"] < middle["updated_at"] < last["updated_at"]
        assert gen._gmail_key(first["email"]) == gen._gmail_key(middle["email"])
        assert not _same_phone(first, middle)  # phone changed 1 -> 2
        assert _same_phone(middle, last)
        assert gen._gmail_key(middle["email"]) != gen._gmail_key(last["email"])
        assert _conflicts(first, last)  # 1 and 3 share nothing


def test_duplicates_are_corrupted_in_each_documented_way(dataset):
    by_source, truth = dataset
    rows = {r["record_id"]: r for rs in by_source.values() for r in rs}
    groups = defaultdict(list)
    for rid, eid, _ in truth:
        groups[eid].append(rows[rid])
    dups = [g for g in groups.values() if len(g) > 1]
    flat = [r for g in dups for r in g]
    assert any(
        r["email"].lower().endswith("@googlemail.com") or "+" in r["email"]
        for r in flat
    )  # gmail variants
    assert any(
        re.search(r"\b(St|Ave|Blvd|Rd|Dr|Ln|Ct)\b", r["address"]) for r in flat
    )  # abbreviations
    assert any(r["phone"].startswith("(") for r in flat) and any(
        "." in r["phone"] for r in flat
    )  # phone formats
    assert any(r["address"] == "" for r in flat) and any(
        r["dob"] == "" for r in flat
    )  # missing fields
    assert any(
        g[0]["first_name"].strip().lower() != g[1]["first_name"].strip().lower()
        for g in dups
    )  # names differ
    assert any(
        not r["first_name"].isascii() or not r["last_name"].isascii()
        for rs in by_source.values()
        for r in rs
    )


def test_shared_id_households_reuse_host_identifier(monkeypatch):
    """Hard negatives: a family member with a different first name who shares the
    household phone or email (forced on for every household here)."""
    monkeypatch.setattr(gen, "P_SHARED_ID", 1.0)
    g = gen.Generator(seed=3)
    host = g.make_entity(1)
    kinds = set()
    for i in range(2, 40):
        member = g.make_entity(i, household_of=host)
        assert member.last == host.last and member.street == host.street
        assert member.first.lower() != host.first.lower()
        shares_phone, shares_email = (
            member.phone == host.phone,
            member.email == host.email,
        )
        assert shares_phone != shares_email  # exactly one identifier is shared
        kinds.add("phone" if shares_phone else "email")
    assert kinds == {"phone", "email"}


def test_rejects_bad_args():
    with pytest.raises(ValueError):
        gen.generate(0, 0.25, 1)
    with pytest.raises(ValueError):
        gen.generate(10, 1.0, 1)


def test_same_name_child_shares_name_and_one_identifier_not_dob(monkeypatch):
    monkeypatch.setattr(gen, "P_SAME_NAME", 1.0)
    g = gen.Generator(5)
    for i in range(50):
        parent = g.make_entity(2 * i + 1)
        child = g.make_entity(2 * i + 2, household_of=parent)
        assert child.same_name_as == parent.eid
        assert (child.first, child.last) == (parent.first, parent.last)
        assert (child.phone == parent.phone) != (child.email == parent.email)
        assert abs((child.dob - parent.dob).days) >= 18 * 365
        assert child.dob <= gen.LATEST_DOB


def test_same_name_children_are_single_labelled_records(monkeypatch):
    monkeypatch.setattr(gen, "P_SAME_NAME", 1.0)  # every household member is a Jr.
    _, truth = gen.generate(3000, 0.25, 11)
    by_entity = defaultdict(list)
    for rid, eid, kind in truth:
        by_entity[eid].append(kind)
    children = [
        eid for eid, kinds in by_entity.items() if "household_same_name" in kinds
    ]
    assert len(children) > 20
    assert all(by_entity[eid] == ["household_same_name"] for eid in children)
