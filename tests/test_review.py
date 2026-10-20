"""Milestone 7: review queue, plain-word rules, decisions CSV and the API."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from resolver import review, review_api
from resolver.cli import app as cli_app


def _expl(rule: str, **scores: float) -> dict[str, object]:
    return {"rule": rule, "score": 0.8, "field_scores": scores}


@pytest.mark.parametrize(
    ("explanation", "expected"),
    [
        (
            _expl("shared_identifier", phone=1.0, email=0.4),
            "Same phone, different first names",
        ),
        (_expl("shared_identifier", email=1.0), "Same email, different first names"),
        (
            _expl("exact_id_dob_mismatch", phone=1.0, email=1.0),
            "Same email and phone and same first name, different dates of birth",
        ),
        (
            _expl("exact_id_other_mismatch", phone=1.0, email=0.5),
            "Same phone, different emails; first names similar but not identical",
        ),
        (
            _expl("exact_id_no_name", email=1.0),
            "Same email, but a first name is missing",
        ),
        (
            _expl("weighted_id_mismatch", email=0.93, name=1.0),
            "Very similar records (score 0.80), but the email differ",
        ),
        (
            _expl("weighted_low_evidence", name=1.0, address=1.0),
            "Name and address agree, but too little else to compare",
        ),
        (
            _expl("weighted", name=0.9, address=0.7, email=0.3),
            "No shared email or phone; similar name and address (score 0.80)",
        ),
    ],
)
def test_describe_pair(explanation, expected):
    assert review.describe_pair(explanation) == expected


def test_every_review_rule_has_a_group_title():
    for rule in (
        "shared_identifier",
        "exact_id_dob_mismatch",
        "exact_id_other_mismatch",
        "exact_id_no_name",
        "weighted",
        "weighted_low_evidence",
        "weighted_id_mismatch",
        "weighted_match_disabled",
    ):
        assert rule in review.GROUP_TITLES


def test_describe_split():
    split = {
        "component": ["A", "B", "C"],
        "conflicts": [["A", "C"]],
        "clusters": [["A"], ["B", "C"]],
    }
    assert review.describe_split(split) == (
        "3 records linked by exact matches, but A and C differ on both email and "
        "phone; split into 2 clusters"
    )


def test_compare_fields_uses_normalized_values():
    rows = review.compare_fields(
        [
            {"first_name": "Bob", "phone": "(310) 555-0100", "email": "a@x.com", "dob": ""},
            {"first_name": "ROBERT", "phone": "310.555.0100", "email": "b@x.com", "dob": "1990-01-01"},
        ]
    )  # fmt: skip
    by = {r["field"]: r["agreement"] for r in rows}
    assert by["first_name"] == "same"  # nickname-normalized
    assert by["phone"] == "same"
    assert by["email"] == "different"
    assert by["dob"] == "missing"


RECORDS = {
    rid: {"record_id": rid, "first_name": "Ana", "last_name": "Silva"}
    for rid in ("A", "B", "C", "D", "E")
}
PAIRS = [
    {"left_id": "D", "right_id": "E", **_expl("weighted", name=0.9)},
    {"left_id": "A", "right_id": "D", **_expl("shared_identifier", phone=1.0)},
]
SPLITS = [
    {
        "component": ["A", "B", "C"],
        "conflicts": [["A", "C"]],
        "rejected_edges": [["A", "B"]],
        "clusters": [["A"], ["B", "C"]],
        "edges": [],
    }
]


def test_queue_groups_in_order_with_splits_first():
    queue = review.build_queue(PAIRS, SPLITS, RECORDS)
    assert [i["id"] for i in queue.items] == ["S:A", "P:A:D", "P:D:E"]
    summary = queue.summary()
    assert [g["group"] for g in summary["groups"]] == [
        "conflict_split",
        "shared_identifier",
        "weighted",
    ]
    detail = queue.detail("S:A")
    assert [r["record_id"] for r in detail["records"]] == ["A", "B", "C"]
    assert detail["evidence"]["rejected_edges"] == [["A", "B"]]


def test_decisions_append_and_latest_wins(tmp_path: Path):
    path = tmp_path / "decisions.csv"
    queue = review.build_queue(PAIRS, SPLITS, RECORDS)
    review.record_decision(path, queue, "P:A:D", "approve")
    review.record_decision(path, queue, "P:A:D", "reject")
    review.record_decision(path, queue, "S:A", "approve")
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert [r["outcome"] for r in rows] == [
        "same_entity",
        "different_entities",
        "split_correct",
    ]
    assert rows[0]["record_ids"] == "A;D"
    assert review.read_decisions(path) == {"P:A:D": "reject", "S:A": "approve"}
    with pytest.raises(KeyError):
        review.record_decision(path, queue, "P:nope", "approve")


COLUMNS = [
    "record_id", "source", "first_name", "last_name", "email", "email_verified",
    "phone", "address", "city", "state", "zip", "dob", "updated_at",
]  # fmt: skip


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """`resolver run` on a small fixture, then the API over its output."""
    sources = tmp_path / "sources"
    sources.mkdir()
    rows = [
        # drift chain: R1-R2 share an email, R2-R3 a phone, R1/R3 conflict -> split
        ["R1", "crm", "Maria", "Garcia", "maria.g@example.com", "true", "310 555 0100", "", "", "", "", "", "2023-01-01"],
        ["R2", "web", "Maria", "Garcia", "maria.g@example.com", "false", "213 555 0199", "", "", "", "", "", "2024-01-01"],
        ["R3", "ads", "Maria", "Garcia", "mgarcia@example.org", "false", "213-555-0199", "", "", "", "", "", "2025-01-01"],
        # household: same phone, different first names -> REVIEW shared_identifier
        ["R4", "crm", "Kenneth", "Lopez", "kl@example.com", "true", "415 555 0123", "", "", "", "", "", "2024-01-01"],
        ["R5", "web", "Jeremy", "Lopez", "jl@example.com", "false", "415-555-0123", "", "", "", "", "", "2024-01-01"],
    ]  # fmt: skip
    with (sources / "crm.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    out = tmp_path / "out"
    result = CliRunner().invoke(
        cli_app, ["run", "--sources", str(sources), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    monkeypatch.setenv("RESOLVER_OUT", str(out))
    monkeypatch.setenv("RESOLVER_SOURCES", str(sources))
    monkeypatch.setenv("RESOLVER_DECISIONS", str(tmp_path / "decisions.csv"))
    review_api.get_queue.cache_clear()
    yield TestClient(review_api.app)
    review_api.get_queue.cache_clear()


def test_api_serves_pairs_and_splits_with_evidence(api: TestClient):
    queue = api.get("/api/queue").json()
    assert {g["group"]: g["total"] for g in queue["groups"]} == {
        "conflict_split": 1,
        "shared_identifier": 1,
    }
    split = api.get("/api/items/S:R1").json()
    assert split["kind"] == "split"
    assert split["evidence"]["conflicts"] == [["R1", "R3"]]
    assert len(split["evidence"]["edges"]) == 2  # both MATCH edges, with explanations
    assert {e["rule"] for e in split["evidence"]["edges"]} == {"exact_id"}
    assert {e["description"] for e in split["evidence"]["edges"]} == {
        "Same email, first names agree (1.00)",
        "Same phone, first names agree (1.00)",
    }
    assert all("field_scores" in e for e in split["evidence"]["edges"])
    assert len(split["evidence"]["rejected_edges"]) == 1

    pair = api.get("/api/items/P:R4:R5").json()
    assert pair["title"] == "Same phone, different first names"
    assert pair["evidence"]["field_scores"]["phone"] == 1.0
    assert [r["record_id"] for r in pair["records"]] == ["R4", "R5"]
    fields = {f["field"]: f["agreement"] for f in pair["fields"]}
    assert (fields["phone"], fields["first_name"]) == ("same", "different")


def test_api_records_decisions(api: TestClient, tmp_path: Path):
    r = api.post("/api/decisions", json={"item_id": "P:R4:R5", "decision": "reject"})
    assert r.status_code == 200
    assert r.json()["outcome"] == "different_entities"
    items = {i["id"]: i["decision"] for i in api.get("/api/queue").json()["items"]}
    assert items["P:R4:R5"] == "reject"
    assert (tmp_path / "decisions.csv").read_text().count("\n") == 2  # header + 1
    assert (
        api.post(
            "/api/decisions", json={"item_id": "P:x", "decision": "approve"}
        ).status_code
        == 404
    )
    assert (
        api.post(
            "/api/decisions", json={"item_id": "P:R4:R5", "decision": "maybe"}
        ).status_code
        == 422
    )
    assert api.get("/api/items/nope").status_code == 404
