import hashlib
import unicodedata


_FORBIDDEN_ID_CATEGORIES = {"Cc", "Cf", "Zs", "Zl", "Zp"}
_MAX_COMPETENCY_ID_LENGTH = 128


class InvalidCompetencyIdentifierError(ValueError):
    """Raised when a competency identifier is not canonical."""


def _latin_cyrillic_mix(value: str) -> bool:
    scripts = set()
    for character in value:
        name = unicodedata.name(character, "")
        if "LATIN" in name:
            scripts.add("latin")
        elif "CYRILLIC" in name:
            scripts.add("cyrillic")
        if scripts == {"latin", "cyrillic"}:
            return True
    return False


def validate_competency_id(value):
    """Return a canonical competency identifier without modifying it.

    Rejects control/format characters, Unicode space separators (including
    non-breaking space), compatibility lookalikes (NFKC ≠ NFC), and mixed
    Latin/Cyrillic identifiers. The value is never rewritten.
    """
    if (
        not isinstance(value, str)
        or not value
        or not value.strip()
        or value[0].isspace()
        or value[-1].isspace()
        or len(value) > _MAX_COMPETENCY_ID_LENGTH
        or any(unicodedata.category(character) in _FORBIDDEN_ID_CATEGORIES for character in value)
        or unicodedata.normalize("NFKC", value) != unicodedata.normalize("NFC", value)
        or _latin_cyrillic_mix(value)
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
