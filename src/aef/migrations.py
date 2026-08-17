from copy import deepcopy


def apply_migration(state, ledger, migration_id, from_version, to_version, transform):
    if any(x["id"] == migration_id and x["status"] == "applied" for x in ledger["applied"]):
        return "NO_CHANGE", deepcopy(state), deepcopy(ledger)
    new_state = transform(deepcopy(state))
    new_ledger = deepcopy(ledger)
    new_ledger["applied"].append({
        "id": migration_id,
        "from_version": from_version,
        "to_version": to_version,
        "status": "applied"
    })
    return "CHANGE", new_state, new_ledger
