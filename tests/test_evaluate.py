"""Milestone 6: evaluation metrics on small hand-built cases."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_path = Path(__file__).parents[1] / "eval" / "evaluate.py"
_spec = importlib.util.spec_from_file_location("evaluate", _path)
assert _spec and _spec.loader
ev = importlib.util.module_from_spec(_spec)
sys.modules["evaluate"] = ev  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(ev)

# E1: ordinary entity of 3 records. E2: original + hard_dup. E3: drift chain.
# E4, E5: singletons.
TRUTH_ROWS = [
    ("A1", "E1", "original"),
    ("A2", "E1", "dup"),
    ("A3", "E1", "dup"),
    ("B1", "E2", "original"),
    ("B2", "E2", "hard_dup"),
    ("C1", "E3", "drift_chain"),
    ("C2", "E3", "drift_chain"),
    ("C3", "E3", "drift_chain"),
    ("D1", "E4", "original"),
    ("F1", "E5", "original"),
]


@pytest.fixture
def truth():
    return ev.load_truth(TRUTH_ROWS)


def test_true_pairs_split_by_kind(truth):
    assert truth.pairs["ordinary"] == {("A1", "A2"), ("A1", "A3"), ("A2", "A3")}
    assert truth.pairs["hard_dup"] == {("B1", "B2")}
    assert truth.pairs["drift_chain"] == {("C1", "C2"), ("C1", "C3"), ("C2", "C3")}
    assert truth.kind(("A1", "D1")) is None


def test_perfect_clustering_of_mergeable_records(truth):
    # What the safety rules allow at best: hard_dup apart, drift chain split.
    clusters = [
        ["A1", "A2", "A3"],
        ["B1"],
        ["B2"],
        ["C1", "C2"],
        ["C3"],
        ["D1"],
        ["F1"],
    ]
    pw = ev.pairwise_metrics(clusters, truth)
    assert pw.precision == 1.0
    assert pw.kind_recall("ordinary") == 1.0
    assert pw.kind_recall("hard_dup") == 0.0
    assert pw.kind_recall("drift_chain") == pytest.approx(1 / 3)
    assert pw.recall == pytest.approx(4 / 7)
    assert pw.false_positives == 0


def test_merged_hard_dup_is_not_a_true_positive(truth):
    clusters = [
        ["A1", "A2", "A3"],
        ["B1", "B2"],
        ["C1"],
        ["C2"],
        ["C3"],
        ["D1"],
        ["F1"],
    ]
    pw = ev.pairwise_metrics(clusters, truth)
    assert pw.found["hard_dup"] == 1
    assert pw.true_positives == 3
    assert pw.precision == pytest.approx(3 / 4)
    assert pw.false_positives == 0


def test_wrong_merge_counts_as_false_positive_and_mixed_cluster(truth):
    clusters = [
        ["A1", "A2", "A3", "D1"],
        ["B1"],
        ["B2"],
        ["C1"],
        ["C2"],
        ["C3"],
        ["F1"],
    ]
    pw = ev.pairwise_metrics(clusters, truth)
    assert pw.false_positives == 3
    assert pw.precision == pytest.approx(3 / 6)
    assert ev.cluster_metrics(clusters, truth).mixed == 1


def test_cluster_metrics_exact_match(truth):
    clusters = [["A1", "A2"], ["A3"], ["B1", "B2"], ["C1", "C2", "C3"], ["D1"], ["F1"]]
    every = ev.cluster_metrics(clusters, truth)
    assert (every.predicted, every.true, every.exact, every.mixed) == (6, 5, 4, 0)
    multi = ev.cluster_metrics(clusters, truth, min_size=2)
    # Predicted multi-record clusters: A1A2 (wrong), B, C. True multi: E1, E2, E3.
    assert (multi.predicted, multi.true, multi.exact) == (3, 3, 2)
    assert multi.precision == multi.recall == pytest.approx(2 / 3)


def test_blocking_completeness_by_kind(truth):
    candidates = {("A1", "A2"), ("B1", "B2"), ("C1", "C2"), ("C2", "C3"), ("A1", "D1")}
    out = ev.blocking_completeness(candidates, truth)
    assert out["ordinary"] == (1, 3)
    assert out["hard_dup"] == (1, 1)
    assert out["drift_chain"] == (2, 3)
    assert out["all"] == (4, 7)


def test_clusters_from_cluster_ids():
    cluster_of = {"A1": "A1", "A2": "A1", "B1": "B1"}
    assert ev.clusters_from(cluster_of) == [["A1", "A2"], ["B1"]]


HEADER = "record_id,source,first_name,last_name,email,email_verified,phone,address,city,state,zip,dob,updated_at\n"
DUSTIN = "R1,crm,Dustin,Smith,dustin@example.com,true,718-667-2532,768 Stanley Isle,Brooklyn,NY,11201,1990-01-01,2025-01-01"


def _household(tmp_path, destiny_email):
    sources = tmp_path / "sources"
    sources.mkdir()
    destiny = f"R2,crm,Destiny,Smith,{destiny_email},true,718-667-2532,768 Stanley Isle,Brooklyn,NY,11201,1992-02-02,2025-01-01"
    (sources / "crm.csv").write_text(f"{HEADER}{DUSTIN}\n{destiny}\n", encoding="utf-8")
    prep = ev.prepare(sources)
    assert prep.pairs == [("R1", "R2")]
    return prep, ev.load_truth([("R1", "E1", "original"), ("R2", "E2", "original")])


def test_sweep_point_counts_households_and_review(tmp_path):
    """End to end on a tiny dataset: a household sharing a phone (and only one email
    on file) is merged only when the threshold is below the first-name similarity."""
    from resolver.scoring import load_config

    prep, truth = _household(tmp_path, destiny_email="")
    config = load_config()
    low = ev.sweep_point(0, 0.50, prep, config, truth)
    high = ev.sweep_point(0, 0.95, prep, config, truth)
    assert (low.households, low.households_merged, low.mixed_clusters) == (1, 1, 1)
    assert (high.households, high.households_merged, high.mixed_clusters) == (1, 0, 0)
    assert high.exact_id_dups == 0


def test_household_with_distinct_emails_never_merges(tmp_path):
    from resolver.scoring import load_config

    prep, truth = _household(tmp_path, destiny_email="destiny@example.com")
    low = ev.sweep_point(0, 0.50, prep, load_config(), truth)
    assert (low.households, low.households_merged, low.mixed_clusters) == (1, 0, 0)


def test_same_name_outcome_counts_jr_sr_pairs(tmp_path):
    from resolver.merge import cluster_matches
    from resolver.scoring import load_config

    sources = tmp_path / "sources"
    sources.mkdir()
    rows = [
        "R1,crm,Carol,Wade,carol.wade@example.com,true,212-555-0142,1 Elm St,Troy,NY,12180,1958-03-14,2025-01-01",
        "R2,crm,Carol,Wade,cwade91@example.com,false,212-555-0142,1 Elm St,Troy,NY,12180,1991-06-02,2025-01-01",
    ]
    (sources / "crm.csv").write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    truth = ev.load_truth(
        [("R1", "E1", "original"), ("R2", "E2", "household_same_name")]
    )
    assert truth.same_name == {"R2"}
    prep = ev.prepare(sources)
    results = ev.decide(prep, load_config())
    clusters = cluster_matches(prep.records, results).clusters
    outcome = ev.same_name_outcome(prep, results, clusters, truth)
    assert outcome.children == 1
    assert outcome.rules == {"REVIEW/exact_id_dob_mismatch": 1}
    assert outcome.merged == 0
    # Had they been merged, the child would count as merged.
    assert ev.same_name_outcome(prep, results, [["R1", "R2"]], truth).merged == 1
