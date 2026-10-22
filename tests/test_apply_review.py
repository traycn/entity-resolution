"""Applying review decisions back into the clusters."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from resolver.cli import app
from resolver.merge import HUMAN_RULE, SplitInfo, cluster_matches, merge
from resolver.review import finalize_report, plan_decisions
from resolver.scoring import Decision, NormalizedRecord, PairResult, normalize_record

# A shares its email with B, B shares its phone with C, A and C differ on both.
CHAIN: dict[str, NormalizedRecord] = {
    rid: normalize_record({"record_id": rid, "first_name": "Maria", "last_name": "Garcia", **ids})
    for rid, ids in {
        "A": {"email": "maria.g@example.com", "phone": "310 555 0100"},
        "B": {"email": "maria.g@example.com", "phone": "213 555 0199"},
        "C": {"email": "mgarcia@example.org", "phone": "213-555-0199"},
        "D": {"email": "d@example.com"},
        "E": {"email": "e@example.com"},
    }.items()
}  # fmt: skip


def _edge(a: str, b: str, score: float = 1.0, rule: str = "exact_id") -> PairResult:
    return PairResult(a, b, score, Decision.MATCH, rule=rule)


def test_approved_pair_joins_two_clusters():
    human = _edge("D", "E", 0.8, HUMAN_RULE)
    clustering = cluster_matches(CHAIN, [], human_links=[human])
    assert ["D", "E"] in clustering.clusters
    assert clustering.edges[("D", "E")].rule == HUMAN_RULE


def test_rejected_pair_splits_an_automatic_component():
    # D-E linked automatically, a reviewer says different people.
    clustering = cluster_matches(CHAIN, [_edge("D", "E")], cannot_link=[("E", "D")])
    assert ["D"] in clustering.clusters and ["E"] in clustering.clusters


def test_rejected_pair_cuts_a_transitive_link():
    records = {k: CHAIN[k] for k in ("A", "B", "D")}
    edges = [_edge("A", "B"), _edge("B", "D", 0.9)]
    clustering = cluster_matches(records, edges, cannot_link=[("A", "D")])
    [split] = clustering.splits
    assert split.conflicts == [] and split.human_rejected == [("A", "D")]
    assert split.rejected_edges == [("B", "D")]  # the weaker edge goes
    assert clustering.clusters == [["A", "B"], ["D"]]


def test_reviewer_link_wins_over_a_stronger_automatic_edge():
    # Without review the stronger A-B edge is kept and B-C cut. A reviewer approving
    # B-C reverses that; the conflict between A and C still keeps them apart.
    automatic = cluster_matches(CHAIN, [_edge("A", "B", 1.0), _edge("B", "C", 0.9)])
    assert ["A", "B"] in automatic.clusters
    reviewed = cluster_matches(
        CHAIN, [_edge("A", "B", 1.0)], human_links=[_edge("B", "C", 0.5, HUMAN_RULE)]
    )
    assert ["B", "C"] in reviewed.clusters and ["A"] in reviewed.clusters


def _row(item_id: str, item_type: str, ids: str, outcome: str) -> dict[str, str]:
    decision = "approve" if outcome in ("same_entity", "split_correct") else "reject"
    return {
        "item_id": item_id,
        "item_type": item_type,
        "record_ids": ids,
        "decision": decision,
        "outcome": outcome,
        "decided_at": "2026-10-20T00:00:00+00:00",
    }


def test_plan_and_report_every_status():
    review = [
        PairResult("D", "E", 0.8, Decision.REVIEW, rule="weighted"),
        PairResult("A", "D", 0.8, Decision.REVIEW, rule="weighted"),
    ]
    split = SplitInfo(["A", "B", "C"], [("A", "C")], [("A", "B")], [["A"], ["B", "C"]])
    rows = {
        "P:D:E": _row("P:D:E", "pair", "D;E", "same_entity"),
        "P:A:D": _row("P:A:D", "pair", "A;D", "different_entities"),
        "P:X:Y": _row("P:X:Y", "pair", "X;Y", "same_entity"),
        "S:A": _row("S:A", "split", "A;B;C", "split_wrong"),
        "S:A2": _row("S:A2", "split", "C;B;A", "split_correct"),
        "S:Q": _row("S:Q", "split", "Q;R", "split_correct"),
    }
    plan = plan_decisions(rows, review, [split])
    assert [(r.left_id, r.right_id, r.rule) for r in plan.human_links] == [
        ("D", "E", HUMAN_RULE)
    ]
    assert plan.cannot_link == [("A", "D")]
    result = merge(
        {k: {"record_id": k} for k in CHAIN},
        CHAIN,
        [_edge("A", "B"), _edge("B", "C", 0.9)],
        human_links=plan.human_links,
        cannot_link=plan.cannot_link,
    )
    automatic = {k: k for k in CHAIN}
    report = {
        r["item_id"]: r
        for r in finalize_report(plan, automatic, result.clustering.cluster_of())
    }
    assert {k: r["status"] for k, r in report.items()} == {
        "P:D:E": "merged",
        "P:A:D": "already_apart",
        "P:X:Y": "stale",
        "S:A": "refused_safety_rule",
        "S:A2": "split_kept",
        "S:Q": "stale",
    }


COLUMNS = [
    "record_id", "source", "first_name", "last_name", "email", "email_verified",
    "phone", "address", "city", "state", "zip", "dob", "updated_at",
]  # fmt: skip
DECISION_COLUMNS = [
    "decided_at",
    "item_id",
    "item_type",
    "rule",
    "record_ids",
    "decision",
    "outcome",
]


def test_resolver_run_applies_decisions(tmp_path: Path):
    sources = tmp_path / "sources"
    sources.mkdir()
    rows = [
        ["R1", "crm", "Maria", "Garcia", "maria.g@example.com", "true", "310 555 0100", "", "", "", "", "", "2023-01-01"],
        ["R2", "web", "Maria", "Garcia", "maria.g@example.com", "false", "213 555 0199", "", "", "", "", "", "2024-01-01"],
        ["R3", "ads", "Maria", "Garcia", "mgarcia@example.org", "false", "213-555-0199", "", "", "", "", "", "2025-01-01"],
        ["R4", "crm", "Kenneth", "Lopez", "kl@example.com", "true", "415 555 0123", "", "", "", "", "", "2024-01-01"],
        ["R5", "web", "Jeremy", "Lopez", "jl@example.com", "false", "415-555-0123", "", "", "", "", "", "2024-01-01"],
    ]  # fmt: skip
    with (sources / "crm.csv").open("w", newline="") as fh:
        csv.writer(fh).writerows([COLUMNS, *rows])
    decisions = tmp_path / "decisions.csv"
    with decisions.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(DECISION_COLUMNS)
        # an earlier reject, then an approve: the latest row wins
        writer.writerow(
            [
                "2026-10-20T00:00:00+00:00",
                "P:R4:R5",
                "pair",
                "shared_identifier",
                "R4;R5",
                "reject",
                "different_entities",
            ]
        )
        writer.writerow(
            [
                "2026-10-20T00:01:00+00:00",
                "P:R4:R5",
                "pair",
                "shared_identifier",
                "R4;R5",
                "approve",
                "same_entity",
            ]
        )
        writer.writerow(
            [
                "2026-10-20T00:02:00+00:00",
                "S:R1",
                "split",
                "conflict_split",
                "R1;R2;R3",
                "reject",
                "split_wrong",
            ]
        )
        writer.writerow(["2026-10-20T00:03:00+00:00", "P:R1:R9", "pair", "weighted", "R1;R9", "approve", "same_entity"])  # fmt: skip

    out = tmp_path / "reviewed"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--sources",
            str(sources),
            "--out",
            str(out),
            "--decisions",
            str(decisions),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "merged=1, refused_safety_rule=1, stale=1" in result.output

    with (out / "clusters.csv").open() as fh:
        clusters = {r["record_id"]: r["cluster_id"] for r in csv.DictReader(fh)}
    assert clusters["R4"] == clusters["R5"]  # approved household pair merged
    assert clusters["R1"] != clusters["R3"]  # split_wrong refused: still apart
    audit = [
        json.loads(line) for line in (out / "audit.jsonl").read_text().splitlines()
    ]
    human = [e for entry in audit for e in entry["edges"] if e["rule"] == HUMAN_RULE]
    assert [(e["left_id"], e["right_id"]) for e in human] == [("R4", "R5")]
    assert human[0]["field_scores"]["phone"] == 1.0  # the scores stay in the audit
    applied = {
        r["item_id"]: r["status"]
        for r in map(
            json.loads, (out / "decisions_applied.jsonl").read_text().splitlines()
        )
    }
    assert applied == {
        "P:R4:R5": "merged",
        "S:R1": "refused_safety_rule",
        "P:R1:R9": "stale",
    }
