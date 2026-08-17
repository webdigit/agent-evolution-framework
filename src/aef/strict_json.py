import math


class InvalidStrictJSONError(ValueError):
    """Raised when a value cannot be represented as strict JSON."""

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


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
