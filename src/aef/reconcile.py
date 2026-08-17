from copy import deepcopy


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        if all(isinstance(x, dict) and "id" in x for x in value):
            return [normalize(x) for x in sorted(value, key=lambda x: x["id"])]
        return [normalize(x) for x in value]
    return value


def reconcile(current, desired):
    """Return (status, state) without adding timestamps or changing stable IDs."""
    if normalize(current) == normalize(desired):
        return "NO_CHANGE", deepcopy(current)
    return "CHANGE", deepcopy(desired)
