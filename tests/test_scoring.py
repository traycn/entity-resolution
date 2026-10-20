from pathlib import Path

import pytest

from resolver.scoring import (
    Decision,
    NormalizedRecord,
    Thresholds,
    first_name_similarity,
    has_conflicting_identifiers,
    load_config,
    normalize_record,
    score_pair,
)


def _write_config(
    tmp_path: Path, *, review: float = 0.4, allow_weighted_match: bool = True
) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f"[policy]\nallow_weighted_match = {str(allow_weighted_match).lower()}\n"
        f"[thresholds]\nmatch = 0.5\nreview = {review}\nmin_match_weight = 0.0\n"
        "min_first_name_similarity = 0.7\n"
        "[weights]\nname = 1.0\n"
    )
    return path


def test_thresholds_and_weights_come_from_config() -> None:
    cfg = load_config()
    assert Thresholds() == Thresholds(match=cfg.match, review=cfg.review)
    assert set(cfg.weights) == {"email", "phone", "name", "address"}


def test_shipped_policy_is_exact_id_only() -> None:
    cfg = load_config()
    assert cfg.allow_weighted_match is False
    assert cfg.min_match_weight == 0.5


def test_custom_config_file(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path))
    left: NormalizedRecord = {"name": "ana silva", "address": "1 main street"}
    right: NormalizedRecord = {"name": "ana silva", "address": "99 other road"}
    result = score_pair(left, right, Thresholds(cfg.match, cfg.review), cfg)
    assert result.weights == {"name": 1.0}  # address has no weight -> ignored
    assert result.decision is Decision.MATCH


def test_weighted_match_disabled_caps_at_review(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, allow_weighted_match=False))
    left: NormalizedRecord = {"name": "ana silva"}
    right: NormalizedRecord = {"name": "ana silva"}
    result = score_pair(left, right, Thresholds(cfg.match, cfg.review), cfg)
    assert (result.decision, result.rule) == (
        Decision.REVIEW,
        "weighted_match_disabled",
    )


def test_bad_thresholds_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="review <= match"):
        load_config(_write_config(tmp_path, review=0.9))


def test_normalize_record_maps_first_name_nicknames_only() -> None:
    rec = normalize_record(
        {
            "record_id": "R1",
            "first_name": " Bob ",
            "last_name": "Will",
            "email": "Bob.Will+shop@googlemail.com",
            "phone": "(310) 555-0100",
            "address": "12 Main St.",
        }
    )
    assert rec == {
        "record_id": "R1",
        "name": "robert will",  # surname "Will" is not turned into "william"
        "first_name": "robert",
        "last_name": "will",
        "email": "bobwill@gmail.com",
        "phone": "+13105550100",
        "address": "12 main street",
    }


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (
            {"email": "a@x.com", "phone": "+1"},
            {"email": "b@x.com", "phone": "+2"},
            True,
        ),
        (
            {"email": "a@x.com", "phone": "+1"},
            {"email": "a@x.com", "phone": "+2"},
            False,
        ),
        (
            {"email": "a@x.com", "phone": "+1"},
            {"email": "b@x.com", "phone": "+1"},
            False,
        ),
        ({"email": "a@x.com"}, {"email": "b@x.com", "phone": "+2"}, False),
        (
            {"email": "a@x.com", "phone": None},
            {"email": "b@x.com", "phone": "+2"},
            False,
        ),
        ({}, {}, False),
    ],
)
def test_has_conflicting_identifiers(
    left: NormalizedRecord, right: NormalizedRecord, expected: bool
) -> None:
    assert has_conflicting_identifiers(left, right) is expected


def test_exact_phone_matches() -> None:
    left: NormalizedRecord = {
        "record_id": "A",
        "name": "ana silva",
        "phone": "+13105550100",
    }
    right: NormalizedRecord = {
        "record_id": "B",
        "name": "anna silva",
        "phone": "+13105550100",
    }
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.MATCH, "exact_id")
    assert (result.left_id, result.right_id) == ("A", "B")


def test_name_and_address_alone_never_auto_merge() -> None:
    # Could be a same-named family member at the same address.
    left: NormalizedRecord = {"name": "john smith", "address": "12 main street"}
    right: NormalizedRecord = {"name": "smith john", "address": "12 main street"}
    result = score_pair(left, right, Thresholds())
    assert result.field_scores == {"name": 1.0, "address": 1.0}
    assert (result.decision, result.rule) == (Decision.REVIEW, "weighted_low_evidence")


def _person(
    first: str, last: str, *, phone: str | None = None, email: str | None = None
) -> NormalizedRecord:
    return {
        "name": f"{first} {last}",
        "first_name": first,
        "last_name": last,
        "address": "12 main street",
        "phone": phone,
        "email": email,
    }


def test_shared_phone_different_first_name_is_review_not_match() -> None:
    left = _person("kenneth", "lopez", phone="+13105550100", email="kl@x.com")
    right = _person("jeremy", "lopez", phone="+13105550100", email="jl@x.com")
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.REVIEW, "shared_identifier")
    assert result.checks["first_name"] < load_config().min_first_name_similarity


def test_shared_email_different_first_name_is_review_not_match() -> None:
    left = _person("daniel", "ross", email="rossfamily@gmail.com")
    right = _person("michele", "ross", email="rossfamily@gmail.com")
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.REVIEW, "shared_identifier")


@pytest.mark.parametrize(
    ("left_first", "left_last", "right_first", "right_last"),
    [
        ("robert", "lopez", "robert", "lopez"),  # Bob -> robert via normalize_record
        ("jonathan", "lee", "jonathon", "lee"),  # typo
        ("john", "smith", "smith", "john"),  # swapped first/last
    ],
)
def test_shared_phone_with_agreeing_first_name_matches(
    left_first: str, left_last: str, right_first: str, right_last: str
) -> None:
    left = _person(left_first, left_last, phone="+13105550100")
    right = _person(right_first, right_last, phone="+13105550100")
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.MATCH, "exact_id")
    assert "first_name" in result.explanation()["checks"]


def test_exact_id_without_names_is_review() -> None:
    left: NormalizedRecord = {"phone": "+13105550100"}
    right: NormalizedRecord = {"phone": "+13105550100"}
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.REVIEW, "exact_id_no_name")


def test_first_name_similarity_falls_back_to_name_token() -> None:
    left: NormalizedRecord = {"name": "maria garcia"}
    right: NormalizedRecord = {"name": "maria lopez"}
    assert first_name_similarity(left, right) == 1.0
    assert first_name_similarity(left, {"email": "x@y.com"}) is None


def test_similar_but_different_email_is_capped_at_review() -> None:
    # Looks like the same person's second address, but a different identifier
    # must never auto-merge on its own.
    left: NormalizedRecord = {
        "name": "kathryn jones",
        "email": "kathrynkjones@gmail.com",
        "address": "7785 jamie cliff",
    }
    right: NormalizedRecord = {
        "name": "kathryn jones",
        "email": "kathrynjones83@gmail.com",
        "address": "7785 jamie cliff",
    }
    result = score_pair(left, right, Thresholds())
    assert result.score >= Thresholds().match
    assert (result.decision, result.rule) == (Decision.REVIEW, "weighted_id_mismatch")


def test_name_only_agreement_is_capped_at_review() -> None:
    left: NormalizedRecord = {"name": "maria garcia"}
    right: NormalizedRecord = {"name": "maria garcia"}
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.REVIEW, "weighted_low_evidence")


def test_no_shared_fields_is_no_match() -> None:
    left: NormalizedRecord = {"name": "maria garcia"}
    right: NormalizedRecord = {"address": "12 main street"}
    result = score_pair(left, right, Thresholds())
    assert (result.decision, result.rule) == (Decision.NO_MATCH, "no_evidence")
    assert result.field_scores == {}


def test_missing_fields_reweight_and_explanation_is_complete() -> None:
    left: NormalizedRecord = {"name": "ana silva", "address": "12 main street"}
    right: NormalizedRecord = {"name": "anna silva", "address": "12 main street"}
    result = score_pair(left, right, Thresholds())
    assert result.weights == {"name": 0.625, "address": 0.375}
    assert sum(result.weights.values()) == pytest.approx(1.0)
    explanation = result.explanation()
    assert explanation["rule"] == result.rule
    assert explanation["decision"] == result.decision.value
    assert explanation["field_scores"] == result.field_scores


def test_conflict_still_records_field_scores() -> None:
    left: NormalizedRecord = {"name": "a b", "email": "a@x.com", "phone": "+1"}
    right: NormalizedRecord = {"name": "a b", "email": "b@x.com", "phone": "+2"}
    result = score_pair(left, right, Thresholds())
    assert result.rule == "conflict"
    assert set(result.field_scores) == {"name", "email", "phone"}
