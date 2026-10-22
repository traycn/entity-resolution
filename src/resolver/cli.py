"""Command-line entry point: `resolver run --sources data/sources --out out/`."""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from resolver.blocking import add_blocking_columns, candidate_pairs, load_sources
from resolver.merge import cluster_matches, merge, write_outputs
from resolver.review import (
    finalize_report,
    plan_decisions,
    read_decision_rows,
    write_review_pairs,
)
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
    decisions: Annotated[
        Path | None,
        typer.Option(
            help="Review decisions CSV to apply (approved pairs merge, rejected pairs "
            "stay apart); also writes decisions_applied.jsonl."
        ),
    ] = None,
) -> None:
    """Resolve the source records and write clusters, golden records and the audit log."""
    t0 = time.perf_counter()
    df = load_sources(sources)
    pairs = sorted(candidate_pairs(add_blocking_columns(df)))
    rows = {str(r["record_id"]): r for r in df.to_dicts()}
    records = {rid: normalize_record(r) for rid, r in rows.items()}
    thresholds = Thresholds()
    results = [score_pair(records[a], records[b], thresholds) for a, b in pairs]
    report: list[dict[str, str]] = []
    if decisions is None:
        result = merge(rows, records, results)
    else:
        automatic = cluster_matches(records, results)
        plan = plan_decisions(read_decision_rows(decisions), results, automatic.splits)
        result = merge(
            rows,
            records,
            results,
            human_links=plan.human_links,
            cannot_link=plan.cannot_link,
        )
        report = finalize_report(
            plan, automatic.cluster_of(), result.clustering.cluster_of()
        )
    write_outputs(out, result)
    write_review_pairs(out / "review.jsonl", results)
    if decisions is not None:
        with (out / "decisions_applied.jsonl").open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, sort_keys=True) + "\n" for r in report)
    elapsed = time.perf_counter() - t0

    by_decision = Counter(r.decision.value for r in results)
    clusters = result.clustering.clusters
    typer.echo(
        f"{df.height:,} records, {len(pairs):,} candidate pairs "
        f"({', '.join(f'{k}={v:,}' for k, v in sorted(by_decision.items()))})"
    )
    typer.echo(
        f"{len(clusters):,} clusters, {len(result.audit):,} merges, "
        f"{df.height - len(clusters):,} records merged away, "
        f"{len(result.clustering.splits):,} components split by the conflict check"
    )
    if decisions is not None:
        statuses = Counter(r["status"] for r in report)
        typer.echo(
            f"{len(report):,} review decisions from {decisions}: "
            + (", ".join(f"{k}={v:,}" for k, v in sorted(statuses.items())) or "none")
        )
    typer.echo(
        f"wrote {out}/: clusters.csv golden.csv audit.jsonl splits.jsonl review.jsonl"
        + (" decisions_applied.jsonl" if decisions is not None else "")
    )
    typer.echo(f"runtime: {elapsed:.1f}s")
