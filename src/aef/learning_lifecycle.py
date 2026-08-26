from copy import deepcopy
from .knowledge import upsert_by_id


def observe(records, *, observation_id, summary, pattern_key, strength="normal"):
    record = {
        "id": observation_id,
        "type": "observation",
        "status": "active",
        "summary": summary,
        "pattern_key": pattern_key,
        "strength": strength,
    }
    return upsert_by_id(records, record)


def derive_hypothesis(observations, hypotheses, *, pattern_key, minimum_observations=2):
    evidence = sorted(o["id"] for o in observations if o.get("pattern_key") == pattern_key and o.get("status") == "active")
    if len(evidence) < minimum_observations:
        return "INSUFFICIENT_EVIDENCE", deepcopy(hypotheses), None
    record = {
        "id": f"hypothesis:{pattern_key}",
        "type": "hypothesis",
        "status": "candidate",
        "pattern_key": pattern_key,
        "evidence_ids": evidence,
        "confirmations": 0,
        "explicit_human_validation": False,
    }
    existing = next((h for h in hypotheses if h.get("id") == record["id"]), None)
    if existing:
        record["confirmations"] = existing.get("confirmations", 0)
        record["explicit_human_validation"] = existing.get(
            "explicit_human_validation", False,
        )
        if existing.get("confirmation_source_records"):
            record["confirmation_source_records"] = deepcopy(
                existing["confirmation_source_records"],
            )
    status, out = upsert_by_id(hypotheses, record)
    return status, out, record["id"]


def confirm_hypothesis(hypotheses, hypothesis_id, *, explicit_human_validation=False):
    out = deepcopy(hypotheses)
    for h in out:
        if h.get("id") == hypothesis_id:
            if explicit_human_validation:
                if h.get("explicit_human_validation"):
                    return "NO_CHANGE", deepcopy(hypotheses)
                h["explicit_human_validation"] = True
                return "CHANGE", out
            h["confirmations"] = h.get("confirmations", 0) + 1
            return "CHANGE", out
    return "NOT_FOUND", out


def derive_rule(hypotheses, rules, *, hypothesis_id, minimum_confirmations=3):
    h = next((x for x in hypotheses if x.get("id") == hypothesis_id), None)
    if h is None:
        return "NOT_FOUND", deepcopy(rules), None
    if not h.get("explicit_human_validation") and h.get("confirmations", 0) < minimum_confirmations:
        return "INSUFFICIENT_EVIDENCE", deepcopy(rules), None
    record = {
        "id": f"rule:{hypothesis_id.removeprefix('hypothesis:')}",
        "type": "rule",
        "status": "active",
        "pattern_key": h["pattern_key"],
        "derived_from": hypothesis_id,
        "evidence_ids": deepcopy(h.get("evidence_ids", [])),
        "confirmations": h.get("confirmations", 0),
        "explicit_human_validation": h.get("explicit_human_validation", False),
    }
    status, out = upsert_by_id(rules, record)
    return status, out, record["id"]


def derive_principle(rules, principles, *, rule_id, human_approved=False):
    if not human_approved:
        return "REQUIRE_HUMAN_APPROVAL", deepcopy(principles), None
    r = next((x for x in rules if x.get("id") == rule_id), None)
    if r is None:
        return "NOT_FOUND", deepcopy(principles), None
    record = {
        "id": f"principle:{rule_id.removeprefix('rule:')}",
        "type": "principle",
        "status": "active",
        "derived_from": rule_id,
        "human_approved": True,
    }
    status, out = upsert_by_id(principles, record)
    return status, out, record["id"]
