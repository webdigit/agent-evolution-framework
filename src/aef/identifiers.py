import hashlib
import unicodedata


class InvalidCompetencyIdentifierError(ValueError):
    """Raised when a competency identifier is not canonical."""


def validate_competency_id(value):
    """Return a canonical competency identifier without modifying it."""
    if (
        not isinstance(value, str)
        or not value
        or not value.strip()
        or value[0].isspace()
        or value[-1].isspace()
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise InvalidCompetencyIdentifierError(
            "invalid competency identifier; explicit migration required"
        )
    return value


def competency_recommendation_subject(value):
    """Return the unambiguous stable subject used in recommendation IDs."""
    validate_competency_id(value)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256-{digest}"
