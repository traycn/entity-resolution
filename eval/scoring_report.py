"""Scoring report: MATCH / REVIEW / NO_MATCH counts over all candidate pairs, split by
ordinary duplicates, hard_dup pairs and true non-duplicates. "shared-id non-dup" is
the subset of non-duplicates that share an exact normalized email or phone (household
members); none of them may be MATCHed.

Usage: uv run python eval/scoring_report.py [--sources data/sources] [--truth data/truth.csv]
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

import polars as pl
import typer
from blocking_report import true_pairs

from resolver.blocking import add_blocking_columns, candidate_pairs, load_sources
from resolver.scoring import (
    Decision,
    NormalizedRecord,
    Thresholds,
    load_config,
    normalize_record,
    score_pair,
)


def _shares_identifier(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    return any(a.get(k) and a.get(k) == b.get(k) for k in ("email", "phone"))


def _row(label: str, counts: Counter[Decision]) -> str:
    total = sum(counts.values())
    cells = "".join(
        f"  {counts[d]:>9,} ({counts[d] / max(total, 1):6.2%})" for d in Decision
    )
    return f"{label:22s}{total:>10,}{cells}"


def main(
    sources: Path = Path("data/sources"),
    truth: Path = Path("data/truth.csv"),
) -> None:
    df = load_sources(sources)
    t0 = time.perf_counter()
    pairs = sorted(candidate_pairs(add_blocking_columns(df)))
    records = {r["record_id"]: normalize_record(r) for r in df.to_dicts()}
    t1 = time.perf_counter()
    thresholds = Thresholds()
    results = [score_pair(records[a], records[b], thresholds) for a, b in pairs]
    t2 = time.perf_counter()

    true_all, true_hard = true_pairs(pl.read_csv(truth, infer_schema=False))
    groups: dict[str, Counter[Decision]] = {
        k: Counter()
        for k in (
            "all candidates",
            "ordinary dup",
            "hard_dup",
            "non-dup",
            "  shared-id non-dup",
        )
    }
    rules: dict[str, Counter[str]] = {k: Counter() for k in groups}
    for (a, b), res in zip(pairs, results, strict=True):
        if (a, b) in true_hard:
            kind = "hard_dup"
        elif (a, b) in true_all:
            kind = "ordinary dup"
        else:
            kind = "non-dup"
        labels = ["all candidates", kind]
        if kind == "non-dup" and _shares_identifier(records[a], records[b]):
            labels.append("  shared-id non-dup")
        for k in labels:
            groups[k][res.decision] += 1
            rules[k][f"{res.decision.value}/{res.rule}"] += 1

    cfg = load_config()
    print(
        f"thresholds: match={thresholds.match}  review={thresholds.review}  "
        f"min_match_weight={cfg.min_match_weight}  "
        f"min_first_name_similarity={cfg.min_first_name_similarity}  "
        f"allow_weighted_match={cfg.allow_weighted_match}"
    )
    print(
        f"timing: block+normalize={t1 - t0:.2f}s  score={t2 - t1:.2f}s  "
        f"({len(pairs):,} pairs)"
    )
    header = "".join(f"  {d.value:>18s}" for d in Decision)
    print(f"{'pair group':22s}{'pairs':>10s}{header}")
    for label, counts in groups.items():
        print(_row(label, counts))
    print("\ndecision/rule breakdown:")
    for label, counter in rules.items():
        detail = ", ".join(f"{k}={v:,}" for k, v in sorted(counter.items()))
        print(f"  {label:16s} {detail}")


if __name__ == "__main__":
    typer.run(main)
