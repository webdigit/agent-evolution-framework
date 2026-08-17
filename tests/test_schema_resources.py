from pathlib import Path

import pytest

from aef.schema_validation import load_packaged_schema


SCHEMA_NAMES = {
    "capability.schema.json",
    "career.schema.json",
    "competencies.schema.json",
    "evaluation.schema.json",
    "exploration.schema.json",
    "knowledge.schema.json",
    "manifest.schema.json",
    "migrations.schema.json",
    "policies.schema.json",
    "supervision.schema.json",
}


@pytest.mark.parametrize("name", sorted(SCHEMA_NAMES))
def test_schema_resources_are_available_outside_checkout(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    schema = load_packaged_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert isinstance(schema, dict)


def test_canonical_schemas_have_no_checkout_root_duplicate():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "schemas").exists()
