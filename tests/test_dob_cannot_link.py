"""A birth-date mismatch keeps two same-name records apart in clustering (v1.1.0).

Project 02 found the gap: a parent and child with the same name and different
DOBs are scored REVIEW, but a third record without a DOB can exact-match both on
the shared email or phone and bridge them into one cluster.
"""

from __future__ import annotations

from resolver.merge import HUMAN_RULE, cluster_matches
from resolver.scoring import (
    Decision,
    NormalizedRecord,
    PairResult,
    Thresholds,
    normalize_record,
    score_pair,
)


def _rec(rid: str, **fields: str) -> NormalizedRecord:
    return normalize_record(
        {"record_id": rid, "first_name": "James", "last_name": "Carter", **fields}
    )


# SR and JR share the household email; F (a form fill) has the email and no DOB.
BRIDGE: dict[str, NormalizedRecord] = {
    "SR": _rec("SR", email="carter.home@example.com", dob="1961-04-02"),
    "JR": _rec("JR", email="carter.home@example.com", dob="1990-08-15"),
    "F": _rec("F", email="carter.home@example.com"),
}


def _scored(records: dict[str, NormalizedRecord]) -> list[PairResult]:
    ids = sorted(records)
    t = Thresholds()
    return [
        score_pair(records[a], records[b], t)
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
    ]


def test_scoring_already_flags_the_direct_pair() -> None:
    by_pair = {(r.left_id, r.right_id): r for r in _scored(BRIDGE)}
    assert by_pair[("JR", "SR")].decision is Decision.REVIEW
    assert by_pair[("JR", "SR")].rule == "exact_id_dob_mismatch"
    assert by_pair[("F", "JR")].decision is Decision.MATCH
    assert by_pair[("F", "SR")].decision is Decision.MATCH


def test_bridge_record_cannot_join_a_dob_mismatch() -> None:
    clustering = cluster_matches(BRIDGE, _scored(BRIDGE))
    cluster_of = clustering.cluster_of()
    assert cluster_of["SR"] != cluster_of["JR"]
    [split] = clustering.splits
    assert split.conflicts == []
    assert split.dob_mismatches == [("JR", "SR")]
    assert len(split.rejected_edges) == 1


def test_different_first_names_are_not_a_dob_cannot_link() -> None:
    records = dict(BRIDGE)
    records["JR"] = normalize_record(
        {
            "record_id": "JR",
            "first_name": "Jamie",
            "last_name": "Carter",
            "email": "carter.home@example.com",
            "dob": "1990-08-15",
        }
    )
    edges = [
        PairResult("F", "JR", 1.0, Decision.MATCH, rule="exact_id"),
        PairResult("F", "SR", 1.0, Decision.MATCH, rule="exact_id"),
    ]
    clustering = cluster_matches(records, edges)
    assert clustering.splits == []


def test_same_dob_is_not_a_cannot_link() -> None:
    records = dict(BRIDGE)
    records["JR"] = _rec("JR", email="carter.home@example.com", dob="1961-04-02")
    clustering = cluster_matches(records, _scored(records))
    assert clustering.splits == []
    assert clustering.clusters == [["F", "JR", "SR"]]


def test_reviewer_approval_of_the_exact_pair_lifts_it() -> None:
    results = _scored(BRIDGE)
    [direct] = [r for r in results if (r.left_id, r.right_id) == ("JR", "SR")]
    human = PairResult(
        direct.left_id, direct.right_id, direct.score, Decision.MATCH, rule=HUMAN_RULE
    )
    clustering = cluster_matches(BRIDGE, results, human_links=[human])
    assert clustering.clusters == [["F", "JR", "SR"]]
    assert clustering.splits == []


def test_reviewer_approval_never_lifts_an_identifier_conflict() -> None:
    records = {
        "SR": _rec("SR", email="a@example.com", phone="310 555 0100", dob="1961-04-02"),
        "JR": _rec("JR", email="b@example.com", phone="310 555 0199", dob="1990-08-15"),
    }
    human = PairResult("JR", "SR", 0.5, Decision.MATCH, rule=HUMAN_RULE)
    clustering = cluster_matches(records, [], human_links=[human])
    assert clustering.cluster_of()["SR"] != clustering.cluster_of()["JR"]


def test_dob_split_is_described_and_a_split_reject_is_refused() -> None:
    from dataclasses import asdict

    from resolver.review import describe_split, plan_decisions

    results = _scored(BRIDGE)
    [split] = cluster_matches(BRIDGE, results).splits
    assert "different birth dates" in describe_split(asdict(split))
    row = {
        "item_id": "S:F",
        "item_type": "split",
        "record_ids": ";".join(split.component),
        "decision": "reject",
        "outcome": "split_wrong",
        "decided_at": "2026-10-23T00:00:00+00:00",
    }
    plan = plan_decisions({"S:F": row}, results, [split])
    assert plan.report["S:F"]["status"] == "refused_dob_rule"
    assert plan.human_links == []
