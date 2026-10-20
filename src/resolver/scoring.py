"""Pairwise scoring and match decisions (Milestone 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    MATCH = "MATCH"
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


@dataclass(frozen=True)
class Thresholds:
    match: float = 0.92
    review: float = 0.75


@dataclass
class PairResult:
    left_id: str
    right_id: str
    score: float
    decision: Decision
    field_scores: dict[str, float] = field(default_factory=dict)
    rule: str = ""  # which rule produced the decision, for the audit log


FIELD_WEIGHTS: dict[str, float] = {
    "email": 0.35,
    "phone": 0.25,
    "name": 0.25,
    "address": 0.15,
}


def has_conflicting_identifiers(left: dict, right: dict) -> bool:
    """SAFETY RULE: both emails present and different AND both phones present and different."""
    raise NotImplementedError


def score_pair(left: dict, right: dict, thresholds: Thresholds) -> PairResult:
    """Compute weighted similarity, apply hard rules, return an explainable result.

    Order of evaluation:
      1. conflicting identifiers -> NO_MATCH, rule="conflict"
      2. exact normalized email or phone match -> MATCH, rule="exact_id"
      3. weighted score vs thresholds -> MATCH / REVIEW / NO_MATCH, rule="weighted"
    Missing fields are excluded and the remaining weights re-normalized.
    """
    raise NotImplementedError
