"""Compare exact-ID auto-merge policies on the same data (Milestone 6).

The policies differ only for pairs that reach the exact-ID branch of `score_pair`
(an exact normalized email or phone match whose first names clear
min_first_name_similarity). Each policy re-decides those pairs from the scorer's own
explanation (first-name similarity, field scores) and the records' birth dates;
every other pair keeps its decision. One of them must reproduce `score_pair`
exactly: that is the shipped policy.

  original           MATCH
  strict             other identifier present on both and different -> REVIEW
  refined            names not identical: other identifier differs -> REVIEW;
                     identical names: both DOBs present and different -> REVIEW
  refined_dob_guard  refined, plus identical names with the other identifier
                     different and a DOB missing on either side -> REVIEW

Writes the `policies` and `dob-gap` blocks of docs/results.md.

Usage: uv run python eval/compare_policies.py [--seed 42 --seed 7 ...]
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from evaluate import (
    Prepared,
    Truth,
    cluster_metrics,
    decide,
    pairwise_metrics,
    prepare,
    replace_block,
    same_name_outcome,
    synthetic,
)

from resolver.merge import cluster_matches
from resolver.scoring import Decision, PairResult, load_config

POLICIES = ("original", "strict", "refined", "refined_dob_guard")
LABELS = {
    "original": "original (no mismatch rule)",
    "strict": "strict (any other-ID mismatch)",
    "refined": "refined (name-aware + DOB mismatch)",
    "refined_dob_guard": "refined + missing-DOB guard",
}
# Rules `score_pair` can return once a pair has reached the exact-ID branch with
# agreeing first names.
EXACT_ID_RULES = frozenset(
    {
        "exact_id",
        "exact_id_other_mismatch",
        "exact_id_dob_mismatch",
        "exact_id_dob_missing",
    }
)
# Adopt refined_dob_guard only if it costs less than this much ordinary recall
# (mean over seeds, in points) compared with refined.
MAX_GUARD_COST_POINTS = 0.5
SEEDS = (42, 7, 1234, 2026, 99, 31337)


def exact_id_decision(
    policy: str,
    first_sim: float,
    other_differs: bool,
    dob_left: str | None,
    dob_right: str | None,
) -> tuple[Decision, str]:
    """Decision and rule for an exact-ID pair whose first names already agree."""
    if policy == "original":
        return Decision.MATCH, "exact_id"
    if policy == "strict" or first_sim < 1.0:
        if other_differs:
            return Decision.REVIEW, "exact_id_other_mismatch"
        return Decision.MATCH, "exact_id"
    if dob_left and dob_right and dob_left != dob_right:
        return Decision.REVIEW, "exact_id_dob_mismatch"
    if policy == "refined_dob_guard" and other_differs and not (dob_left and dob_right):
        return Decision.REVIEW, "exact_id_dob_missing"
    return Decision.MATCH, "exact_id"


def apply_policy(
    policy: str, prep: Prepared, results: Sequence[PairResult]
) -> list[PairResult]:
    out: list[PairResult] = []
    for (a, b), res in zip(prep.pairs, results, strict=True):
        if res.rule in EXACT_ID_RULES:
            other_differs = any(
                res.field_scores.get(k, 1.0) < 1.0 for k in ("email", "phone")
            )
            decision, rule = exact_id_decision(
                policy,
                res.checks["first_name"],
                other_differs,
                prep.records[a].get("dob"),
                prep.records[b].get("dob"),
            )
            res = dataclasses.replace(res, decision=decision, rule=rule)
        out.append(res)
    return out


@dataclass
class PolicyRow:
    seed: int
    policy: str
    precision: float
    ordinary_recall: float
    review: int
    drift_merged: int
    drift_total: int
    mixed: int
    jr_sr_match: int
    jr_sr_pairs: int
    children: int
    children_merged: int


def compare_seed(
    seed: int, prep: Prepared, truth: Truth
) -> tuple[list[PolicyRow], list[str]]:
    """Rows for every policy, and the policies that reproduce `score_pair`."""
    shipped = decide(prep, load_config())
    rows: list[PolicyRow] = []
    matching: list[str] = []
    for policy in POLICIES:
        results = apply_policy(policy, prep, shipped)
        if [(r.decision, r.rule) for r in results] == [
            (r.decision, r.rule) for r in shipped
        ]:
            matching.append(policy)
        clusters = cluster_matches(prep.records, results).clusters
        pw = pairwise_metrics(clusters, truth)
        sn = same_name_outcome(prep, results, clusters, truth)
        rows.append(
            PolicyRow(
                seed=seed,
                policy=policy,
                precision=pw.precision,
                ordinary_recall=pw.kind_recall("ordinary"),
                review=sum(r.decision is Decision.REVIEW for r in results),
                drift_merged=pw.found["drift_chain"],
                drift_total=pw.total["drift_chain"],
                mixed=cluster_metrics(clusters, truth).mixed,
                jr_sr_match=sn.decided(Decision.MATCH),
                jr_sr_pairs=sn.pairs,
                children=sn.children,
                children_merged=sn.merged,
            )
        )
    return rows, matching


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def guard_cost_points(rows: Sequence[PolicyRow]) -> float:
    """Mean ordinary recall of refined minus refined_dob_guard, in points."""
    by = {p: [r.ordinary_recall for r in rows if r.policy == p] for p in POLICIES}
    return 100 * (_mean(by["refined"]) - _mean(by["refined_dob_guard"]))


def render_policies(
    rows: Sequence[PolicyRow], shipped: str, seeds: Sequence[int]
) -> str:
    lines = [
        (
            f"Same data for every policy (seeds {', '.join(map(str, seeds))}). Only pairs "
            "that reach the exact-ID branch with agreeing first names are re-decided. "
            f"Shipped policy: **{LABELS[shipped]}** (reproduces `score_pair` exactly)."
        ),
        "",
        "Mean over all seeds (Jr./Sr. and mixed clusters summed):",
        "",
        (
            "| Policy | Precision | Ordinary recall | REVIEW queue | drift_chain merged "
            "| Mixed clusters | Jr./Sr. pairs MATCHed |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in POLICIES:
        rs = [r for r in rows if r.policy == policy]
        name = f"**{LABELS[policy]}**" if policy == shipped else LABELS[policy]
        lines.append(
            f"| {name} | {_mean([r.precision for r in rs]):.4f} "
            f"| {_mean([r.ordinary_recall for r in rs]):.4f} "
            f"| {_mean([r.review for r in rs]):,.0f} "
            f"| {_mean([r.drift_merged for r in rs]):,.0f} / {rs[0].drift_total:,} "
            f"| {sum(r.mixed for r in rs)} "
            f"| {sum(r.jr_sr_match for r in rs)} / {sum(r.jr_sr_pairs for r in rs)} |"
        )
    lines += [
        "",
        "Per seed:",
        "",
        (
            "| Seed | Policy | Precision | Ordinary recall | REVIEW | drift_chain merged "
            "| Mixed | Jr./Sr. MATCHed | Children merged |"
        ),
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r.seed} | {LABELS[r.policy]} | {r.precision:.4f} | {r.ordinary_recall:.4f} "
            f"| {r.review:,} | {r.drift_merged:,} | {r.mixed} "
            f"| {r.jr_sr_match} / {r.jr_sr_pairs} | {r.children_merged} / {r.children} |"
        )
    return "\n".join(lines)


def render_gap(rows: Sequence[PolicyRow], shipped: str) -> str:
    cost = guard_cost_points(rows)
    refined_leak = sum(r.jr_sr_match for r in rows if r.policy == "refined")
    guard_leak = sum(r.jr_sr_match for r in rows if r.policy == "refined_dob_guard")
    pairs = sum(r.jr_sr_pairs for r in rows if r.policy == "refined")
    measured = (
        f"Measured over {len({r.seed for r in rows})} seeds: refined MATCHes "
        f"{refined_leak} of {pairs} Jr./Sr. pairs; the missing-DOB guard "
        f"(identical names, other identifier different, a DOB missing -> REVIEW) brings "
        f"that to {guard_leak} at a cost of {cost:.2f} points of mean ordinary recall "
        f"(adoption limit: < {MAX_GUARD_COST_POINTS} points)."
    )
    if shipped == "refined_dob_guard":
        return f"- **Missing-DOB gap: closed by the guard (`exact_id_dob_missing`).** {measured}"
    return (
        "- **Missing-DOB gap (not closed).** With identical first names and a birth date "
        "missing on either side, an exact email/phone match MATCHes even when the other "
        "identifier differs, so a same-name parent and child (Jr./Sr.) can still merge. "
        f"{measured} The guard was not adopted."
    )


def main(
    seed: Annotated[
        list[int] | None,
        typer.Option(help=f"Seeds to compare [default: {' '.join(map(str, SEEDS))}]."),
    ] = None,
    n: Annotated[int, typer.Option(help="Records per seed.")] = 50_000,
    dup_rate: Annotated[float, typer.Option(help="Dup rate per seed.")] = 0.25,
    results: Annotated[Path, typer.Option(help="Markdown report to update.")] = Path(
        "docs/results.md"
    ),
    readme: Annotated[
        Path | None, typer.Option(help="README whose policy table to rewrite.")
    ] = None,
) -> None:
    seeds = list(seed) if seed else list(SEEDS)
    rows: list[PolicyRow] = []
    shipped_by_seed: list[set[str]] = []
    for s in seeds:
        with synthetic(s, n, dup_rate) as (sources, truth):
            seed_rows, matching = compare_seed(s, prepare(sources), truth)
        rows += seed_rows
        shipped_by_seed.append(set(matching))
        for r in seed_rows:
            print(
                f"{r.seed:>6} {r.policy:18s} P={r.precision:.4f} "
                f"R(ord)={r.ordinary_recall:.4f} REVIEW={r.review:>5,} "
                f"drift={r.drift_merged:>4} mixed={r.mixed:>2} "
                f"jr/sr MATCH={r.jr_sr_match}/{r.jr_sr_pairs}",
                flush=True,
            )
    shipped = [p for p in POLICIES if all(p in m for m in shipped_by_seed)]
    if len(shipped) != 1:
        raise SystemExit(
            f"expected exactly one policy to match score_pair, got {shipped}"
        )
    print(
        f"shipped policy: {shipped[0]}; guard cost {guard_cost_points(rows):.2f} points"
    )

    text = results.read_text(encoding="utf-8")
    text = replace_block(text, "policies", render_policies(rows, shipped[0], seeds))
    text = replace_block(text, "dob-gap", render_gap(rows, shipped[0]))
    results.write_text(text, encoding="utf-8")
    if readme is not None:
        mean_table = render_policies(rows, shipped[0], seeds).split("\n\nPer seed:")[0]
        rt = readme.read_text(encoding="utf-8")
        readme.write_text(replace_block(rt, "policies", mean_table), encoding="utf-8")
    print(f"updated {results}" + (f" and {readme}" if readme else ""))


if __name__ == "__main__":
    typer.run(main)
