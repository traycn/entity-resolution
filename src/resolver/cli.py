"""Command-line entry point: `resolver run --sources data/sources --out out/`."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from resolver.blocking import add_blocking_columns, candidate_pairs, load_sources
from resolver.merge import merge, write_outputs
from resolver.review import write_review_pairs
from resolver.scoring import Thresholds, normalize_record, score_pair

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Entity resolution: block, score, cluster and merge customer records."""


@app.command()
def run(
    sources: Annotated[Path, typer.Option(help="Directory of source CSVs.")] = Path(
        "data/sources"
    ),
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("out"),
) -> None:
    """Resolve the source records and write clusters, golden records and the audit log."""
    t0 = time.perf_counter()
    df = load_sources(sources)
    pairs = sorted(candidate_pairs(add_blocking_columns(df)))
    rows = {str(r["record_id"]): r for r in df.to_dicts()}
    records = {rid: normalize_record(r) for rid, r in rows.items()}
    thresholds = Thresholds()
    results = [score_pair(records[a], records[b], thresholds) for a, b in pairs]
    result = merge(rows, records, results)
    write_outputs(out, result)
    write_review_pairs(out / "review.jsonl", results)
    elapsed = time.perf_counter() - t0

    decisions = Counter(r.decision.value for r in results)
    clusters = result.clustering.clusters
    typer.echo(
        f"{df.height:,} records, {len(pairs):,} candidate pairs "
        f"({', '.join(f'{k}={v:,}' for k, v in sorted(decisions.items()))})"
    )
    typer.echo(
        f"{len(clusters):,} clusters, {len(result.audit):,} merges, "
        f"{df.height - len(clusters):,} records merged away, "
        f"{len(result.clustering.splits):,} components split by the conflict check"
    )
    typer.echo(
        f"wrote {out}/: clusters.csv golden.csv audit.jsonl splits.jsonl review.jsonl"
    )
    typer.echo(f"runtime: {elapsed:.1f}s")
