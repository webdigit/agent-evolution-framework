import json
from pathlib import Path
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aef.reconcile import reconcile, normalize

CASES = {
    "manifest": "manifest.schema.json",
    "career": "career.schema.json",
    "competencies": "competencies.schema.json",
    "supervision": "supervision.schema.json",
    "exploration": "exploration.schema.json",
    "capabilities": "capability.schema.json",
    "knowledge": "knowledge.schema.json",
    "evaluations": "evaluation.schema.json",
    "migrations": "migrations.schema.json",
    "policies": "policies.schema.json",
}


def load(path):
    return json.loads(path.read_text())


def test_minimal_fixture_validates():
    for name, schema_file in CASES.items():
        schema = load(ROOT / "src" / "aef" / "schemas" / schema_file)
        data = load(ROOT / "fixtures" / "minimal" / f"{name}.json")
        jsonschema.Draft202012Validator(schema).validate(data)


def test_reconcile_is_idempotent():
    state = load(ROOT / "fixtures" / "minimal" / "career.json")
    status1, s1 = reconcile({}, state)
    status2, s2 = reconcile(s1, state)
    assert status1 == "CHANGE"
    assert status2 == "NO_CHANGE"
    assert s1 == s2


def test_audit_style_normalization_is_read_only():
    state = load(ROOT / "fixtures" / "minimal" / "career.json")
    before = json.dumps(state, sort_keys=True)
    _ = normalize(state)
    after = json.dumps(state, sort_keys=True)
    assert before == after


def test_connector_agnosticism():
    schema = load(ROOT / "src/aef/schemas/capability.schema.json")
    arbitrary = {
        "connectors": [{
            "id": "totally-unknown-vendor-42",
            "status": "available",
            "capabilities": [{"id":"totally-unknown-vendor-42.do-x","operation":"do-x","risk":"R1","reversible":True}]
        }]
    }
    jsonschema.Draft202012Validator(schema).validate(arbitrary)


def test_zero_connectors_is_valid():
    schema = load(ROOT / "src/aef/schemas/capability.schema.json")
    jsonschema.Draft202012Validator(schema).validate({"connectors": []})


def test_unproven_trust_is_distinct_from_zero():
    schema = load(ROOT / "src/aef/schemas/career.schema.json")
    jsonschema.Draft202012Validator(schema).validate({"level":"L1","xp":0,"trust":None,"status":"active","probation":False})
    jsonschema.Draft202012Validator(schema).validate({"level":"L1","xp":0,"trust":0.0,"status":"active","probation":False})


def test_canonical_id_keyed_competencies_validate():
    schema = load(ROOT / "src/aef/schemas/competencies.schema.json")
    state = {
        "record-classification": {
            "id": "record-classification",
            "title": "Record classification",
            "level": "L2",
            "xp": 80,
            "cases": 10,
            "trust": 0.95,
            "complex_cases": 0,
            "recent_significant_errors": 0,
            "probation": False,
            "source": "pilot",
        }
    }
    jsonschema.Draft202012Validator(schema).validate(state)


def test_legacy_competency_fixture_remains_valid():
    schema = load(ROOT / "src/aef/schemas/competencies.schema.json")
    legacy = load(ROOT / "fixtures" / "minimal" / "competencies.json")
    jsonschema.Draft202012Validator(schema).validate(legacy)


def test_canonical_principles_and_optional_mistakes_validate():
    schema = load(ROOT / "src/aef/schemas/knowledge.schema.json")
    state = {
        "observations": [],
        "hypotheses": [],
        "rules": [],
        "principles": [{
            "id": "principle:verify-source",
            "type": "principle",
            "status": "active",
            "derived_from": "rule:verify-source",
            "human_approved": True,
        }],
    }
    jsonschema.Draft202012Validator(schema).validate(state)


def test_specialized_rule_requires_real_context_and_lifecycle_shape():
    schema = load(ROOT / "src/aef/schemas/knowledge.schema.json")
    state = {
        "observations": [],
        "hypotheses": [],
        "rules": [{
            "id": "rule:response-tone",
            "type": "rule",
            "status": "specialized",
            "context": {"channel": "email"},
            "lifecycle": {
                "specialized": {
                    "reason": "Chat evidence diverged",
                    "evidence_ids": ["obs:3"],
                }
            },
        }],
        "principles": [],
    }
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(state)

    invalid = load(ROOT / "fixtures" / "minimal" / "knowledge.json")
    invalid["rules"] = [{
        "id": "rule:response-tone",
        "type": "rule",
        "status": "specialized",
    }]
    assert list(validator.iter_errors(invalid))
