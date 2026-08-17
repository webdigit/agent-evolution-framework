from copy import deepcopy


def upsert_by_id(records, record):
    out = deepcopy(records)
    for i, existing in enumerate(out):
        if existing.get("id") == record.get("id"):
            if existing == record:
                return "NO_CHANGE", out
            out[i] = deepcopy(record)
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
