from pathlib import Path

from resolver.merge import (
    RawRecord,
    build_golden,
    cluster_matches,
    merge,
    read_audit_log,
    unmerge,
    write_audit_log,
)
from resolver.scoring import (
    Decision,
    NormalizedRecord,
    PairResult,
    Thresholds,
    has_conflicting_identifiers,
    normalize_record,
    score_pair,
)


def _raw(rid: str, **fields: str) -> RawRecord:
    row: RawRecord = {
        "record_id": rid,
        "source": "crm",
        "first_name": "Maria",
        "last_name": "Garcia",
        "email": "",
        "email_verified": "false",
        "phone": "",
        "address": "",
        "city": "",
        "state": "",
        "zip": "",
        "dob": "",
        "updated_at": "2024-01-01",
    }
    row.update(fields)
    return row


def _match(a: str, b: str, score: float = 1.0) -> PairResult:
    return PairResult(a, b, score, Decision.MATCH, rule="exact_id")


def _normalized(raws: list[RawRecord]) -> dict[str, NormalizedRecord]:
    return {str(r["record_id"]): normalize_record(r) for r in raws}


# A shares its email with B, B shares its phone with C, but A and C differ on both.
CHAIN = [
    _raw("A", email="maria.g@example.com", phone="310 555 0100"),
    _raw("B", email="maria.g@example.com", phone="213 555 0199"),
    _raw("C", email="mgarcia@example.org", phone="213-555-0199"),
]


def test_chain_with_conflicting_ends_is_split() -> None:
    records = _normalized(CHAIN)
    assert has_conflicting_identifiers(records["A"], records["C"])
    results = [
        score_pair(records[a], records[b], Thresholds()) for a, b in ("AB", "BC", "AC")
    ]
    assert [r.decision for r in results] == [
        Decision.MATCH,
        Decision.MATCH,
        Decision.NO_MATCH,
    ]

    clustering = cluster_matches(records, results)

    for cluster in clustering.clusters:
        assert not {"A", "C"} <= set(cluster)
    assert sorted(len(c) for c in clustering.clusters) == [1, 2]
    [split] = clustering.splits
    assert split.component == ["A", "B", "C"]
    assert split.conflicts == [("A", "C")]
    assert len(split.rejected_edges) == 1


def test_split_keeps_the_stronger_edge() -> None:
    records = _normalized(CHAIN)
    clustering = cluster_matches(
        records, [_match("A", "B", 0.7), _match("B", "C", 0.9)]
    )
    assert clustering.clusters == [["A"], ["B", "C"]]
    assert clustering.splits[0].rejected_edges == [("A", "B")]


def test_conflict_three_hops_apart_is_split() -> None:
    raws = [
        _raw("A", email="a@example.com", phone="310 555 0100"),
        _raw("B", email="a@example.com"),
        _raw("C", phone="213 555 0199"),
        _raw("D", email="d@example.com", phone="213 555 0199"),
    ]
    edges = [_match("A", "B"), _match("B", "C"), _match("C", "D")]
    clustering = cluster_matches(_normalized(raws), edges)
    assert len(clustering.clusters) == 2
    assert all(not {"A", "D"} <= set(c) for c in clustering.clusters)


def test_component_without_conflict_is_kept_whole() -> None:
    raws = [
        _raw("A", email="m@example.com"),
        _raw("B", email="m@example.com", phone="310 555 0100"),
        _raw("C", phone="310 555 0100"),
        _raw("D"),
    ]
    clustering = cluster_matches(
        _normalized(raws), [_match("A", "B"), _match("B", "C")]
    )
    assert clustering.clusters == [["A", "B", "C"], ["D"]]
    assert clustering.splits == []
    assert set(clustering.edges) == {("A", "B"), ("B", "C")}


def test_non_match_edges_are_ignored() -> None:
    raws = [_raw("A"), _raw("B")]
    review = PairResult("A", "B", 0.8, Decision.REVIEW, rule="weighted")
    assert cluster_matches(_normalized(raws), [review]).clusters == [["A"], ["B"]]


def test_survivorship_prefers_verified_then_most_recent() -> None:
    golden = build_golden(
        "G-1",
        [
            _raw(
                "old_verified",
                email="v@example.com",
                email_verified="true",
                updated_at="2020-01-01",
                address="1 Old Rd",
                city="X",
                zip="11111",
            ),
            _raw(
                "new",
                email="new@example.com",
                updated_at="2025-06-01",
                phone="(310) 555-0100",
                address="9 New St",
                city="Y",
                zip="22222",
            ),
            _raw("newest_sparse", updated_at="2025-12-31", first_name="Mari"),
        ],
    )
    values, lineage = golden["values"], golden["lineage"]
    # Verified outranks recency for every group it has.
    assert values["email"] == "v@example.com"
    assert values["email_verified"] == "true"
    assert lineage["email"] == "old_verified"
    assert (values["address"], values["city"], values["zip"]) == (
        "1 Old Rd",
        "X",
        "11111",
    )
    assert lineage["zip"] == "old_verified"
    # Missing on the verified record -> most recent non-null among the rest.
    assert values["phone"] == "+13105550100"
    assert lineage["phone"] == "new"
    assert values["first_name"] == "Maria"  # verified record has a name
    assert values["dob"] is None and lineage["dob"] is None
    assert golden["members"] == ["new", "newest_sparse", "old_verified"]


def test_survivorship_recency_among_unverified() -> None:
    golden = build_golden(
        "G-1",
        [
            _raw("r1", updated_at="2023-05-01", dob="01/02/1980", first_name="Bob"),
            _raw("r2", updated_at="2024-05-01", dob="", first_name="Robert"),
        ],
    )
    assert golden["values"]["first_name"] == "Robert"
    assert golden["lineage"]["first_name"] == "r2"
    assert golden["values"]["dob"] == "1980-01-02"  # newest record has no dob
    assert golden["lineage"]["dob"] == "r1"


def test_golden_email_keeps_the_address_as_written() -> None:
    golden = build_golden("G-1", [_raw("r1", email="Jules.Guyon+promo@gmail.com")])
    assert golden["values"]["email"] == "Jules.Guyon+promo@gmail.com"


def test_invalid_value_counts_as_missing() -> None:
    golden = build_golden(
        "G-1",
        [
            _raw("r1", updated_at="2025-01-01", phone="12"),
            _raw("r2", updated_at="2020-01-01", phone="310 555 0100"),
        ],
    )
    assert golden["lineage"]["phone"] == "r2"
    golden = build_golden(
        "G-1",
        [
            _raw("r1", updated_at="2025-01-01", email="not-an-email"),
            _raw("r2", updated_at="2020-01-01", email="m@example.com"),
        ],
    )
    assert golden["lineage"]["email"] == "r2"


def _merged() -> dict[str, RawRecord]:
    raws = [
        _raw("A", email="maria.g@example.com", phone="310 555 0100"),
        _raw("B", email="Maria.G@example.com", updated_at="2025-01-01"),
        _raw("C", first_name="Jane", last_name="Doe"),
    ]
    return {str(r["record_id"]): r for r in raws}


def test_audit_log_records_every_merge(tmp_path: Path) -> None:
    raw = _merged()
    records = _normalized(list(raw.values()))
    result = score_pair(records["A"], records["B"], Thresholds())
    out = merge(raw, records, [result])

    assert set(out.golden) == {"G-A", "G-C"}
    [entry] = out.audit
    assert entry["members"] == ["A", "B"]
    assert entry["edges"][0]["rule"] == "exact_id"
    assert entry["edges"][0]["field_scores"]["email"] == 1.0

    path = tmp_path / "audit.jsonl"
    write_audit_log(path, out.audit)
    assert read_audit_log(path) == out.audit


def test_unmerge_restores_original_records(tmp_path: Path) -> None:
    raw = _merged()
    snapshot = {k: dict(v) for k, v in raw.items()}
    records = _normalized(list(raw.values()))
    out = merge(raw, records, [score_pair(records["A"], records["B"], Thresholds())])
    path = tmp_path / "audit.jsonl"
    write_audit_log(path, out.audit)
    [entry] = read_audit_log(path)

    golden, originals = unmerge(out.golden, entry)

    assert originals == [snapshot["A"], snapshot["B"]]
    # Same golden set as if A and B had never matched.
    unmerged = merge(snapshot, records, [])
    assert golden == unmerged.golden
    assert unmerged.audit == []
    assert raw == snapshot  # merging never mutates the inputs
