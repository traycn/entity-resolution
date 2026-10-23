"""Human review queue (Milestone 7).

The queue holds two kinds of items:
  pair   a candidate pair the scorer sent to REVIEW (out/review.jsonl)
  split  a MATCH component the conflict check broke apart (out/splits.jsonl)

Each item carries the source records, the per-field scores and the rule that fired,
described in plain words. Decisions are appended to a CSV (latest row per item
wins), so the file is also the audit trail of every human decision.
"""

from __future__ import annotations

import csv
import json
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from resolver.blocking import Pair
from resolver.merge import HUMAN_RULE, SplitInfo
from resolver.normalize import (
    normalize_address,
    normalize_date,
    normalize_email,
    normalize_name,
    normalize_phone,
)
from resolver.scoring import Decision, PairResult

RawRecord = Mapping[str, str | None]
DecisionValue = Literal["approve", "reject"]
Agreement = Literal["same", "different", "missing"]

SPLIT_GROUP = "conflict_split"
# Sidebar headings, in queue order.
GROUP_TITLES: dict[str, str] = {
    SPLIT_GROUP: "Split by the conflict check",
    "shared_identifier": "Same email or phone, different first names",
    "exact_id_dob_mismatch": "Same email or phone and first name, different birth dates",
    "exact_id_other_mismatch": "Same email or phone, the other one differs",
    "exact_id_no_name": "Same email or phone, a name is missing",
    "weighted_id_mismatch": "Very similar records, different email or phone",
    "weighted_match_disabled": "Strong fuzzy match (fuzzy auto-merge is off)",
    "weighted_low_evidence": "Name and address agree, little else to compare",
    "weighted": "Similar records, no shared email or phone",
}
# What Approve / Reject mean for each kind of item, stored next to the raw choice.
OUTCOMES: dict[tuple[str, DecisionValue], str] = {
    ("pair", "approve"): "same_entity",
    ("pair", "reject"): "different_entities",
    ("split", "approve"): "split_correct",
    ("split", "reject"): "split_wrong",
}
DECISION_COLUMNS = (
    "decided_at",
    "item_id",
    "item_type",
    "rule",
    "record_ids",
    "decision",
    "outcome",
)
# Fields shown side by side, with the normalizer that decides "same" vs "different".
DISPLAY_FIELDS: tuple[tuple[str, Callable[[str | None], str | None]], ...] = (
    ("first_name", lambda v: normalize_name(v, is_first_name=True)),
    ("last_name", normalize_name),
    ("email", normalize_email),
    ("email_verified", lambda v: (v or "").strip().lower() or None),
    ("phone", normalize_phone),
    ("address", normalize_address),
    ("city", lambda v: (v or "").strip().lower() or None),
    ("state", lambda v: (v or "").strip().upper() or None),
    ("zip", lambda v: (v or "").strip() or None),
    ("dob", normalize_date),
    ("source", lambda v: (v or "").strip() or None),
    ("updated_at", normalize_date),
)


# ------------------------------------------------------------- plain words


def _matched_ids(field_scores: Mapping[str, float]) -> list[str]:
    return [k for k in ("email", "phone") if field_scores.get(k) == 1.0]


def _ids_phrase(ids: Sequence[str]) -> str:
    return "Same " + " and ".join(ids) if ids else "No shared email or phone"


def describe_pair(explanation: Mapping[str, Any]) -> str:
    """One sentence saying why a pair is in the queue."""
    rule = str(explanation["rule"])
    scores: Mapping[str, float] = explanation.get("field_scores", {})
    ids = _matched_ids(scores)
    same = _ids_phrase(ids)
    other = [k for k in ("email", "phone") if k in scores and k not in ids]
    if rule == "shared_identifier":
        return f"{same}, different first names"
    if rule == "exact_id_dob_mismatch":
        return f"{same} and same first name, different dates of birth"
    if rule == "exact_id_other_mismatch":
        differs = (
            " and ".join(f"different {k}s" for k in other) or "the other ID differs"
        )
        return f"{same}, {differs}; first names similar but not identical"
    if rule == "exact_id_no_name":
        return f"{same}, but a first name is missing"
    score = float(explanation.get("score", 0.0))
    if rule == "weighted_id_mismatch":
        differs = " and ".join(other) or "identifiers"
        return f"Very similar records (score {score:.2f}), but the {differs} differ"
    if rule == "weighted_match_disabled":
        return f"Strong fuzzy match (score {score:.2f}); fuzzy auto-merge is turned off"
    if rule == "weighted_low_evidence":
        agree = " and ".join(
            k for k in ("name", "address") if scores.get(k, 0.0) >= 0.9
        )
        return f"{(agree or 'Some fields').capitalize()} agree, but too little else to compare"
    top = sorted(scores, key=lambda k: -scores[k])[:2]
    return f"No shared email or phone; similar {' and '.join(top)} (score {score:.2f})"


def describe_edge(explanation: Mapping[str, Any]) -> str:
    """Why one MATCH edge linked two records."""
    if explanation.get("rule") == HUMAN_RULE:
        return "Approved by a reviewer"
    first = explanation.get("checks", {}).get("first_name")
    names = f", first names agree ({first:.2f})" if first is not None else ""
    return f"{_ids_phrase(_matched_ids(explanation.get('field_scores', {})))}{names}"


def describe_split(split: Mapping[str, Any]) -> str:
    n = len(split["component"])
    parts = len(split["clusters"])
    conflicts: list[list[str]] = split["conflicts"]
    if not conflicts and split.get("dob_mismatches"):  # same name, different DOBs
        a, b = split["dob_mismatches"][0]
        return (
            f"{n} records linked by exact matches, but {a} and {b} share a first name "
            f"and have different birth dates; split into {parts} clusters"
        )
    if not conflicts:  # split only by a reviewer's "different people" decision
        a, b = split["human_rejected"][0]
        return f"{n} records linked, but a reviewer kept {a} and {b} apart; split into {parts} clusters"
    a, b = conflicts[0]
    more = (
        f" (+{len(conflicts) - 1} more conflicting pairs)" if len(conflicts) > 1 else ""
    )
    return (
        f"{n} records linked by exact matches, but {a} and {b} differ on both email "
        f"and phone{more}; split into {parts} clusters"
    )


# ------------------------------------------------------------------ queue


class QueueItem(TypedDict):
    id: str
    kind: Literal["pair", "split"]
    group: str
    title: str
    score: float | None
    record_ids: list[str]
    decision: DecisionValue | None


class FieldRow(TypedDict):
    field: str
    values: list[str | None]
    agreement: Agreement


@dataclass
class ReviewQueue:
    items: list[QueueItem]
    evidence: dict[str, dict[str, Any]]  # item id -> explanation or split record
    records: Mapping[str, RawRecord]
    decisions: dict[str, DecisionValue] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        totals: Counter[str] = Counter(i["group"] for i in self.items)
        decided: Counter[str] = Counter(
            i["group"] for i in self.items if i["id"] in self.decisions
        )
        groups = [
            {
                "group": g,
                "title": GROUP_TITLES.get(g, g),
                "total": totals[g],
                "decided": decided[g],
            }
            for g in GROUP_TITLES
            if totals[g]
        ]
        items = [{**i, "decision": self.decisions.get(i["id"])} for i in self.items]
        return {"groups": groups, "items": items}

    def detail(self, item_id: str) -> dict[str, Any]:
        item = next((i for i in self.items if i["id"] == item_id), None)
        if item is None:
            raise KeyError(item_id)
        ids = item["record_ids"]
        return {
            **item,
            "decision": self.decisions.get(item_id),
            "records": [dict(self.records[r]) for r in ids],
            "fields": compare_fields([self.records[r] for r in ids]),
            "evidence": self.evidence[item_id],
        }


def compare_fields(records: Sequence[RawRecord]) -> list[FieldRow]:
    """Display rows: raw values per record, and whether their normalized forms agree."""
    rows: list[FieldRow] = []
    for name, norm in DISPLAY_FIELDS:
        values = [(r.get(name) or "").strip() or None for r in records]
        present = [v for v in values if v]
        agreement: Agreement
        if len(present) < 2:
            agreement = "missing"  # nothing to compare
        elif len({norm(v) for v in present}) == 1:
            agreement = "same"
        else:
            agreement = "different"
        rows.append({"field": name, "values": values, "agreement": agreement})
    return rows


def _group_order(item: QueueItem) -> tuple[int, float]:
    order = list(GROUP_TITLES)
    group = order.index(item["group"]) if item["group"] in order else len(order)
    return group, -(item["score"] or 0.0)


def build_queue(
    review_pairs: Iterable[Mapping[str, Any]],
    splits: Iterable[Mapping[str, Any]],
    records: Mapping[str, RawRecord],
) -> ReviewQueue:
    items: list[QueueItem] = []
    evidence: dict[str, dict[str, Any]] = {}
    for split in splits:
        item_id = f"S:{split['component'][0]}"
        items.append(
            {
                "id": item_id,
                "kind": "split",
                "group": SPLIT_GROUP,
                "title": describe_split(split),
                "score": None,
                "record_ids": list(split["component"]),
                "decision": None,
            }
        )
        evidence[item_id] = {
            **split,
            "edges": [
                {**e, "description": describe_edge(e)} for e in split.get("edges", [])
            ],
        }
    for pair in review_pairs:
        item_id = f"P:{pair['left_id']}:{pair['right_id']}"
        items.append(
            {
                "id": item_id,
                "kind": "pair",
                "group": str(pair["rule"]),
                "title": describe_pair(pair),
                "score": float(pair["score"]),
                "record_ids": [str(pair["left_id"]), str(pair["right_id"])],
                "decision": None,
            }
        )
        evidence[item_id] = dict(pair)
    items.sort(key=_group_order)
    return ReviewQueue(items, evidence, records)


# ------------------------------------------------------------ persistence


def write_review_pairs(path: Path, results: Iterable[PairResult]) -> int:
    """One JSON line per REVIEW pair: ids plus the scorer's explanation."""
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for r in results:
            if r.decision is Decision.REVIEW:
                row = {"left_id": r.left_id, "right_id": r.right_id, **r.explanation()}
                fh.write(json.dumps(row, sort_keys=True) + "\n")
                n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_decisions(path: Path) -> dict[str, DecisionValue]:
    """item id -> latest decision."""
    if not path.exists():
        return {}
    latest: dict[str, DecisionValue] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["decision"] in ("approve", "reject"):
                latest[row["item_id"]] = row["decision"]  # type: ignore[assignment]
    return latest


_write_lock = threading.Lock()


def record_decision(
    path: Path, queue: ReviewQueue, item_id: str, decision: DecisionValue
) -> dict[str, str]:
    """Append one decision row (creating the file with a header) and return it."""
    item = next((i for i in queue.items if i["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    row = {
        "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "item_id": item_id,
        "item_type": item["kind"],
        "rule": item["group"],
        "record_ids": ";".join(item["record_ids"]),
        "decision": decision,
        "outcome": OUTCOMES[(item["kind"], decision)],
    }
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, DECISION_COLUMNS, lineterminator="\n")
            if new:
                writer.writeheader()
            writer.writerow(row)
        queue.decisions[item_id] = decision
    return row


# ------------------------------------------------- applying decisions (M7+)

# Report statuses (decisions_applied.jsonl), see docs/architecture.md:
#   merged               approved pair now shares a cluster
#   cut_by_safety_rule   approved pair left apart: joining would merge conflicting IDs
#   separated            rejected pair was together automatically, now apart
#   already_apart        rejected pair was never in one cluster; kept apart from now on
#   split_kept           split confirmed; nothing to change
#   refused_safety_rule  "should be one entity" would merge conflicting IDs
#   refused_dob_rule     "should be one entity" on a split made only by a birth-date
#                        mismatch: approve that REVIEW pair instead (one pair at a time)
#   stale                the item is no longer in this run's review queue


def read_decision_rows(path: Path) -> dict[str, dict[str, str]]:
    """item id -> its latest decision row (the CSV is append-only)."""
    if not path.exists():
        return {}
    latest: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("decision") in ("approve", "reject"):
                latest[row["item_id"]] = row
    return latest


@dataclass
class DecisionPlan:
    """Review decisions turned into clustering inputs, plus a report row per item."""

    human_links: list[PairResult] = field(default_factory=list)
    cannot_link: list[Pair] = field(default_factory=list)
    report: dict[str, dict[str, str]] = field(default_factory=dict)


def plan_decisions(
    rows: Mapping[str, Mapping[str, str]],
    results: Iterable[PairResult],
    splits: Iterable[SplitInfo],
) -> DecisionPlan:
    """Check each decision against this run and turn it into a link or constraint.

    A decision only applies to the item it was made on: a pair must still be a
    REVIEW pair, a split must still have exactly the same members. Anything else
    (regenerated data, changed config) is reported as stale and ignored.
    """
    review = {
        (r.left_id, r.right_id): r for r in results if r.decision is Decision.REVIEW
    }
    components = {tuple(s.component): s for s in splits}
    plan = DecisionPlan()
    for item_id, row in sorted(rows.items()):
        ids = tuple(sorted(row["record_ids"].split(";")))
        status: str = "pending"
        reason = ""
        if row["item_type"] == "pair":
            res = review.get((ids[0], ids[1])) if len(ids) == 2 else None
            if res is None:
                status, reason = "stale", "not a REVIEW pair in this run"
            elif row["outcome"] == "same_entity":
                plan.human_links.append(
                    replace(res, decision=Decision.MATCH, rule=HUMAN_RULE)
                )
            else:
                plan.cannot_link.append((ids[0], ids[1]))
        elif ids not in components:
            status, reason = "stale", "no split with these members in this run"
        elif row["outcome"] == "split_correct":
            status = "split_kept"
        elif not components[ids].conflicts and components[ids].dob_mismatches:
            status = "refused_dob_rule"
            reason = (
                "split by a birth-date mismatch; approve the REVIEW pair itself "
                "to merge those two records"
            )
        else:
            status = "refused_safety_rule"
            reason = "would merge records whose email and phone both differ"
        plan.report[item_id] = {
            "item_id": item_id,
            "item_type": row["item_type"],
            "outcome": row["outcome"],
            "decided_at": row["decided_at"],
            "record_ids": ";".join(ids),
            "status": status,
            "reason": reason,
        }
    return plan


def finalize_report(
    plan: DecisionPlan,
    automatic: Mapping[str, str],
    reviewed: Mapping[str, str],
) -> list[dict[str, str]]:
    """Fill in what each applied pair decision did, from the automatic and reviewed
    record -> cluster maps."""
    rows = []
    for row in plan.report.values():
        if row["status"] == "pending":
            a, b = row["record_ids"].split(";")
            together = reviewed[a] == reviewed[b]
            if row["outcome"] == "same_entity":
                row["status"] = "merged" if together else "cut_by_safety_rule"
                if not together:
                    row["reason"] = "joining would merge conflicting identifiers"
            else:
                row["status"] = (
                    "separated" if automatic[a] == automatic[b] else "already_apart"
                )
        rows.append(row)
    return rows
