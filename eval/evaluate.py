"""Evaluation against ground truth (Milestone 6).

Scores the clusters that `resolver run` wrote (out/clusters.csv) against truth.csv:
pairwise and cluster-level precision / recall / F1, recall split by duplicate kind,
and blocking pair completeness split the same way. Then re-runs the pipeline
in-process to sweep `min_first_name_similarity` and to check the chosen value on
other generator seeds. Writes docs/results.md and the sweep chart.

Duplicate kinds (of a true pair, i.e. two records of the same entity):
  ordinary     both records are "original" / "dup"
  hard_dup     either record is a hard_dup: differs on BOTH email and phone, so the
               safety rule must not merge it. A merged hard_dup pair is never counted
               as a true positive (it does count against precision).
  drift_chain  the 3 records of an identity-drift entity. Records 1 and 3 conflict,
               so at most one of the chain's 3 pairs can end up co-clustered:
               drift recall is capped at 1/3 by design.

Usage: uv run python eval/evaluate.py [--out out] [--truth data/truth.csv] ...
"""

from __future__ import annotations

import dataclasses
import importlib.util
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from types import ModuleType
from typing import Annotated

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import polars as pl
import typer
from matplotlib.ticker import MaxNLocator

from resolver.blocking import (
    Pair,
    add_blocking_columns,
    candidate_pairs,
    load_sources,
)
from resolver.merge import cluster_matches
from resolver.scoring import (
    Decision,
    NormalizedRecord,
    PairResult,
    ScoringConfig,
    Thresholds,
    load_config,
    normalize_record,
    score_pair,
)

KINDS = ("ordinary", "hard_dup", "drift_chain")
# Sections of docs/results.md written by eval/compare_policies.py (`make compare`).
POLICY_BLOCKS = ("policies", "dob-gap")
SWEEP = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


# ------------------------------------------------------------------ truth


@dataclass
class Truth:
    entity: dict[str, str]  # record_id -> entity_id
    pairs: dict[str, set[Pair]]  # kind -> true duplicate pairs
    same_name: set[str] = dataclasses.field(default_factory=set)  # Jr./Sr. children

    @property
    def all_pairs(self) -> set[Pair]:
        return set().union(*self.pairs.values())

    def kind(self, pair: Pair) -> str | None:
        """Duplicate kind of a pair, or None for a non-duplicate."""
        for k in KINDS:
            if pair in self.pairs[k]:
                return k
        return None


def load_truth(rows: Iterable[tuple[str, str, str]]) -> Truth:
    """Truth from (record_id, entity_id, dup_type) rows."""
    entity: dict[str, str] = {}
    members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    same_name: set[str] = set()
    for rid, eid, dup_type in rows:
        entity[rid] = eid
        members[eid].append((rid, dup_type))
        if dup_type == "household_same_name":
            same_name.add(rid)
    pairs: dict[str, set[Pair]] = {k: set() for k in KINDS}
    for recs in members.values():
        types = dict(recs)
        for a, b in combinations(sorted(types), 2):
            if "drift_chain" in (types[a], types[b]):
                pairs["drift_chain"].add((a, b))
            elif "hard_dup" in (types[a], types[b]):
                pairs["hard_dup"].add((a, b))
            else:
                pairs["ordinary"].add((a, b))
    return Truth(entity, pairs, same_name)


def read_truth(path: Path) -> Truth:
    df = pl.read_csv(path, infer_schema=False)
    return load_truth(df.select("record_id", "entity_id", "dup_type").iter_rows())


# ---------------------------------------------------------------- metrics


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class PairwiseMetrics:
    predicted: int  # co-clustered record pairs
    true_positives: int  # co-clustered ordinary + drift_chain pairs
    false_positives: int  # co-clustered pairs of different entities
    found: dict[str, int]  # kind -> co-clustered true pairs
    total: dict[str, int]  # kind -> true pairs

    @property
    def precision(self) -> float:
        """Merged hard_dup pairs are not wins, so they count against precision."""
        return _ratio(self.true_positives, self.predicted)

    @property
    def recall(self) -> float:
        """Over every true pair, hard_dup and drift_chain included."""
        return _ratio(self.true_positives, sum(self.total.values()))

    @property
    def f1(self) -> float:
        return _f1(self.precision, self.recall)

    def kind_recall(self, kind: str) -> float:
        return _ratio(self.found[kind], self.total[kind])

    @property
    def ordinary_f1(self) -> float:
        return _f1(self.precision, self.kind_recall("ordinary"))


def clusters_from(cluster_of: Mapping[str, str]) -> list[list[str]]:
    members: dict[str, list[str]] = defaultdict(list)
    for rid, cid in cluster_of.items():
        members[cid].append(rid)
    return sorted(sorted(m) for m in members.values())


def co_clustered(clusters: Iterable[Sequence[str]]) -> set[Pair]:
    return {p for c in clusters for p in combinations(sorted(c), 2)}


def pairwise_metrics(
    clusters: Iterable[Sequence[str]], truth: Truth
) -> PairwiseMetrics:
    predicted = co_clustered(clusters)
    found = {k: len(predicted & truth.pairs[k]) for k in KINDS}
    return PairwiseMetrics(
        predicted=len(predicted),
        true_positives=found["ordinary"] + found["drift_chain"],
        false_positives=len(predicted - truth.all_pairs),
        found=found,
        total={k: len(truth.pairs[k]) for k in KINDS},
    )


@dataclass
class ClusterMetrics:
    """Exact-match cluster metrics: a predicted cluster is correct only if it holds
    exactly the records of one true entity."""

    predicted: int
    true: int
    exact: int
    mixed: int  # predicted clusters holding more than one true entity

    @property
    def precision(self) -> float:
        return _ratio(self.exact, self.predicted)

    @property
    def recall(self) -> float:
        return _ratio(self.exact, self.true)

    @property
    def f1(self) -> float:
        return _f1(self.precision, self.recall)


def cluster_metrics(
    clusters: Iterable[Sequence[str]], truth: Truth, *, min_size: int = 1
) -> ClusterMetrics:
    """Over predicted clusters and true entities with at least `min_size` records."""
    by_entity: dict[str, set[str]] = defaultdict(set)
    for rid, eid in truth.entity.items():
        by_entity[eid].add(rid)
    true = {frozenset(m) for m in by_entity.values() if len(m) >= min_size}
    predicted = [frozenset(c) for c in clusters]
    mixed = sum(1 for c in predicted if len({truth.entity[r] for r in c}) > 1)
    predicted = [c for c in predicted if len(c) >= min_size]
    return ClusterMetrics(
        predicted=len(predicted),
        true=len(true),
        exact=sum(1 for c in predicted if c in true),
        mixed=mixed,
    )


def blocking_completeness(
    candidates: set[Pair], truth: Truth
) -> dict[str, tuple[int, int]]:
    """kind -> (true pairs that survive blocking, true pairs)."""
    out = {k: (len(candidates & truth.pairs[k]), len(truth.pairs[k])) for k in KINDS}
    out["all"] = (len(candidates & truth.all_pairs), len(truth.all_pairs))
    return out


# --------------------------------------------------------------- pipeline


@dataclass
class Prepared:
    """Blocked and normalized records: everything that does not depend on config."""

    n_records: int
    pairs: list[Pair]
    records: dict[str, NormalizedRecord]
    prepare_seconds: float


def prepare(sources: Path) -> Prepared:
    t0 = time.perf_counter()
    df = load_sources(sources)
    pairs = sorted(candidate_pairs(add_blocking_columns(df)))
    records = {str(r["record_id"]): normalize_record(r) for r in df.to_dicts()}
    return Prepared(df.height, pairs, records, time.perf_counter() - t0)


def decide(prep: Prepared, config: ScoringConfig) -> list[PairResult]:
    thresholds = Thresholds(match=config.match, review=config.review)
    return [
        score_pair(prep.records[a], prep.records[b], thresholds, config)
        for a, b in prep.pairs
    ]


def _shares_identifier(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    return any(a.get(k) and a.get(k) == b.get(k) for k in ("email", "phone"))


@dataclass
class SweepPoint:
    seed: int
    threshold: float
    households_merged: int  # shared-identifier non-duplicate pairs MATCHed
    households: int
    exact_id_dups_to_review: int  # true pairs sharing an identifier, sent to REVIEW
    exact_id_dups: int
    mixed_clusters: int
    precision: float
    ordinary_recall: float

    @property
    def review_share(self) -> float:
        return _ratio(self.exact_id_dups_to_review, self.exact_id_dups)


def sweep_point(
    seed: int, threshold: float, prep: Prepared, config: ScoringConfig, truth: Truth
) -> SweepPoint:
    cfg = dataclasses.replace(config, min_first_name_similarity=threshold)
    results = decide(prep, cfg)
    clusters = cluster_matches(prep.records, results).clusters
    households = merged = id_dups = to_review = 0
    for (a, b), res in zip(prep.pairs, results, strict=True):
        if not _shares_identifier(prep.records[a], prep.records[b]):
            continue
        if truth.kind((a, b)) is None:
            households += 1
            merged += res.decision is Decision.MATCH
        else:
            id_dups += 1
            to_review += res.decision is Decision.REVIEW
    pw = pairwise_metrics(clusters, truth)
    return SweepPoint(
        seed=seed,
        threshold=threshold,
        households_merged=merged,
        households=households,
        exact_id_dups_to_review=to_review,
        exact_id_dups=id_dups,
        mixed_clusters=cluster_metrics(clusters, truth).mixed,
        precision=pw.precision,
        ordinary_recall=pw.kind_recall("ordinary"),
    )


@dataclass
class SameNameOutcome:
    """Jr./Sr. pairs: candidate pairs of a same-name child's record with a record of
    another entity sharing an exact normalized email or phone (the parent)."""

    children: int  # household_same_name records
    rules: Counter[str]  # "DECISION/rule" -> Jr./Sr. pairs
    merged: int  # children whose cluster holds another entity's record
    wrong_pairs: int = 0  # co-clustered different-entity pairs involving a child

    @property
    def pairs(self) -> int:
        return sum(self.rules.values())

    def decided(self, decision: Decision) -> int:
        return sum(
            v for k, v in self.rules.items() if k.startswith(decision.value + "/")
        )


def same_name_outcome(
    prep: Prepared,
    results: Sequence[PairResult],
    clusters: Iterable[Sequence[str]],
    truth: Truth,
) -> SameNameOutcome:
    rules: Counter[str] = Counter()
    for (a, b), res in zip(prep.pairs, results, strict=True):
        if (
            (a in truth.same_name) != (b in truth.same_name)
            and truth.entity[a] != truth.entity[b]
            and _shares_identifier(prep.records[a], prep.records[b])
        ):
            rules[f"{res.decision.value}/{res.rule}"] += 1
    mixed = [c for c in clusters if len({truth.entity[r] for r in c}) > 1]
    merged = sum(sum(r in truth.same_name for r in c) for c in mixed)
    wrong = sum(
        1
        for c in mixed
        for a, b in combinations(c, 2)
        if truth.entity[a] != truth.entity[b]
        and (a in truth.same_name or b in truth.same_name)
    )
    return SameNameOutcome(len(truth.same_name), rules, merged, wrong)


@dataclass
class SeedReport:
    seed: int
    n_records: int
    pairs: int
    decisions: Counter[str]
    blocking: dict[str, tuple[int, int]]
    reduction_ratio: float
    pairwise: PairwiseMetrics
    clusters: ClusterMetrics
    clusters_multi: ClusterMetrics
    splits: int
    runtime: float
    sweep: list[SweepPoint]
    same_name: SameNameOutcome
    held_out: bool = False  # never looked at while choosing thresholds or rules

    @property
    def label(self) -> str:
        return f"{self.seed} (held out)" if self.held_out else str(self.seed)


def evaluate_seed(
    seed: int,
    sources: Path,
    truth: Truth,
    config: ScoringConfig,
    clusters: list[list[str]] | None = None,
) -> SeedReport:
    """Evaluate one dataset. `clusters` (from out/clusters.csv) replaces the in-process
    clustering, which must then agree with it."""
    prep = prepare(sources)
    t0 = time.perf_counter()
    results = decide(prep, config)
    clustering = cluster_matches(prep.records, results)
    runtime = prep.prepare_seconds + time.perf_counter() - t0
    if clusters is not None and clusters != clustering.clusters:
        raise SystemExit(
            "out/clusters.csv does not match an in-process run on the same sources and "
            "src/resolver/config.toml; re-run `make resolve`."
        )
    clusters = clustering.clusters
    n = prep.n_records
    return SeedReport(
        seed=seed,
        n_records=n,
        pairs=len(prep.pairs),
        decisions=Counter(r.decision.value for r in results),
        blocking=blocking_completeness(set(prep.pairs), truth),
        reduction_ratio=1 - len(prep.pairs) / (n * (n - 1) // 2),
        pairwise=pairwise_metrics(clusters, truth),
        clusters=cluster_metrics(clusters, truth),
        clusters_multi=cluster_metrics(clusters, truth, min_size=2),
        splits=len(clustering.splits),
        runtime=runtime,
        sweep=[sweep_point(seed, t, prep, config, truth) for t in SWEEP],
        same_name=same_name_outcome(prep, results, clusters, truth),
    )


@contextmanager
def synthetic(seed: int, n: int, dup_rate: float) -> Iterator[tuple[Path, Truth]]:
    """Generate a dataset into a temporary directory: (sources dir, truth)."""
    generator = _load_generator()
    with tempfile.TemporaryDirectory() as tmp:
        by_source, truth_rows = generator.generate(n, dup_rate, seed)
        sources = Path(tmp) / "sources"
        generator.write_outputs(sources, Path(tmp) / "truth.csv", by_source, truth_rows)
        yield sources, load_truth(truth_rows)


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate", Path(__file__).resolve().parents[1] / "data" / "generate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------- output

# Reference categorical palette, slots 1-3 (validated all-pairs on the light surface).
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")
MARKERS = ("o", "s", "^")
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def plot_sweep(reports: Sequence[SeedReport], configured: float, path: Path) -> None:
    """Two panels sharing the x axis (never a dual y axis): households auto-merged,
    and the share of true exact-identifier duplicates sent to REVIEW."""
    plt.rcParams.update({"font.size": 10, "text.color": INK, "axes.labelcolor": INK_2})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE)
    panels: tuple[tuple[str, Callable[[SweepPoint], float], str], ...] = (
        ("Household pairs auto-merged", lambda p: p.households_merged, "{:.0f}"),
        (
            "True exact-ID duplicates sent to REVIEW",
            lambda p: 100 * p.review_share,
            "{:.0f}%",
        ),
    )
    for ax, (title, value, fmt) in zip(axes, panels, strict=True):
        ax.set_facecolor(SURFACE)
        for i, rep in enumerate(reports):
            xs = [p.threshold for p in rep.sweep]
            ys = [value(p) for p in rep.sweep]
            ax.plot(
                xs, ys, color=SERIES[i], linewidth=2, marker=MARKERS[i], markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=1.5, label=f"seed {rep.seed}",
            )  # fmt: skip
        ax.axvline(configured, color=INK_2, linewidth=1, linestyle=(0, (4, 3)))
        ax.annotate(
            f"configured {configured:.2f}", (configured, 1), xycoords=("data", "axes fraction"),
            xytext=(4, -4), textcoords="offset points", va="top", fontsize=9, color=INK_2,
        )  # fmt: skip
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        ax.set_xlabel("min_first_name_similarity")
        ax.set_xlim(SWEEP[0] - 0.02, SWEEP[-1] + 0.02)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.yaxis.set_major_formatter(fmt.replace("{:", "{x:"))
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.tick_params(colors=INK_2, length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.set_ylim(bottom=0)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        frameon=False,
        loc="upper right",
        ncols=len(labels),
        labelcolor=INK_2,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def _pct(num: int, den: int) -> str:
    return f"{num:,} / {den:,} = {_ratio(num, den):.4f}"


def render_markdown(
    reports: Sequence[SeedReport],
    config: ScoringConfig,
    chart: str,
    n: int,
    dup_rate: float,
) -> str:
    main = reports[0]
    seeds = ", ".join(str(r.seed) for r in reports if not r.held_out)
    held_out = ", ".join(str(r.seed) for r in reports if r.held_out)
    lines = [
        "# Evaluation results",
        "",
        "_Generated by `make eval` (`eval/evaluate.py`); do not edit by hand._",
        "",
        (
            f"Synthetic data: `--n {n} --dup-rate {dup_rate}`, seeds {seeds}. Seed "
            f"{main.seed} is scored from `out/clusters.csv` as written by `resolver run`; "
            "the other seeds are generated and resolved in-process with the same code. "
            f"Config: `min_first_name_similarity = {config.min_first_name_similarity}`, "
            f"`allow_weighted_match = {str(config.allow_weighted_match).lower()}`, "
            f"review threshold {config.review}."
        ),
        "",
        (
            f"Held-out seeds ({held_out or 'none'}) were added after the thresholds and "
            "rules were chosen on the seeds above; they are in every table but not in "
            "the sweep chart."
        ),
        "",
        (
            "Duplicate kinds: **ordinary** pairs; **hard_dup** pairs (differ on both email "
            "and phone, so the safety rule must not merge them; a merged one is never "
            "counted as a true positive); **drift_chain** pairs (3-record identity-drift "
            "entities whose first and last records conflict, so at most 1 of each chain's "
            "3 pairs can be co-clustered: recall is capped at 0.333 by design)."
        ),
        "",
        "## Headline (pairwise, over co-clustered record pairs)",
        "",
        (
            "| Seed | Precision | Recall (ordinary) | F1 (ordinary) | Recall (all true pairs) "
            "| hard_dup merged | drift_chain found | Mixed clusters |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        pw = r.pairwise
        lines.append(
            f"| {r.label} | {pw.precision:.4f} | {pw.kind_recall('ordinary'):.4f} "
            f"| {pw.ordinary_f1:.4f} | {pw.recall:.4f} "
            f"| {pw.found['hard_dup']:,} / {pw.total['hard_dup']:,} "
            f"| {pw.found['drift_chain']:,} / {pw.total['drift_chain']:,} "
            f"| {r.clusters.mixed:,} |"
        )
    lines += [
        "",
        "Precision = co-clustered pairs of the same entity (excluding hard_dup) / all "
        "co-clustered pairs. False positives (pairs of different entities): "
        + ", ".join(f"seed {r.label}: {r.pairwise.false_positives:,}" for r in reports)
        + ".",
        "",
        "## Exact-ID policy comparison",
        "",
        "_Written by `make compare` (`eval/compare_policies.py`)._",
        "",
        "<!-- policies:start -->",
        "_Not generated yet: run `make compare`._",
        "<!-- policies:end -->",
        "",
        "## Pipeline",
        "",
        (
            "| Seed | Records | Candidate pairs | MATCH | REVIEW | NO_MATCH | Clusters "
            "| Records merged away | Components split | Runtime |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        d = r.decisions
        lines.append(
            f"| {r.label} | {r.n_records:,} | {r.pairs:,} | {d['MATCH']:,} | {d['REVIEW']:,} "
            f"| {d['NO_MATCH']:,} | {r.clusters.predicted:,} "
            f"| {r.n_records - r.clusters.predicted:,} | {r.splits:,} | {r.runtime:.1f}s |"
        )
    lines += [
        "",
        (
            "Runtime is block + normalize + score + cluster, in-process (no golden records "
            "or file output)."
        ),
        "",
        "## Pairwise recall by duplicate kind",
        "",
        "| Seed | ordinary | hard_dup | drift_chain (cap 0.333) | all |",
        "|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        pw = r.pairwise
        cells = " | ".join(_pct(pw.found[k], pw.total[k]) for k in KINDS)
        all_found = sum(pw.found.values())
        lines.append(
            f"| {r.label} | {cells} | {_pct(all_found, sum(pw.total.values()))} |"
        )
    lines += [
        "",
        (
            "The hard_dup column counts merges the safety rule should have prevented; it is "
            'expected to be 0. "all" here counts hard_dup merges as found.'
        ),
        "",
        "## Cluster level (exact match)",
        "",
        (
            "A predicted cluster is correct only if it holds exactly the records of one true "
            "entity. *All* includes singletons (an un-duplicated entity left alone counts); "
            "*multi-record* keeps only predicted clusters and true entities with ≥ 2 records."
        ),
        "",
        "| Seed | Scope | Predicted | True entities | Exact | Precision | Recall | F1 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        for scope, c in (("all", r.clusters), ("multi-record", r.clusters_multi)):
            lines.append(
                f"| {r.label} | {scope} | {c.predicted:,} | {c.true:,} | {c.exact:,} "
                f"| {c.precision:.4f} | {c.recall:.4f} | {c.f1:.4f} |"
            )
    lines += [
        "",
        "## Same-name households (Jr./Sr.)",
        "",
        (
            "A same-name child (`household_same_name`, one record) shares the parent's "
            "first and last name and phone or email, with a different birth date. A "
            "Jr./Sr. pair is a candidate pair of the child's record with another entity's "
            "record that shares an exact normalized email or phone. None may be MATCHed."
        ),
        "",
        (
            "| Seed | Children | Jr./Sr. pairs | MATCH | REVIEW | NO_MATCH | Children merged "
            "| Rules |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in reports:
        sn = r.same_name
        rules = ", ".join(f"{k} {v}" for k, v in sorted(sn.rules.items())) or "-"
        lines.append(
            f"| {r.label} | {sn.children} | {sn.pairs} | {sn.decided(Decision.MATCH)} "
            f"| {sn.decided(Decision.REVIEW)} | {sn.decided(Decision.NO_MATCH)} "
            f"| {sn.merged} | {rules} |"
        )
    lines += [
        "",
        "## Blocking pair completeness (M3)",
        "",
        "Share of true duplicate pairs that survive blocking, by kind.",
        "",
        "| Seed | ordinary | hard_dup | drift_chain | all | Reduction ratio |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        cells = " | ".join(_pct(*r.blocking[k]) for k in (*KINDS, "all"))
        lines.append(f"| {r.label} | {cells} | {r.reduction_ratio:.6f} |")
    lines += [
        "",
        "## `min_first_name_similarity` sweep",
        "",
        (
            "An exact email/phone match auto-merges only when the first names agree at this "
            "threshold. *Households* are candidate pairs of different people that share an "
            "exact normalized email or phone; *exact-ID duplicates* are true pairs that do."
        ),
        "",
        f"![min_first_name_similarity sweep]({chart})",
        "",
        (
            "| Seed | Threshold | Households merged | Exact-ID dups to REVIEW | Mixed clusters "
            "| Precision | Recall (ordinary) |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        for p in r.sweep:
            mark = (
                "**"
                if abs(p.threshold - config.min_first_name_similarity) < 1e-9
                else ""
            )
            lines.append(
                f"| {r.label} | {mark}{p.threshold:.2f}{mark} "
                f"| {p.households_merged} / {p.households} "
                f"| {p.exact_id_dups_to_review:,} / {p.exact_id_dups:,} ({p.review_share:.2%}) "
                f"| {p.mixed_clusters} | {p.precision:.4f} | {p.ordinary_recall:.4f} |"
            )
    lines += [
        "",
        "## Known limitations",
        "",
        (
            "- **Synthetic birth dates are never corrupted.** The generator only drops a "
            "DOB (about 20% of duplicates), never mistypes or reformats it beyond what the "
            "normalizer parses, so the DOB guard (`exact_id_dob_mismatch`) sends no true "
            "duplicate to REVIEW here and the clustering DOB cannot-link (v1.1.0) never "
            "splits one. On real data, DOB typos would turn some true duplicates into "
            "REVIEW pairs or splits: their recall cost is likely understated. A reviewer "
            "can approve such a pair to lift the cannot-link."
        ),
        (
            "- **A record without a DOB picks a side.** When a DOB-less record matches "
            "both a parent and a same-name child, clustering keeps the stronger edge "
            "(then the lower record ids) and cuts the other, so a true duplicate can end "
            "up with the child. This is the small recall cost of the DOB cannot-link."
        ),
        (
            "- **Households with similar first names.** A shared household phone or email "
            "plus first names that clear `min_first_name_similarity` (0.80 Jaro-Winkler, "
            "e.g. Margaret / Maria) MATCHes when no second identifier is present to "
            "contradict it. These are the wrong-entity pairs that do not involve a Jr./Sr."
        ),
        (
            "- **hard_dup pairs are never merged by design** (they differ on both email "
            "and phone), and **drift_chain recall is capped at 1/3** by the conflict check."
        ),
        "<!-- dob-gap:start -->",
        "<!-- dob-gap:end -->",
    ]
    return "\n".join(lines) + "\n"


def print_summary(reports: Sequence[SeedReport]) -> None:
    head = (
        f"{'seed':>6} {'precision':>10} {'rec(ord)':>9} {'F1(ord)':>8} {'rec(all)':>9} "
    )
    head += (
        f"{'hard_dup':>9} {'drift':>10} {'mixed':>6} {'blk(ord)':>9} {'blk(hard)':>10} "
    )
    head += f"{'blk(drift)':>11} {'REVIEW':>7} {'jr/sr MATCH':>12}"
    print(head)
    for r in reports:
        pw = r.pairwise
        blk = {k: _ratio(*r.blocking[k]) for k in KINDS}
        print(
            f"{str(r.seed) + ('*' if r.held_out else ''):>6} {pw.precision:>10.4f} {pw.kind_recall('ordinary'):>9.4f} "
            f"{pw.ordinary_f1:>8.4f} {pw.recall:>9.4f} "
            f"{pw.found['hard_dup']:>4}/{pw.total['hard_dup']:<4} "
            f"{pw.found['drift_chain']:>5}/{pw.total['drift_chain']:<4} "
            f"{r.clusters.mixed:>6} {blk['ordinary']:>9.4f} {blk['hard_dup']:>10.4f} "
            f"{blk['drift_chain']:>11.4f} {r.decisions['REVIEW']:>7,} "
            f"{r.same_name.decided(Decision.MATCH):>5}/{r.same_name.pairs:<6}"
        )


def _markers(name: str) -> tuple[str, str]:
    return f"<!-- {name}:start -->", f"<!-- {name}:end -->"


def read_block(text: str, name: str) -> str | None:
    """Content between the `name` markers, or None if they are missing."""
    start, end = _markers(name)
    i, j = text.find(start), text.find(end)
    return text[i + len(start) : j].strip("\n") if 0 <= i < j else None


def replace_block(text: str, name: str, content: str) -> str:
    """Replace the content between the `name` markers (which must exist)."""
    start, end = _markers(name)
    i, j = text.find(start), text.find(end)
    if not 0 <= i < j:
        raise SystemExit(f"missing {start} ... {end} markers")
    return f"{text[:i]}{start}\n{content}\n{text[j:]}"


def readme_headline(reports: Sequence[SeedReport], link: str) -> str:
    precisions = [r.pairwise.precision for r in reports]
    recalls = [r.pairwise.kind_recall("ordinary") for r in reports]
    wrong = sum(r.pairwise.false_positives for r in reports)
    jr_sr = sum(r.same_name.wrong_pairs for r in reports)
    hard = sum(r.pairwise.found["hard_dup"] for r in reports)
    share = "All" if jr_sr == wrong else f"{jr_sr} of the"
    return (
        f"**Pairwise precision {sum(precisions) / len(precisions):.4f}** (mean over "
        f"{len(reports)} seeds, range {min(precisions):.4f}-{max(precisions):.4f}) at "
        f"**{sum(recalls) / len(recalls):.4f} recall on ordinary duplicates**, with "
        f"{hard} hard_dup merges. {share} {wrong} wrong-entity pairs across all seeds "
        "involve a same-name parent and child (Jr./Sr.); every wrong merge links "
        "different people through a shared phone or email. See "
        f"[Known limitations]({link}#known-limitations)."
    )


def update_readme(
    readme: Path, main: SeedReport, reports: Sequence[SeedReport], results: Path
) -> None:
    """Rewrite the README headline (all seeds) and Result table (`main`) blocks."""
    pw = main.pairwise
    link = results.resolve().relative_to(readme.resolve().parent, walk_up=True)
    table = "\n".join(
        [
            (
                f"Detail for seed {main.seed}, {main.n_records:,} synthetic records. Every "
                "seed, recall by duplicate kind, cluster-level metrics and the threshold "
                f"sweep: [{link.as_posix()}]({link.as_posix()})."
            ),
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Records processed | {main.n_records:,} |",
            (
                "| Duplicates resolved | "
                f"{main.n_records - main.clusters.predicted:,} records merged away |"
            ),
            (
                f"| Pairwise precision | {pw.precision:.4f} ({pw.false_positives:,} wrong-entity "
                f"pairs; {pw.found['hard_dup']:,} of {pw.total['hard_dup']:,} hard_dup pairs "
                "merged) |"
            ),
            (
                f"| Pairwise recall | {pw.kind_recall('ordinary'):.4f} on ordinary duplicates; "
                f"{pw.recall:.4f} on all true pairs |"
            ),
            f"| Pairs sent to human review | {main.decisions['REVIEW']:,} |",
            (
                f"| Runtime (50k records) | {main.runtime:.1f} s (block, normalize, score, "
                "cluster) |"
            ),
        ]
    )
    text = readme.read_text(encoding="utf-8")
    text = replace_block(text, "headline", readme_headline(reports, link.as_posix()))
    readme.write_text(replace_block(text, "results", table), encoding="utf-8")


def main(
    out: Annotated[Path, typer.Option(help="`resolver run` output dir.")] = Path("out"),
    sources: Annotated[Path, typer.Option(help="Sources resolved into --out.")] = Path(
        "data/sources"
    ),
    truth: Annotated[Path, typer.Option(help="Ground truth for --sources.")] = Path(
        "data/truth.csv"
    ),
    seed: Annotated[int, typer.Option(help="Seed --sources was generated with.")] = 42,
    extra_seed: Annotated[
        list[int] | None,
        typer.Option(help="Also generate and resolve these seeds [default: 7 1234]."),
    ] = None,
    held_out_seed: Annotated[
        list[int] | None,
        typer.Option(
            help="Seeds never used for tuning: reported, not charted "
            "[default: 2026 99 31337]."
        ),
    ] = None,
    n: Annotated[int, typer.Option(help="Records per extra seed.")] = 50_000,
    dup_rate: Annotated[float, typer.Option(help="Dup rate per extra seed.")] = 0.25,
    results: Annotated[Path, typer.Option(help="Markdown report.")] = Path(
        "docs/results.md"
    ),
    chart: Annotated[Path, typer.Option(help="Sweep chart (PNG).")] = Path(
        "docs/threshold_sweep.png"
    ),
    readme: Annotated[
        Path | None, typer.Option(help="README whose Result table to rewrite.")
    ] = None,
) -> None:
    config = load_config()
    cluster_of = dict(pl.read_csv(out / "clusters.csv", infer_schema=False).iter_rows())
    reports = [
        evaluate_seed(
            seed, sources, read_truth(truth), config, clusters_from(cluster_of)
        )
    ]
    tuning = extra_seed if extra_seed is not None else [7, 1234]
    held_out = held_out_seed if held_out_seed is not None else [2026, 99, 31337]
    for extra in [*tuning, *held_out]:
        with synthetic(extra, n, dup_rate) as (extra_sources, extra_truth):
            report = evaluate_seed(extra, extra_sources, extra_truth, config)
            report.held_out = extra in held_out
            reports.append(report)

    charted = [r for r in reports if not r.held_out][: len(SERIES)]
    plot_sweep(charted, config.min_first_name_similarity, chart)
    rel_chart = chart.resolve().relative_to(results.resolve().parent, walk_up=True)
    results.parent.mkdir(parents=True, exist_ok=True)
    text = render_markdown(reports, config, rel_chart.as_posix(), n, dup_rate)
    if results.exists():  # keep what `make compare` wrote
        old = results.read_text(encoding="utf-8")
        for name in POLICY_BLOCKS:
            kept = read_block(old, name)
            if kept is not None:
                text = replace_block(text, name, kept)
    results.write_text(text, encoding="utf-8")
    if readme is not None:
        update_readme(readme, reports[0], reports, results)
    print_summary(reports)
    print(f"wrote {results} and {chart}" + (f", updated {readme}" if readme else ""))


if __name__ == "__main__":
    typer.run(main)
