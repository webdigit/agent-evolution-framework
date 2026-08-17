from copy import deepcopy
import hashlib
import json

import pytest
import jsonschema

from aef.filesystem import apply_workspace, load_workspace


recommendations = pytest.importorskip("aef.promotion_recommendations")


EVIDENCE = {
    "level": "L1",
    "xp": 50,
    "cases": 10,
    "trust": 0.90,
    "complex_cases": 0,
    "recent_significant_errors": 0,
    "probation": False,
}


def empty_evaluations():
    return {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "promotion_recommendations": [],
    }


def test_new_empty_evaluation_state_is_explicitly_versioned():
    assert recommendations.empty_evaluations() == {
        "schema_version": "1.0.0",
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "promotion_recommendations": [],
    }
    schema_validator().validate(recommendations.empty_evaluations())


def expected_digest(evidence):
    payload = json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_false_readiness_creates_no_recommendation():
    state = deepcopy(EVIDENCE)
    state["cases"] = 9

    status, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        empty_evaluations(), state, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )

    assert status == "NO_CHANGE"
    assert out == empty_evaluations()
    assert recommendation_id is None
    assert created is False


def test_first_true_readiness_creates_explainable_pending_recommendation():
    source = empty_evaluations()
    before = deepcopy(source)

    status, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        source, EVIDENCE, scope="competency", competency_id="record-classification",
        detected_at="2026-08-14T10:00:00Z",
    )

    evidence = {
        "xp": 50,
        "cases": 10,
        "trust": 0.90,
        "complex_cases": 0,
        "recent_significant_errors": 0,
    }
    assert status == "CHANGE"
    assert created is True
    assert recommendation_id == "promotion:competency:sha256-0fc7ee538068e4090d67e516131c7e36d2e5fe3886570753a75912d085b69396:L1:L2"
    assert out["promotion_recommendations"] == [{
        "id": recommendation_id,
        "type": "promotion",
        "scope": "competency",
        "competency_id": "record-classification",
        "from_level": "L1",
        "to_level": "L2",
        "status": "pending",
        "detected_at": "2026-08-14T10:00:00Z",
        "evidence": evidence,
        "evidence_digest": expected_digest(evidence),
    }]
    assert source == before


def test_replay_keeps_first_snapshot_and_never_duplicates():
    _, once, recommendation_id, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )
    changed_evidence = deepcopy(EVIDENCE)
    changed_evidence["xp"] = 99

    status, twice, replay_id, created = recommendations.ensure_pending_promotion(
        once, changed_evidence, scope="career", competency_id=None,
        detected_at="2026-08-15T10:00:00Z",
    )

    assert status == "NO_CHANGE"
    assert replay_id == recommendation_id == "promotion:career:global:L1:L2"
    assert created is False
    assert twice == once
    assert len(twice["promotion_recommendations"]) == 1


def test_career_and_competency_recommendations_have_distinct_stable_ids():
    _, state, career_id, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )
    _, state, competency_id, _ = recommendations.ensure_pending_promotion(
        state, EVIDENCE, scope="competency", competency_id="general",
        detected_at="2026-08-14T10:00:00Z",
    )

    assert career_id == "promotion:career:global:L1:L2"
    assert competency_id == "promotion:competency:sha256-0feae16d55365acf07fe9f909834361ba6ee606854746539230bdc84a6a24cee:L1:L2"
    assert len(state["promotion_recommendations"]) == 2


def test_status_vocabulary_reserves_future_evaluation_outcomes():
    assert recommendations.PROMOTION_RECOMMENDATION_STATUSES == {
        "pending", "approved", "rejected", "withdrawn", "superseded"
    }


def test_created_recommendation_validates_as_evaluation_state():
    _, evaluations, _, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    schema = json.loads((root / "src/aef/schemas/evaluation.schema.json").read_text(encoding="utf-8"))

    assert recommendations.validate_promotion_recommendation_state(evaluations) is evaluations
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(evaluations)


def test_historical_evaluation_without_recommendation_collection_is_valid():
    evaluations = {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
    }
    before = deepcopy(evaluations)

    assert recommendations.validate_promotion_recommendation_state(evaluations) is evaluations
    assert recommendations.recommendation_metadata(evaluations, []) == {
        "review_required": False,
        "new_recommendations": [],
    }
    assert evaluations == before


def test_false_readiness_preserves_historical_state_without_materializing_collection():
    evaluations = {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "unknown": {"preserve": True},
    }
    source = deepcopy(evaluations)
    state = deepcopy(EVIDENCE)
    state["cases"] = 9

    status, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        evaluations, state, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )

    assert status == "NO_CHANGE"
    assert out == source == evaluations
    assert "promotion_recommendations" not in out
    assert recommendation_id is None
    assert created is False


def test_true_readiness_materializes_collection_only_for_first_recommendation():
    evaluations = {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "unknown": "preserved",
    }

    status, out, _, created = recommendations.ensure_pending_promotion(
        evaluations, EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )

    assert status == "CHANGE"
    assert created is True
    assert len(out["promotion_recommendations"]) == 1
    assert out["unknown"] == "preserved"
    assert "promotion_recommendations" not in evaluations


def test_false_readiness_causes_no_filesystem_rewrite(tmp_path):
    historical = {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
    }
    path = ".agent/state/evaluations.json"
    apply_workspace(tmp_path, load_workspace(tmp_path), {"files": {path: historical}})
    current = load_workspace(tmp_path)
    before = (tmp_path / path).read_bytes()
    state = deepcopy(EVIDENCE)
    state["cases"] = 9

    status, evaluations, _, _ = recommendations.ensure_pending_promotion(
        current["files"][path], state, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )
    desired = deepcopy(current)
    desired["files"][path] = evaluations
    diff = apply_workspace(tmp_path, current, desired)

    assert status == "NO_CHANGE"
    assert diff == {"created": [], "modified": [], "removed": []}
    assert (tmp_path / path).read_bytes() == before


def test_malformed_collection_and_source_metrics_raise_stable_domain_error():
    evaluations = {"promotion_recommendations": None}
    state = deepcopy(EVIDENCE)
    state["trust"] = True

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.recommendation_metadata(evaluations, [])
    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            empty_evaluations(), state, scope="career", competency_id=None,
            detected_at="2026-08-14T10:00:00Z",
        )
    assert evaluations == {"promotion_recommendations": None}
    assert state["trust"] is True


def test_overlapping_invalid_evidence_is_rejected_by_domain_and_schema():
    _, evaluations, _, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )
    invalid = deepcopy(evaluations)
    invalid["promotion_recommendations"][0]["evidence"]["trust"] = True
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    schema = json.loads((root / "src/aef/schemas/evaluation.schema.json").read_text(encoding="utf-8"))

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(invalid)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(
            schema, format_checker=jsonschema.FormatChecker()
        ).validate(invalid)


def schema_validator():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    schema = json.loads((root / "src/aef/schemas/evaluation.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def valid_recommendations():
    return recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )[1]


@pytest.mark.parametrize("collection", [None, {}, "invalid", [None], ["invalid"]])
def test_malformed_collection_shapes_raise_domain_error_and_schema_error(collection):
    evaluations = empty_evaluations()
    evaluations["promotion_recommendations"] = collection
    before = deepcopy(evaluations)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(evaluations)
    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(evaluations)
    assert evaluations == before


def test_empty_and_duplicate_recommendation_ids_are_rejected_without_mutation():
    empty_id = valid_recommendations()
    empty_id["promotion_recommendations"][0]["id"] = ""
    duplicate = valid_recommendations()
    duplicate["promotion_recommendations"].append(
        deepcopy(duplicate["promotion_recommendations"][0])
    )

    for evaluations in (empty_id, duplicate):
        before = deepcopy(evaluations)
        with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
            recommendations.validate_promotion_recommendation_state(evaluations)
        assert evaluations == before
    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(empty_id)


@pytest.mark.parametrize(("field", "value"), [
    ("scope", "user"),
    ("competency_id", "unexpected"),
    ("from_level", "L0"),
    ("to_level", "L3"),
    ("detected_at", "2026-08-14"),
    ("detected_at", "2026-08-14T10:00:00"),
    ("evidence_digest", "sha256:not-a-digest"),
])
def test_invalid_recommendation_invariants_are_rejected_by_domain_and_schema(field, value):
    evaluations = valid_recommendations()
    evaluations["promotion_recommendations"][0][field] = value

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(evaluations)
    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(evaluations)


def test_competency_scope_requires_matching_competency_and_canonical_id():
    evaluations = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="competency", competency_id="analysis",
        detected_at="2026-08-14T10:00:00Z",
    )[1]
    invalid = deepcopy(evaluations)
    invalid["promotion_recommendations"][0]["competency_id"] = None

    assert recommendations.validate_promotion_recommendation_state(evaluations) is evaluations
    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(invalid)
    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(invalid)


def test_well_formed_but_incorrect_digest_is_rejected_by_domain_validator():
    evaluations = valid_recommendations()
    evaluations["promotion_recommendations"][0]["evidence_digest"] = "sha256:" + "0" * 64

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(evaluations)
    # JSON Schema owns the representation; semantic digest equality is the
    # domain validator's responsibility.
    schema_validator().validate(evaluations)


@pytest.mark.parametrize("status", ["approved", "rejected", "withdrawn", "superseded"])
def test_future_valid_status_is_preserved_and_never_applies_promotion(status):
    evaluations = valid_recommendations()
    evaluations["promotion_recommendations"][0]["status"] = status
    source = deepcopy(EVIDENCE)

    result, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        evaluations, source, scope="career", competency_id=None,
        detected_at="2026-08-15T10:00:00Z",
    )

    assert result == "NO_CHANGE"
    assert out == evaluations
    assert recommendation_id == "promotion:career:global:L1:L2"
    assert created is False
    assert recommendations.recommendation_metadata(out, []) == {
        "review_required": False, "new_recommendations": [],
    }
    assert source["level"] == "L1"


@pytest.mark.parametrize("trust", [True, False, -0.01, 1.01, "0.9"])
def test_invalid_trust_is_rejected_in_source_snapshot_without_mutation(trust):
    state = deepcopy(EVIDENCE)
    state["trust"] = trust
    before = deepcopy(state)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            empty_evaluations(), state, scope="career", competency_id=None,
            detected_at="2026-08-14T10:00:00Z",
        )

    assert state == before
    assert state["level"] == "L1"


@pytest.mark.parametrize(("field", "value"), [
    ("xp", True),
    ("xp", -1),
    ("xp", "50"),
    ("cases", True),
    ("cases", -1),
    ("cases", 1.5),
    ("complex_cases", False),
    ("complex_cases", -1),
    ("recent_significant_errors", True),
    ("recent_significant_errors", "0"),
])
def test_invalid_source_metrics_are_rejected_before_readiness(field, value):
    state = deepcopy(EVIDENCE)
    state[field] = value
    before = deepcopy(state)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            empty_evaluations(), state, scope="career", competency_id=None,
            detected_at="2026-08-14T10:00:00Z",
        )

    assert state == before


def test_digest_has_known_literal_and_is_independent_of_source_key_order():
    first = deepcopy(EVIDENCE)
    second = {key: EVIDENCE[key] for key in reversed(tuple(EVIDENCE))}

    first_out = recommendations.ensure_pending_promotion(
        empty_evaluations(), first, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )[1]
    second_out = recommendations.ensure_pending_promotion(
        empty_evaluations(), second, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )[1]
    first_digest = first_out["promotion_recommendations"][0]["evidence_digest"]
    second_digest = second_out["promotion_recommendations"][0]["evidence_digest"]

    assert first_digest == second_digest
    assert first_digest == "sha256:0040087530564ecf50925019a020cfe486ccf3c4c49d13fdd6d311432b443d92"


@pytest.mark.parametrize("change", ["significant_error", "source_level"])
def test_replay_preserves_old_snapshot_when_current_state_changes(change):
    evaluations = valid_recommendations()
    original = deepcopy(evaluations)
    current = deepcopy(EVIDENCE)
    if change == "significant_error":
        current["recent_significant_errors"] = 1
    else:
        current["level"] = "L2"

    status, out, _, created = recommendations.ensure_pending_promotion(
        evaluations, current, scope="career", competency_id=None,
        detected_at="2026-08-15T10:00:00Z",
    )

    assert status == "NO_CHANGE"
    assert created is False
    assert out == original
    assert out["promotion_recommendations"][0]["evidence"] == original["promotion_recommendations"][0]["evidence"]
    assert current["level"] in {"L1", "L2"}


def test_recommendation_output_has_no_mutable_aliases():
    evaluations = empty_evaluations()
    state = deepcopy(EVIDENCE)
    _, out, _, _ = recommendations.ensure_pending_promotion(
        evaluations, state, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )

    out["promotion_recommendations"][0]["evidence"]["cases"] = 999
    assert state["cases"] == 10
    assert evaluations["promotion_recommendations"] == []


def test_recommendation_round_trips_through_filesystem_without_promotion(tmp_path):
    evaluations = valid_recommendations()
    path = ".agent/state/evaluations.json"
    current = load_workspace(tmp_path)

    diff = apply_workspace(tmp_path, current, {"files": {path: evaluations}})
    reloaded = load_workspace(tmp_path)

    assert diff["created"] == [path]
    assert reloaded["files"][path] == evaluations
    assert recommendations.validate_promotion_recommendation_state(
        reloaded["files"][path]
    ) is reloaded["files"][path]
    assert "level" not in reloaded["files"][path]


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["xp", "trust"])
def test_non_finite_source_metrics_are_rejected_before_readiness_or_digest(field, non_finite):
    state = deepcopy(EVIDENCE)
    state[field] = non_finite
    before = deepcopy(state)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            empty_evaluations(), state, scope="career", competency_id=None,
            detected_at="2026-08-14T10:00:00Z",
        )

    assert state == before


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["xp", "trust"])
def test_non_finite_persisted_snapshot_and_document_are_rejected(field, non_finite):
    evaluations = valid_recommendations()
    evaluations["promotion_recommendations"][0]["evidence"][field] = non_finite
    before = deepcopy(evaluations)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(evaluations)
    with pytest.raises(ValueError):
        json.dumps(evaluations, allow_nan=False)

    assert evaluations == before


@pytest.mark.parametrize("timestamp", [
    "2026-08-14T10:00:00Z",
    "2026-08-14T10:00:00.123456Z",
    "2026-08-14T10:00:00+02:00",
])
def test_strict_rfc3339_timestamps_are_accepted(timestamp):
    status, evaluations, _, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
        detected_at=timestamp,
    )

    assert status == "CHANGE"
    assert evaluations["promotion_recommendations"][0]["detected_at"] == timestamp
    schema_validator().validate(evaluations)


@pytest.mark.parametrize("timestamp", [
    "2026-08-14T10:00:00+02:00:30",
    "2026-08-14T10:00:00",
    "2026-02-30T10:00:00Z",
])
def test_non_rfc3339_timestamps_are_rejected(timestamp):
    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            empty_evaluations(), EVIDENCE, scope="career", competency_id=None,
            detected_at=timestamp,
        )


@pytest.mark.parametrize(("scope", "competency_id"), [
    ("unknown", None),
    ("career", "analysis"),
    ("competency", None),
    ("competency", " analysis"),
])
def test_invalid_structural_parameters_are_rejected_when_readiness_is_false(
    scope, competency_id
):
    state = deepcopy(EVIDENCE)
    state["xp"] = 0
    before = deepcopy(state)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            empty_evaluations(), state, scope=scope, competency_id=competency_id,
            detected_at="2026-08-14T10:00:00Z",
        )

    assert state == before


def test_false_readiness_allows_absent_timestamp_without_calling_clock(monkeypatch):
    state = deepcopy(EVIDENCE)
    state["xp"] = 0
    monkeypatch.setattr(
        recommendations, "utc_now", lambda: pytest.fail("clock must not be called")
    )

    status, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        empty_evaluations(), state, scope="career", competency_id=None,
    )

    assert status == "NO_CHANGE"
    assert out == empty_evaluations()
    assert recommendation_id is None
    assert created is False


def test_changed_source_level_can_create_the_next_eligible_transition():
    evaluations = valid_recommendations()
    state = {
        **deepcopy(EVIDENCE),
        "level": "L2", "xp": 200, "cases": 30, "trust": 0.90,
    }

    status, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        evaluations, state, scope="career", competency_id=None,
        detected_at="2026-08-15T10:00:00Z",
    )

    assert status == "CHANGE"
    assert created is True
    assert recommendation_id == "promotion:career:global:L2:L3"
    assert {item["id"] for item in out["promotion_recommendations"]} == {
        "promotion:career:global:L1:L2", "promotion:career:global:L2:L3",
    }


def test_root_extension_is_preserved_when_recommendation_is_created():
    extension = {
        "vendor.example": {"opaque": [3, 2, 1], "authorization": "none"}
    }
    evaluations = {**empty_evaluations(), **deepcopy(extension)}

    status, out, _, _ = recommendations.ensure_pending_promotion(
        evaluations, EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )

    assert status == "CHANGE"
    assert out["vendor.example"] == extension["vendor.example"]
    assert evaluations["vendor.example"] == extension["vendor.example"]
    assert "schema_version" not in out
    schema_validator().validate(out)


def test_root_extension_is_not_normalized_on_no_change():
    evaluations = {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "vendor.example": {"preserve": {"exactly": True}},
    }
    state = deepcopy(EVIDENCE)
    state["xp"] = 0
    before = deepcopy(evaluations)

    status, out, _, created = recommendations.ensure_pending_promotion(
        evaluations, state, scope="career", competency_id=None,
    )

    assert status == "NO_CHANGE"
    assert created is False
    assert out == before
    assert evaluations == before
    assert "promotion_recommendations" not in out
    schema_validator().validate(out)


def test_unknown_recommendation_field_is_rejected_by_domain_and_schema():
    evaluations = valid_recommendations()
    evaluations["promotion_recommendations"][0]["extension"] = True

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_promotion_recommendation_state(evaluations)
    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(evaluations)


@pytest.mark.parametrize(("container", "field"), [
    ("policy", "extension"),
    ("evidence", "extension"),
])
def test_unknown_field_in_closed_aef_structure_is_rejected(container, field):
    evaluations = valid_recommendations()
    if container == "policy":
        evaluations["policy"][field] = True
    else:
        evaluations["promotion_recommendations"][0][container][field] = True

    with pytest.raises(jsonschema.ValidationError):
        schema_validator().validate(evaluations)
    if container == "evidence":
        with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
            recommendations.validate_promotion_recommendation_state(evaluations)


def test_producer_domain_schema_and_strict_json_pipeline():
    _, evaluations, _, _ = recommendations.ensure_pending_promotion(
        {**empty_evaluations(), "extension": {"opaque": True}},
        EVIDENCE,
        scope="competency",
        competency_id="record-classification",
        detected_at="2026-08-14T10:00:00.250+02:00",
    )

    assert recommendations.validate_promotion_recommendation_state(evaluations) is evaluations
    schema_validator().validate(evaluations)
    encoded = json.dumps(evaluations, allow_nan=False, sort_keys=True)
    assert json.loads(encoded) == evaluations
    assert "level" not in evaluations


def test_historical_state_without_recommendation_collection_remains_valid():
    evaluations = {
        "policy": {"mode": "manual"},
        "history": [],
        "historical_extension": {"kept": True},
    }
    before = deepcopy(evaluations)

    assert recommendations.validate_promotion_recommendation_state(evaluations) is evaluations
    schema_validator().validate(evaluations)
    assert evaluations == before


def test_official_and_historical_evaluation_documents_cross_validate():
    from aef.init_profiles import get_init_profile

    official = get_init_profile("aef-v1")["initial_files"][
        ".agent/state/evaluations.json"
    ]
    historical = {
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "historical.extension": {"preserve": [1, 2, 3]},
    }

    for document in (official, historical):
        before = deepcopy(document)
        assert recommendations.validate_evaluation_state(document) is document
        schema_validator().validate(document)
        assert json.loads(json.dumps(document, allow_nan=False)) == document
        assert document == before


@pytest.mark.parametrize(("competency_id", "literal_hash"), [
    ("a:b", "6783a31eabf68ccc0660f935c0826282bdd2241f3a80a9f2d10d59aea9ebb5d8"),
    ("日本語", "77710aedc74ecfa33685e33a6c7df5cc83004da1bdcef7fb280f5c2b2e97e0a5"),
    ("a/b", "c14cddc033f64b9dea80ea675cf280a015e672516090a5626781153dc68fea11"),
    (r"a\b", "c62016d0f8ee333350283fd879b50b692932e932794e5d686f7d37d67484e199"),
])
def test_competency_recommendation_uses_literal_utf8_hash(competency_id, literal_hash):
    status, out, recommendation_id, created = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="competency",
        competency_id=competency_id, detected_at="2026-08-14T10:00:00Z",
    )

    assert status == "CHANGE"
    assert created is True
    assert recommendation_id == (
        f"promotion:competency:sha256-{literal_hash}:L1:L2"
    )
    assert out["promotion_recommendations"][0]["competency_id"] == competency_id
    assert recommendations.validate_evaluation_state(out) is out
    schema_validator().validate(out)


def test_hashed_competency_recommendations_are_distinct_stable_and_replay_safe():
    _, first, first_id, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="competency", competency_id="a:b",
        detected_at="2026-08-14T10:00:00Z",
    )
    status, replay, replay_id, created = recommendations.ensure_pending_promotion(
        first, EVIDENCE, scope="competency", competency_id="a:b",
        detected_at="2026-08-15T10:00:00Z",
    )
    _, second, second_id, _ = recommendations.ensure_pending_promotion(
        replay, EVIDENCE, scope="competency", competency_id="a/b",
        detected_at="2026-08-15T10:00:00Z",
    )

    assert status == "NO_CHANGE"
    assert created is False
    assert replay_id == first_id
    assert replay == first
    assert second_id != first_id
    assert len({item["id"] for item in second["promotion_recommendations"]}) == 2
    assert all(item["from_level"] == "L1" for item in second["promotion_recommendations"])


def test_legacy_colon_competency_recommendation_round_trips_without_promotion(tmp_path):
    _, evaluations, recommendation_id, _ = recommendations.ensure_pending_promotion(
        empty_evaluations(), EVIDENCE, scope="competency", competency_id="a:b",
        detected_at="2026-08-14T10:00:00Z",
    )
    path = ".agent/state/evaluations.json"
    current = load_workspace(tmp_path)

    apply_workspace(tmp_path, current, {"files": {path: evaluations}})
    reloaded = load_workspace(tmp_path)["files"][path]

    assert reloaded == evaluations
    assert reloaded["promotion_recommendations"][0]["id"] == recommendation_id
    assert reloaded["promotion_recommendations"][0]["competency_id"] == "a:b"
    assert "level" not in reloaded
    assert recommendations.validate_evaluation_state(reloaded) is reloaded
    schema_validator().validate(reloaded)


@pytest.mark.parametrize("invalid_id", ["", "   ", " legacy ", "legacy\u0007id"])
def test_invalid_competency_identifier_cannot_create_recommendation(invalid_id):
    evaluations = empty_evaluations()
    state = deepcopy(EVIDENCE)
    before_evaluations = deepcopy(evaluations)
    before_state = deepcopy(state)

    with pytest.raises(
        recommendations.InvalidPromotionRecommendationStateError,
        match="explicit migration required",
    ):
        recommendations.ensure_pending_promotion(
            evaluations, state, scope="competency", competency_id=invalid_id,
            detected_at="2026-08-14T10:00:00Z",
        )

    assert evaluations == before_evaluations
    assert state == before_state


@pytest.mark.parametrize("invalid", [
    None,
    [],
    {"history": []},
    {"policy": {"mode": "manual"}},
    {"policy": {"mode": "manual", "unknown": True}, "history": []},
    {"policy": {"mode": "manual"}, "history": [{
        "id": "review-1", "performed_at": "2026-08-14T10:00:00Z",
        "result": "maintain", "unknown": True,
    }]},
    {"schema_version": "1.0", "policy": {"mode": "manual"}, "history": []},
])
def test_complete_evaluation_validator_rejects_invalid_known_state(invalid):
    before = deepcopy(invalid)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.validate_evaluation_state(invalid)

    assert invalid == before


def test_complete_evaluation_validator_accepts_closed_history_and_root_extension():
    evaluations = {
        "schema_version": "1.0.0",
        "policy": {"mode": "interval", "interval_days": 30},
        "history": [{
            "id": "review-1",
            "performed_at": "2026-08-14T10:00:00Z",
            "result": "maintain",
        }],
        "extension": {"opaque": True},
    }
    before = deepcopy(evaluations)

    assert recommendations.validate_evaluation_state(evaluations) is evaluations
    schema_validator().validate(evaluations)
    assert evaluations == before


def test_producer_rejects_complete_source_before_readiness_or_mutation():
    evaluations = empty_evaluations()
    evaluations["policy"]["unknown"] = True
    state = deepcopy(EVIDENCE)
    before_evaluations = deepcopy(evaluations)
    before_state = deepcopy(state)

    with pytest.raises(recommendations.InvalidPromotionRecommendationStateError):
        recommendations.ensure_pending_promotion(
            evaluations, state, scope="career", competency_id=None,
            detected_at="2026-08-14T10:00:00Z",
        )

    assert evaluations == before_evaluations
    assert state == before_state


@pytest.mark.parametrize("version", ["0.9.0", "1.0.1", "2.0.0", "1.0", 100, None])
def test_present_unknown_or_invalid_evaluation_version_is_rejected(version):
    evaluations = recommendations.empty_evaluations()
    evaluations["schema_version"] = version
    before = deepcopy(evaluations)

    with pytest.raises(
        recommendations.InvalidPromotionRecommendationStateError,
        match="invalid evaluation schema version",
    ):
        recommendations.validate_evaluation_state(evaluations)

    assert evaluations == before


def test_historical_unversioned_state_can_change_without_implicit_migration():
    historical = empty_evaluations()
    assert "schema_version" not in historical

    status, out, _, created = recommendations.ensure_pending_promotion(
        historical, EVIDENCE, scope="career", competency_id=None,
        detected_at="2026-08-14T10:00:00Z",
    )

    assert status == "CHANGE"
    assert created is True
    assert "schema_version" not in out
    assert "schema_version" not in historical
    assert recommendations.validate_evaluation_state(out) is out
    schema_validator().validate(out)


def test_versioned_empty_evaluation_state_round_trips_through_filesystem(tmp_path):
    evaluations = recommendations.empty_evaluations()
    path = ".agent/state/evaluations.json"
    current = load_workspace(tmp_path)

    apply_workspace(tmp_path, current, {"files": {path: evaluations}})
    reloaded = load_workspace(tmp_path)["files"][path]

    assert reloaded == evaluations
    assert reloaded["schema_version"] == "1.0.0"
    assert recommendations.validate_evaluation_state(reloaded) is reloaded
    schema_validator().validate(reloaded)
