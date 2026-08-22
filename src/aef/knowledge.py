from copy import deepcopy

MAX_EVIDENCE_IDS = 128


def union_evidence_ids(
    left: list[str] | None,
    right: list[str] | None,
    *,
    max_items: int = MAX_EVIDENCE_IDS,
) -> list[str]:
    """Deterministic bounded union of evidence identifiers."""
    merged = sorted({
        item
        for item in (left or []) + (right or [])
        if isinstance(item, str) and item
    })
    return merged[:max_items]


def upsert_by_id(records, record):
    out = deepcopy(records)
    for i, existing in enumerate(out):
        if existing.get("id") == record.get("id"):
            if existing == record:
                return "NO_CHANGE", out
            merged = deepcopy(record)
            if "evidence_ids" in existing or "evidence_ids" in record:
                merged["evidence_ids"] = union_evidence_ids(
                    existing.get("evidence_ids"),
                    record.get("evidence_ids"),
                )
            out[i] = merged
            return "CHANGE", out
    out.append(deepcopy(record))
    return "CHANGE", out


def promote_observation(observation, target_type, existing_records):
    derived_id = f"{target_type}:{observation['id']}"
    record = {
        "id": derived_id,
        "status": "candidate" if target_type == "hypothesis" else "active",
        "summary": observation["summary"],
        "evidence_ids": [observation["id"]],
    }
    return upsert_by_id(existing_records, record)
