"""The safety rule is the core promise of this project. These tests must never be weakened."""

from resolver.scoring import Decision, Thresholds, score_pair


def test_conflicting_identifiers_never_merge():
    left = {
        "name": "maria garcia",
        "email": "maria.g@example.com",
        "phone": "+13105550100",
    }
    right = {
        "name": "maria garcia",
        "email": "mgarcia@example.org",
        "phone": "+12135550199",
    }
    result = score_pair(left, right, Thresholds())
    assert result.decision is Decision.NO_MATCH
    assert result.rule == "conflict"


def test_exact_email_matches_despite_name_typo():
    left = {"name": "jonathan lee", "email": "jlee@example.com"}
    right = {"name": "jonathon lee", "email": "jlee@example.com"}
    assert score_pair(left, right, Thresholds()).decision is Decision.MATCH


def test_every_result_is_explainable():
    left = {"name": "ana silva", "address": "12 main street"}
    right = {"name": "anna silva", "address": "12 main st"}
    result = score_pair(left, right, Thresholds())
    assert result.rule
    assert set(result.field_scores) <= {"name", "address", "email", "phone"}
