import jsonschema
import json
from importlib.resources import files

from .promotion_recommendations import _valid_rfc3339
from .knowledge_state import InvalidKnowledgeStateError, validate_knowledge_state
from .strict_json import InvalidStrictJSONError, validate_strict_json


def draft202012_validator(schema):
    """Build the official AEF schema validator with semantic format checks."""
    format_checker = jsonschema.FormatChecker()
    format_checker.checks("date-time")(_valid_rfc3339)
    return jsonschema.Draft202012Validator(
        schema, format_checker=format_checker
    )


def load_packaged_schema(name: str):
    """Load one canonical schema from the installed AEF distribution."""
    resource = files("aef.schemas").joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_persisted_knowledge(document):
    """Run the official strict, business, and schema knowledge validators."""
    try:
        validate_strict_json(document)
        validate_knowledge_state(document)
        schema = load_packaged_schema("knowledge.schema.json")
        draft202012_validator(schema).validate(document)
    except (InvalidStrictJSONError, jsonschema.ValidationError) as exc:
        raise InvalidKnowledgeStateError("persisted knowledge validation failed") from exc
    return document
