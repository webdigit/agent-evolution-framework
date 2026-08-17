from copy import deepcopy
from .knowledge import upsert_by_id

VALID_STATUSES = {"active", "specialized", "superseded", "retired"}


def _find(records, rule_id):
    return next((r for r in records if r.get("id") == rule_id), None)


def _context_matches(rule, context):
    scope = rule.get("context") or {}
    if not scope:
        return True
    return all(context.get(k) == v for k, v in scope.items())


def applicable_rules(rules, *, context=None):
    """Return rules that are currently applicable without mutating history.

    Superseded and retired rules remain queryable but are never operational.
    Specialized rules are operational only inside their explicit context.
    """
    context = context or {}
    out = []
    for rule in rules:
        status = rule.get("status", "active")
        if status in {"superseded", "retired"}:
            continue
        if status == "specialized" and not rule.get("context"):
            continue
        if _context_matches(rule, context):
            out.append(deepcopy(rule))
    return sorted(out, key=lambda r: r["id"])


def specialize_rule(rules, *, rule_id, context, reason, evidence_ids=None):
    """Narrow an existing rule's scope while preserving its identity and history."""
    if not isinstance(context, dict) or not context:
        raise ValueError("specialized rule context must be a non-empty dictionary")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("specialized rule reason must be a non-empty string")
    if evidence_ids is None:
        evidence_ids = []
    if not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids):
        raise ValueError("specialized rule evidence_ids must be a list of strings")

    current = _find(rules, rule_id)
    if current is None:
        return "NOT_FOUND", deepcopy(rules)
    desired = deepcopy(current)
    desired["status"] = "specialized"
    desired["context"] = deepcopy(context)
    desired.setdefault("lifecycle", {})
    desired["lifecycle"]["specialized"] = {
        "reason": reason,
        "evidence_ids": sorted(set(evidence_ids)),
    }
    status, out = upsert_by_id(rules, desired)
    return status, out


def supersede_rule(rules, *, rule_id, replacement, reason, evidence_ids=None):
    """Replace a rule without deleting it.

    The old rule becomes superseded and points to the stable ID of the replacement.
    The replacement points back to the old rule. Replays are idempotent.
    """
    current = _find(rules, rule_id)
    if current is None:
        return "NOT_FOUND", deepcopy(rules), None
    if replacement.get("id") == rule_id:
        return "INVALID_REPLACEMENT", deepcopy(rules), None

    replacement = deepcopy(replacement)
    replacement.setdefault("type", "rule")
    replacement.setdefault("status", "active")
    replacement["supersedes"] = rule_id
    replacement.setdefault("lifecycle", {})
    replacement["lifecycle"].setdefault("created_by", "supersession")

    old = deepcopy(current)
    old["status"] = "superseded"
    old["superseded_by"] = replacement["id"]
    old.setdefault("lifecycle", {})
    old["lifecycle"]["superseded"] = {
        "reason": reason,
        "evidence_ids": sorted(set(evidence_ids or [])),
    }

    _, out = upsert_by_id(rules, old)
    _, out = upsert_by_id(out, replacement)

    # Compare semantic target with source to support replay-safe NO_CHANGE.
    if out == rules:
        return "NO_CHANGE", deepcopy(rules), replacement["id"]
    return "CHANGE", out, replacement["id"]


def retire_rule(rules, *, rule_id, reason, evidence_ids=None):
    current = _find(rules, rule_id)
    if current is None:
        return "NOT_FOUND", deepcopy(rules)
    desired = deepcopy(current)
    desired["status"] = "retired"
    desired.setdefault("lifecycle", {})
    desired["lifecycle"]["retired"] = {
        "reason": reason,
        "evidence_ids": sorted(set(evidence_ids or [])),
    }
    status, out = upsert_by_id(rules, desired)
    return status, out


def review_rule(rules, *, rule_id, contradictions=0, contexts=None, replacement=None,
                reason="review", evidence_ids=None, retire_threshold=3):
    """Conservative deterministic consolidation decision for the lab.

    - no contradiction => KEEP
    - contradictions limited to one or more explicit contexts => SPECIALIZE
    - replacement supplied => SUPERSEDE
    - repeated contradictions with no safe replacement => RETIRE
    - otherwise => KEEP_PENDING_EVIDENCE
    """
    rule = _find(rules, rule_id)
    if rule is None:
        return "NOT_FOUND", deepcopy(rules), None
    if contradictions <= 0:
        return "KEEP", deepcopy(rules), rule_id
    if replacement is not None:
        status, out, replacement_id = supersede_rule(
            rules, rule_id=rule_id, replacement=replacement, reason=reason, evidence_ids=evidence_ids
        )
        return "SUPERSEDE" if status == "CHANGE" else status, out, replacement_id
    contexts = contexts or []
    if contexts:
        # V1 intentionally supports one canonical specialization scope per review.
        status, out = specialize_rule(
            rules, rule_id=rule_id, context=contexts[0], reason=reason, evidence_ids=evidence_ids
        )
        return "SPECIALIZE" if status == "CHANGE" else status, out, rule_id
    if contradictions >= retire_threshold:
        status, out = retire_rule(rules, rule_id=rule_id, reason=reason, evidence_ids=evidence_ids)
        return "RETIRE" if status == "CHANGE" else status, out, rule_id
    return "KEEP_PENDING_EVIDENCE", deepcopy(rules), rule_id
