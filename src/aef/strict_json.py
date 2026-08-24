import math


class InvalidStrictJSONError(ValueError):
    """Raised when a value cannot be represented as strict JSON."""

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class DuplicateJSONKeyError(ValueError):
    """Raised when a JSON object contains the same key more than once."""

    def __init__(self, key):
        self.key = key
        super().__init__(f"duplicate JSON key: {key!r}")


def reject_duplicate_keys(pairs):
    """object_pairs_hook that rejects duplicate keys at every object depth."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateJSONKeyError(key)
        out[key] = value
    return out


def validate_strict_json(value):
    """Validate JSON values recursively without coercing keys or values."""
    active = set()

    def visit(item):
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise InvalidStrictJSONError(
                    "non_finite_number", "non-finite JSON number"
                )
            return
        if isinstance(item, dict):
            identity = id(item)
            if identity in active:
                raise InvalidStrictJSONError("cyclic_reference", "cyclic JSON object")
            active.add(identity)
            try:
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise InvalidStrictJSONError(
                            "non_string_key", "JSON object keys must be strings"
                        )
                    visit(nested)
            finally:
                active.remove(identity)
            return
        if isinstance(item, list):
            identity = id(item)
            if identity in active:
                raise InvalidStrictJSONError("cyclic_reference", "cyclic JSON array")
            active.add(identity)
            try:
                for nested in item:
                    visit(nested)
            finally:
                active.remove(identity)
            return
        raise InvalidStrictJSONError("unsupported_value", "unsupported JSON value")

    visit(value)
    return value
