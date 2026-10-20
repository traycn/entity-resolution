"""Milestone 6: the exact-ID policy comparison."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "eval"))
import compare_policies as cp

from resolver.scoring import Decision

M, R = Decision.MATCH, Decision.REVIEW
OLD, YOUNG = "1958-03-14", "1991-06-02"


@pytest.mark.parametrize(
    ("first_sim", "other_differs", "dobs", "expected"),
    [
        # similar names, other identifier differs
        (0.86, True, (OLD, None), {"original": M, "strict": R, "refined": R, "refined_dob_guard": R}),
        # similar names, other identifier agrees or is missing
        (0.86, False, (OLD, YOUNG), {"original": M, "strict": M, "refined": M, "refined_dob_guard": M}),
        # identical names, new phone or email, same DOB: one person
        (1.0, True, (OLD, OLD), {"original": M, "strict": R, "refined": M, "refined_dob_guard": M}),
        # identical names, DOBs differ: Jr./Sr.
        (1.0, False, (OLD, YOUNG), {"original": M, "strict": M, "refined": R, "refined_dob_guard": R}),
        # the gap: identical names, other identifier differs, a DOB missing
        (1.0, True, (OLD, None), {"original": M, "strict": R, "refined": M, "refined_dob_guard": R}),
        # identical names, DOB missing, nothing else differs
        (1.0, False, (None, None), {"original": M, "strict": M, "refined": M, "refined_dob_guard": M}),
    ],
)  # fmt: skip
def test_exact_id_decision_table(first_sim, other_differs, dobs, expected):
    got = {
        p: cp.exact_id_decision(p, first_sim, other_differs, *dobs)[0]
        for p in cp.POLICIES
    }
    assert got == expected


def test_guard_rule_name():
    assert cp.exact_id_decision("refined_dob_guard", 1.0, True, OLD, None) == (
        R,
        "exact_id_dob_missing",
    )


def test_only_refined_reproduces_score_pair():
    from evaluate import prepare, synthetic

    with synthetic(seed=3, n=3000, dup_rate=0.25) as (sources, truth):
        rows, matching = cp.compare_seed(3, prepare(sources), truth)
    assert matching == ["refined"]
    assert {r.policy for r in rows} == set(cp.POLICIES)


def test_guard_cost_points():
    def row(policy: str, recall: float) -> cp.PolicyRow:
        return cp.PolicyRow(1, policy, 1.0, recall, 0, 0, 0, 0, 0, 0, 0, 0)

    rows = [row("refined", 0.87), row("refined_dob_guard", 0.85)]
    rows += [row("refined", 0.89), row("refined_dob_guard", 0.86)]
    assert cp.guard_cost_points(rows) == pytest.approx(2.5)
