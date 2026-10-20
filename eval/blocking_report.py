"""Blocking quality report: pair completeness, reduction ratio, timing, hard_dup survival.

Usage: uv run python eval/blocking_report.py [--sources data/sources] [--truth data/truth.csv]
"""

from __future__ import annotations

import time
from itertools import combinations
from pathlib import Path

import polars as pl
import typer

from resolver.blocking import (
    Pair,
    add_blocking_columns,
    candidate_pairs_by_key,
    load_sources,
)


def true_pairs(truth: pl.DataFrame) -> tuple[set[Pair], set[Pair]]:
    """(all true duplicate pairs, the subset involving a hard_dup record)."""
    all_pairs: set[Pair] = set()
    hard: set[Pair] = set()
    for _, grp in truth.group_by("entity_id"):
        ids = grp["record_id"].to_list()
        hard_ids = set(
            grp.filter(pl.col("dup_type") == "hard_dup")["record_id"].to_list()
        )
        for a, b in combinations(sorted(ids), 2):
            all_pairs.add((a, b))
            if a in hard_ids or b in hard_ids:
                hard.add((a, b))
    return all_pairs, hard


def main(
    sources: Path = Path("data/sources"),
    truth: Path = Path("data/truth.csv"),
) -> None:
    df = load_sources(sources)
    t0 = time.perf_counter()
    df = add_blocking_columns(df)
    t1 = time.perf_counter()
    by_key = candidate_pairs_by_key(df)
    union = set().union(*by_key.values())
    t2 = time.perf_counter()

    truth_df = pl.read_csv(truth, infer_schema=False)
    true_all, true_hard = true_pairs(truth_df)
    n = df.height
    total = n * (n - 1) // 2

    print(f"records={n:,}  all possible pairs={total:,}")
    print(
        f"timing: normalize={t1 - t0:.2f}s  blocking={t2 - t1:.2f}s  total={t2 - t0:.2f}s"
    )
    for name, pairs in by_key.items():
        print(
            f"  {name:18s} pairs={len(pairs):>9,}  completeness={len(pairs & true_all) / len(true_all):.4f}"
        )
    print(f"union candidates={len(union):,}")
    print(f"reduction ratio     = {1 - len(union) / total:.6f}")
    print(
        f"pair completeness   = {len(union & true_all):,}/{len(true_all):,} = {len(union & true_all) / len(true_all):.4f}"
    )
    ordinary = true_all - true_hard
    print(
        f"  ordinary dup pairs = {len(union & ordinary):,}/{len(ordinary):,} = {len(union & ordinary) / len(ordinary):.4f}"
    )
    print(
        f"  hard_dup pairs     = {len(union & true_hard):,}/{len(true_hard):,} = {len(union & true_hard) / max(len(true_hard), 1):.4f}"
    )


if __name__ == "__main__":
    typer.run(main)
