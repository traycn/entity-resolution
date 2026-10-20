"""Pairwise scoring and match decisions (Milestone 4)."""

from __future__ import annotations

import functools
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypedDict

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from resolver.normalize import (
    normalize_address,
    normalize_date,
    normalize_email,
    normalize_name,
    normalize_phone,
)

SCORED_FIELDS = ("email", "phone", "name", "address")

# Repo-root config.toml unless RESOLVER_CONFIG points elsewhere.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"


class Decision(str, Enum):
    MATCH = "MATCH"
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


class NormalizedRecord(TypedDict, total=False):
    """One record after normalization. Absent or None fields are treated as missing."""

    record_id: str
    name: str | None  # "first last"; first name nickname-normalized
    first_name: str | None  # nickname-normalized; falls back to name's first token
    last_name: str | None
    email: str | None
    phone: str | None  # E.164
    address: str | None
    dob: str | None  # ISO date; a gate on exact-ID matches, not a scored field


@dataclass(frozen=True)
class ScoringConfig:
    match: float
    review: float
    min_match_weight: float
    min_first_name_similarity: float
    allow_weighted_match: bool
    weights: Mapping[str, float]


def _config_path() -> Path:
    env = os.environ.get("RESOLVER_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


@functools.cache
def load_config(path: Path | None = None) -> ScoringConfig:
    """Read thresholds and field weights from config.toml (cached per path)."""
    with (path or _config_path()).open("rb") as fh:
        raw = tomllib.load(fh)
    thresholds, weights, policy = raw["thresholds"], raw["weights"], raw["policy"]
    unknown = set(weights) - set(SCORED_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields in [weights]: {sorted(unknown)}")
    config = ScoringConfig(
        match=float(thresholds["match"]),
        review=float(thresholds["review"]),
        min_match_weight=float(thresholds["min_match_weight"]),
        min_first_name_similarity=float(thresholds["min_first_name_similarity"]),
        allow_weighted_match=bool(policy["allow_weighted_match"]),
        weights={k: float(v) for k, v in weights.items()},
    )
    if not 0.0 <= config.review <= config.match <= 1.0:
        raise ValueError("thresholds must satisfy 0 <= review <= match <= 1")
    return config


@dataclass(frozen=True)
class Thresholds:
    match: float = field(default_factory=lambda: load_config().match)
    review: float = field(default_factory=lambda: load_config().review)


class Explanation(TypedDict):
    decision: str
    rule: str
    score: float
    field_scores: dict[str, float]
    weights: dict[str, float]
    checks: dict[str, float]


@dataclass
class PairResult:
    left_id: str
    right_id: str
    score: float
    decision: Decision
    field_scores: dict[str, float] = field(default_factory=dict)
    rule: str = ""  # which rule produced the decision, for the audit log
    weights: dict[str, float] = field(default_factory=dict)  # re-normalized, used
    checks: dict[str, float] = field(default_factory=dict)  # gate scores, not weighted

    def explanation(self) -> Explanation:
        return {
            "decision": self.decision.value,
            "rule": self.rule,
            "score": self.score,
            "field_scores": dict(self.field_scores),
            "weights": dict(self.weights),
            "checks": dict(self.checks),
        }


def normalize_record(raw: Mapping[str, str | None]) -> NormalizedRecord:
    """Build a NormalizedRecord from a raw source row (first_name, last_name, ...)."""
    first = normalize_name(raw.get("first_name"), is_first_name=True)
    last = normalize_name(raw.get("last_name"))
    name = " ".join(p for p in (first, last) if p) or None
    return {
        "record_id": str(raw.get("record_id") or ""),
        "name": name,
        "first_name": first,
        "last_name": last,
        "email": normalize_email(raw.get("email")),
        "phone": normalize_phone(raw.get("phone")),
        "address": normalize_address(raw.get("address")),
        "dob": normalize_date(raw.get("dob")),
    }


def has_conflicting_identifiers(
    left: NormalizedRecord, right: NormalizedRecord
) -> bool:
    """SAFETY RULE: both emails present and different AND both phones present and different."""
    le, re_ = left.get("email"), right.get("email")
    lp, rp = left.get("phone"), right.get("phone")
    email_conflict = bool(le and re_ and le != re_)
    phone_conflict = bool(lp and rp and lp != rp)
    return email_conflict and phone_conflict


def _similarity(name: str, a: str, b: str) -> float:
    if a == b:
        return 1.0
    if name == "phone":
        return 0.0  # a different number is a different number
    if name == "email":
        return fuzz.ratio(a, b) / 100.0
    # name / address: token order does not matter (swapped first/last names)
    return fuzz.token_sort_ratio(a, b) / 100.0


def field_scores(left: NormalizedRecord, right: NormalizedRecord) -> dict[str, float]:
    """Similarity in [0, 1] for every field present on both records."""
    scores: dict[str, float] = {}
    for name in SCORED_FIELDS:
        a, b = left.get(name), right.get(name)
        if isinstance(a, str) and isinstance(b, str) and a and b:
            scores[name] = round(_similarity(name, a, b), 4)
    return scores


def _first_name(record: NormalizedRecord) -> str | None:
    if record.get("first_name"):
        return record.get("first_name")
    name = record.get("name")
    return name.split(" ", 1)[0] if name else None


def first_name_similarity(
    left: NormalizedRecord, right: NormalizedRecord
) -> float | None:
    """Jaro-Winkler similarity of the first names, or None if either is missing.

    If both records have a last name, a swapped first/last order also counts
    (both cross pairs must agree).
    """
    lf, rf = _first_name(left), _first_name(right)
    if not lf or not rf:
        return None
    sim = JaroWinkler.similarity(lf, rf)
    ll, rl = left.get("last_name"), right.get("last_name")
    if ll and rl:
        swapped = min(JaroWinkler.similarity(lf, rl), JaroWinkler.similarity(ll, rf))
        sim = max(sim, swapped)
    return round(sim, 4)


def score_pair(
    left: NormalizedRecord,
    right: NormalizedRecord,
    thresholds: Thresholds,
    config: ScoringConfig | None = None,
) -> PairResult:
    """Compute weighted similarity, apply hard rules, return an explainable result.

    Order of evaluation:
      1. conflicting identifiers -> NO_MATCH, rule="conflict"
      2. exact normalized email or phone match -> MATCH, rule="exact_id", but only
         if the first names agree; otherwise REVIEW, rule="shared_identifier"
         (a household phone or family email) or rule="exact_id_no_name".
         Identical first names (after nickname normalization) with both birth
         dates present and different -> REVIEW, rule="exact_id_dob_mismatch".
         Similar but not identical first names with the other identifier present
         on both records and different -> REVIEW, rule="exact_id_other_mismatch"
      3. weighted score vs thresholds -> MATCH / REVIEW / NO_MATCH, rule="weighted"
         A would-be MATCH is capped at REVIEW when it rests on too little weight
         (rule="weighted_low_evidence") or when both records carry a differing
         email or phone (rule="weighted_id_mismatch") or when weighted auto-merge
         is disabled in config (rule="weighted_match_disabled"). No comparable
         fields -> NO_MATCH, rule="no_evidence".
    Missing fields are excluded and the remaining weights re-normalized.
    Field scores and weights are recorded whichever rule fires.
    """
    cfg = config or load_config()
    scores = field_scores(left, right)
    present = {k: cfg.weights[k] for k in scores if cfg.weights.get(k, 0.0) > 0.0}
    total = sum(present.values())
    weights = {k: round(w / total, 4) for k, w in present.items()} if total else {}
    score = (
        round(sum(scores[k] * w for k, w in present.items()) / total, 4)
        if total
        else 0.0
    )

    checks: dict[str, float] = {}

    def result(decision: Decision, rule: str) -> PairResult:
        return PairResult(
            left_id=left.get("record_id", ""),
            right_id=right.get("record_id", ""),
            score=score,
            decision=decision,
            field_scores=scores,
            rule=rule,
            weights=weights,
            checks=checks,
        )

    if has_conflicting_identifiers(left, right):
        return result(Decision.NO_MATCH, "conflict")
    if scores.get("email") == 1.0 or scores.get("phone") == 1.0:
        first_sim = first_name_similarity(left, right)
        if first_sim is None:
            return result(Decision.REVIEW, "exact_id_no_name")
        checks["first_name"] = first_sim
        if first_sim < cfg.min_first_name_similarity:
            return result(Decision.REVIEW, "shared_identifier")
        if first_sim == 1.0:
            # Identical first names: a differing second identifier is usually the
            # same person's new phone or email, but a parent and child with the same
            # name (Jr./Sr.) share a household identifier; their birth dates differ.
            ld, rd = left.get("dob"), right.get("dob")
            if ld and rd:
                checks["dob"] = 1.0 if ld == rd else 0.0
                if ld != rd:
                    return result(Decision.REVIEW, "exact_id_dob_mismatch")
            return result(Decision.MATCH, "exact_id")
        # Similar but not identical first names, and the other identifier is on both
        # records and differs: the same counter-evidence as weighted_id_mismatch (a
        # household phone with each person's own email, e.g. Dustin / Destiny).
        if scores.get("email", 1.0) < 1.0 or scores.get("phone", 1.0) < 1.0:
            return result(Decision.REVIEW, "exact_id_other_mismatch")
        return result(Decision.MATCH, "exact_id")
    if not total:
        return result(Decision.NO_MATCH, "no_evidence")
    if score >= thresholds.match:
        if total < cfg.min_match_weight:
            return result(Decision.REVIEW, "weighted_low_evidence")
        # Past exact_id, an identifier on both sides means the two values differ:
        # counter-evidence, however similar the strings look.
        if "email" in scores or "phone" in scores:
            return result(Decision.REVIEW, "weighted_id_mismatch")
        if not cfg.allow_weighted_match:
            return result(Decision.REVIEW, "weighted_match_disabled")
        return result(Decision.MATCH, "weighted")
    if score >= thresholds.review:
        return result(Decision.REVIEW, "weighted")
    return result(Decision.NO_MATCH, "weighted")
