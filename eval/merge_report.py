"""Merge report over the outputs of `resolver run`: cluster counts and sizes, how many
components the conflict check split, how many clusters mix true entities, and what
happened to each identity-drift chain.

Usage: uv run python eval/merge_report.py [--out out] [--truth data/truth.csv]
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl
import typer

SIZE_BUCKETS = ("1", "2", "3", "4", "5+")


def _bucket(size: int) -> str:
    return str(size) if size < 5 else "5+"


def main(
    out: Path = Path("out"),
    truth: Path = Path("data/truth.csv"),
) -> None:
    cluster_of = dict(pl.read_csv(out / "clusters.csv", infer_schema=False).iter_rows())
    splits = [
        json.loads(line)
        for line in (out / "splits.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    truth_df = pl.read_csv(truth, infer_schema=False)
    entity = dict(truth_df.select("record_id", "entity_id").iter_rows())

    members: dict[str, list[str]] = defaultdict(list)
    for rid, cid in cluster_of.items():
        members[cid].append(rid)
    clusters = list(members.values())
    sizes = Counter(_bucket(len(c)) for c in clusters)
    multi = [c for c in clusters if len(c) > 1]
    impure = [c for c in multi if len({entity[r] for r in c}) > 1]
    split_parts = sum(len(s["clusters"]) for s in splits)
    split_mixed = sum(1 for s in splits if len({entity[r] for r in s["component"]}) > 1)

    # Drift chains: what happened to the three records of each drift entity.
    chains: dict[str, list[str]] = defaultdict(list)
    for rid, eid, kind in truth_df.iter_rows():
        if kind == "drift_chain":
            chains[eid].append(rid)
    in_split = {rid for s in splits for rid in s["component"]}
    outcome: Counter[str] = Counter()
    for rids in chains.values():
        n_clusters = len({cluster_of[r] for r in rids})
        if n_clusters == 1:
            outcome["one cluster (all 3 merged)"] += 1
        elif set(rids) <= in_split:
            outcome["split by conflict check"] += 1
        elif n_clusters == 2:
            outcome["2 merged, 1 apart (no split needed)"] += 1
        else:
            outcome["3 separate clusters"] += 1

    n = len(cluster_of)
    print(f"records:                        {n:>8,}")
    print(f"clusters (incl. singletons):    {len(clusters):>8,}")
    print(f"multi-record clusters (merges): {len(multi):>8,}")
    print(f"records merged away:            {n - len(clusters):>8,}")
    print("cluster-size distribution:")
    for b in SIZE_BUCKETS:
        print(f"  size {b:>2s}: {sizes[b]:>8,}")
    print(
        f"components split by conflict check: {len(splits):,} -> {split_parts:,} "
        f"clusters ({split_mixed:,} of those components mixed true entities)"
    )
    print(f"drift chains: {len(chains):,}")
    for label in (
        "one cluster (all 3 merged)",
        "split by conflict check",
        "2 merged, 1 apart (no split needed)",
        "3 separate clusters",
    ):
        print(f"  {label:38s}{outcome[label]:>6,}")
    print(f"clusters with >1 true entity:   {len(impure):>8,}")
    for c in impure[:10]:
        print(f"  {sorted(c)}  entities={sorted({entity[r] for r in c})}")


if __name__ == "__main__":
    typer.run(main)
