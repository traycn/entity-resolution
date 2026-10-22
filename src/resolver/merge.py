"""Clustering and survivorship (Milestone 5).

MATCH edges are grouped into connected components. A component that contains any
pair flagged by `has_conflicting_identifiers` (even one linked only through other
records, A-B-C with A/C in conflict) is re-clustered under a cannot-link
constraint: edges are added strongest first and an edge is skipped if it would put
a conflicting pair in the same cluster.

Every multi-record cluster becomes one golden record plus one audit entry. The
audit entry carries the original source rows, so `unmerge` can restore them.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, TypedDict

from resolver.blocking import Pair
from resolver.normalize import normalize_date, normalize_email, normalize_phone
from resolver.scoring import (
    Decision,
    NormalizedRecord,
    PairResult,
    has_conflicting_identifiers,
)

RawRecord = dict[str, str | None]

# Survivorship works on field groups so that values that belong together (street,
# city, state, zip) always come from the same source record.
FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "name": ("first_name", "last_name"),
    "email": ("email", "email_verified"),
    "phone": ("phone",),
    "address": ("address", "city", "state", "zip"),
    "dob": ("dob",),
}
GOLDEN_FIELDS = tuple(f for group in FIELD_GROUPS.values() for f in group)

# Group key field -> normalizer. A value the normalizer rejects counts as missing.
# Phone and dob are stored normalized (E.164, ISO date). Email is only validated:
# its normalized form strips gmail dots/plus tags, which is a matching key, not the
# customer's address.
_GOLDEN_NORMALIZERS: dict[str, Callable[[str | None], str | None]] = {
    "phone": normalize_phone,
    "dob": normalize_date,
}
_GOLDEN_VALIDATORS: dict[str, Callable[[str | None], str | None]] = {
    "email": normalize_email,
}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# ---------------------------------------------------------------- clustering


@dataclass
class SplitInfo:
    """A MATCH component broken into several clusters by the conflict check or by a
    reviewer's "different people" decision."""

    component: list[str]
    conflicts: list[Pair]  # conflicting pairs found inside the component
    rejected_edges: list[Pair]  # MATCH edges skipped to keep the conflicts apart
    clusters: list[list[str]]
    # Explanation of every MATCH edge in the component: the evidence that linked it.
    edges: list[dict[str, Any]] = field(default_factory=list)
    human_rejected: list[Pair] = field(default_factory=list)  # reviewer cannot-links


@dataclass
class Clustering:
    clusters: list[list[str]]  # sorted members, sorted by first member
    edges: dict[Pair, PairResult]  # MATCH edges kept inside a cluster
    splits: list[SplitInfo] = field(default_factory=list)

    def cluster_of(self) -> dict[str, str]:
        """record_id -> cluster id (the cluster's smallest record id)."""
        return {rid: c[0] for c in self.clusters for rid in c}


class _DisjointSet:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> str:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            ra, rb = min(ra, rb), max(ra, rb)
            self.parent[rb] = ra
        return ra

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            out[self.find(x)].append(x)
        return sorted(sorted(g) for g in out.values())


def _key(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


HUMAN_RULE = "human_approved"


def cluster_matches(
    records: Mapping[str, NormalizedRecord],
    results: Iterable[PairResult],
    *,
    human_links: Iterable[PairResult] = (),
    cannot_link: Iterable[Pair] = (),
) -> Clustering:
    """Cluster every record in `records` over the MATCH edges in `results`.

    Records without a MATCH edge become singleton clusters. Review decisions, when
    given, are applied on top: `human_links` (rule HUMAN_RULE) join like MATCH edges
    and win over automatic edges when a component has to be split; `cannot_link`
    pairs are kept apart like conflicting identifiers. The conflict check still
    applies to human links: no decision merges conflicting identifiers.
    """
    edges = {
        _key(r.left_id, r.right_id): r for r in results if r.decision is Decision.MATCH
    }
    edges.update((_key(r.left_id, r.right_id), r) for r in human_links)
    human_apart = {_key(a, b) for a, b in cannot_link}
    for pair in human_apart:
        edges.pop(pair, None)
    components = _DisjointSet(records)
    for a, b in edges:
        components.union(a, b)

    clusters: list[list[str]] = []
    kept: dict[Pair, PairResult] = {}
    splits: list[SplitInfo] = []
    edges_by_root: dict[str, list[Pair]] = defaultdict(list)
    for pair in edges:
        edges_by_root[components.find(pair[0])].append(pair)

    for component in components.groups():
        comp_edges = edges_by_root.get(components.find(component[0]), [])
        conflicts = [
            (a, b)
            for a, b in combinations(component, 2)
            if has_conflicting_identifiers(records[a], records[b])
        ]
        apart = [p for p in combinations(component, 2) if p in human_apart]
        if not conflicts and not apart:
            clusters.append(component)
            kept.update((p, edges[p]) for p in comp_edges)
            continue

        keep_apart: dict[str, set[str]] = defaultdict(set)
        for a, b in conflicts + apart:
            keep_apart[a].add(b)
            keep_apart[b].add(a)
        dsu = _DisjointSet(component)
        members = {rid: {rid} for rid in component}
        rejected: list[Pair] = []
        # Reviewer links first, then strongest evidence; ties broken by ids so the
        # result is deterministic.
        for pair in sorted(
            comp_edges,
            key=lambda p: (edges[p].rule != HUMAN_RULE, -edges[p].score, p),
        ):
            ra, rb = dsu.find(pair[0]), dsu.find(pair[1])
            if ra == rb:
                kept[pair] = edges[pair]
                continue
            if any(keep_apart[x] & members[rb] for x in members[ra]):
                rejected.append(pair)
                continue
            root = dsu.union(ra, rb)
            members[root] |= members.pop(rb if root == ra else ra)
            kept[pair] = edges[pair]
        parts = dsu.groups()
        clusters.extend(parts)
        evidence = [
            {"left_id": a, "right_id": b, **edges[(a, b)].explanation()}
            for a, b in sorted(comp_edges)
        ]
        splits.append(SplitInfo(component, conflicts, rejected, parts, evidence, apart))

    clusters.sort()
    return Clustering(clusters=clusters, edges=kept, splits=splits)


# -------------------------------------------------------------- survivorship


class GoldenRecord(TypedDict):
    golden_id: str
    members: list[str]
    values: dict[str, str | None]
    lineage: dict[str, str | None]  # field -> record_id the value came from


def _is_verified(record: RawRecord) -> bool:
    return (record.get("email_verified") or "").strip().lower() == "true"


def rank_survivors(records: Iterable[RawRecord]) -> list[RawRecord]:
    """Best first: verified source, then most recent `updated_at`, then record id.

    A record's email_verified flag is the only verification signal in the sources,
    so it marks the whole record as coming from a verified source. Records without
    a parseable `updated_at` rank after dated ones.
    """
    ranked = sorted(records, key=lambda r: str(r.get("record_id") or ""))
    ranked.sort(key=lambda r: normalize_date(r.get("updated_at")) or "", reverse=True)
    ranked.sort(key=_is_verified, reverse=True)  # stable: keeps the order above
    return ranked


def _group_values(record: RawRecord, group: tuple[str, ...]) -> dict[str, str | None]:
    values = {f: _clean(record.get(f)) for f in group}
    normalizer = _GOLDEN_NORMALIZERS.get(group[0])
    if normalizer is not None:
        values[group[0]] = normalizer(values[group[0]])
    validator = _GOLDEN_VALIDATORS.get(group[0])
    if validator is not None and validator(values[group[0]]) is None:
        values[group[0]] = None
    return values


def build_golden(golden_id: str, records: Iterable[RawRecord]) -> GoldenRecord:
    """Pick each field group from the best-ranked record that has it.

    A group is taken as a whole from one record (e.g. street + city + zip); it
    counts as present when its first field is non-null after normalization.
    """
    ranked = rank_survivors(records)
    values: dict[str, str | None] = dict.fromkeys(GOLDEN_FIELDS)
    lineage: dict[str, str | None] = dict.fromkeys(GOLDEN_FIELDS)
    for group in FIELD_GROUPS.values():
        for record in ranked:
            candidate = _group_values(record, group)
            if candidate[group[0]] is None:
                continue
            values.update(candidate)
            source_id = str(record.get("record_id"))
            lineage.update((f, source_id if candidate[f] else None) for f in group)
            break
    return {
        "golden_id": golden_id,
        "members": sorted(str(r.get("record_id")) for r in ranked),
        "values": values,
        "lineage": lineage,
    }


# --------------------------------------------------------------- audit + undo


class AuditEntry(TypedDict):
    action: str  # "merge"
    golden_id: str
    members: list[str]
    edges: list[dict[str, Any]]  # the MATCH explanations that joined the members
    rejected_edges: list[Pair]  # edges cut by the conflict check, if any
    golden: GoldenRecord
    originals: list[RawRecord]  # untouched source rows, enough to undo the merge


@dataclass
class MergeOutput:
    golden: dict[str, GoldenRecord]  # every cluster, singletons included
    audit: list[AuditEntry]  # one entry per multi-record cluster
    clustering: Clustering


def merge(
    raw: Mapping[str, RawRecord],
    records: Mapping[str, NormalizedRecord],
    results: Iterable[PairResult],
    *,
    human_links: Iterable[PairResult] = (),
    cannot_link: Iterable[Pair] = (),
) -> MergeOutput:
    """Cluster, build golden records and the audit log of every merge. Review
    decisions (see `cluster_matches`) are applied when given."""
    clustering = cluster_matches(
        records, results, human_links=human_links, cannot_link=cannot_link
    )
    rejected_by_record: dict[str, list[Pair]] = defaultdict(list)
    for split in clustering.splits:
        for pair in split.rejected_edges:
            rejected_by_record[pair[0]].append(pair)
            rejected_by_record[pair[1]].append(pair)
    edges_by_cluster: dict[str, list[PairResult]] = defaultdict(list)
    cluster_of = clustering.cluster_of()
    for (a, _), res in sorted(clustering.edges.items()):
        edges_by_cluster[cluster_of[a]].append(res)

    golden: dict[str, GoldenRecord] = {}
    audit: list[AuditEntry] = []
    for members in clustering.clusters:
        golden_id = f"G-{members[0]}"
        originals = [dict(raw[rid]) for rid in members]
        record = build_golden(golden_id, originals)
        golden[golden_id] = record
        if len(members) == 1:
            continue
        rejected = sorted({p for rid in members for p in rejected_by_record[rid]})
        audit.append(
            {
                "action": "merge",
                "golden_id": golden_id,
                "members": members,
                "edges": [
                    {"left_id": r.left_id, "right_id": r.right_id, **r.explanation()}
                    for r in edges_by_cluster[members[0]]
                ],
                "rejected_edges": rejected,
                "golden": record,
                "originals": originals,
            }
        )
    return MergeOutput(golden=golden, audit=audit, clustering=clustering)


def unmerge(
    golden: Mapping[str, GoldenRecord], entry: AuditEntry
) -> tuple[dict[str, GoldenRecord], list[RawRecord]]:
    """Undo one merge: drop its golden record, give each original record its own
    singleton golden record, and return the original source rows unchanged."""
    if entry["golden_id"] not in golden:
        raise KeyError(f"{entry['golden_id']} is not in the golden set")
    restored = {k: v for k, v in golden.items() if k != entry["golden_id"]}
    originals = [dict(r) for r in entry["originals"]]
    for record in originals:
        golden_id = f"G-{record.get('record_id')}"
        restored[golden_id] = build_golden(golden_id, [record])
    return restored, originals


def write_audit_log(path: Path, audit: Iterable[AuditEntry]) -> None:
    """One JSON object per line."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in audit:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


def read_audit_log(path: Path) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                entry = json.loads(line)
                entry["rejected_edges"] = [tuple(p) for p in entry["rejected_edges"]]
                entries.append(entry)
    return entries


def write_outputs(out: Path, result: MergeOutput) -> None:
    """Write clusters.csv, golden.csv (values + per-field source record),
    audit.jsonl (one line per merge) and splits.jsonl (conflict-check splits)."""
    out.mkdir(parents=True, exist_ok=True)
    with (out / "clusters.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["record_id", "cluster_id"])
        writer.writerows(sorted(result.clustering.cluster_of().items()))
    lineage_cols = [f"{f}__source" for f in GOLDEN_FIELDS]
    with (out / "golden.csv").open("w", newline="", encoding="utf-8") as fh:
        dict_writer = csv.DictWriter(
            fh,
            ["golden_id", "member_count", "members", *GOLDEN_FIELDS, *lineage_cols],
            lineterminator="\n",
        )
        dict_writer.writeheader()
        for golden_id in sorted(result.golden):
            g = result.golden[golden_id]
            dict_writer.writerow(
                {
                    "golden_id": golden_id,
                    "member_count": len(g["members"]),
                    "members": ";".join(g["members"]),
                    **g["values"],
                    **{f"{f}__source": g["lineage"][f] for f in GOLDEN_FIELDS},
                }
            )
    write_audit_log(out / "audit.jsonl", result.audit)
    with (out / "splits.jsonl").open("w", encoding="utf-8") as fh:
        for split in result.clustering.splits:
            fh.write(json.dumps(asdict(split), sort_keys=True) + "\n")
