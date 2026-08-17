from copy import deepcopy
from .knowledge import upsert_by_id


def detect_learning_signals(events, existing_signals=None):
    """Detect candidate learning signals from normalized events.

    This is intentionally conservative: signals are prompts for learning work,
    not learned rules. Stable semantic IDs make repeated detection idempotent.
    """
    existing_signals = existing_signals or []
    out = deepcopy(existing_signals)

    def add(signal):
        nonlocal out
        _, out = upsert_by_id(out, signal)

    # Novel situations explicitly marked as outside known patterns.
    for e in events:
        if e.get("novel") is True:
            key = e.get("pattern_key") or e.get("competency") or "unknown"
            add({
                "id": f"signal:novelty:{key}",
                "type": "novelty",
                "status": "candidate",
                "pattern_key": key,
                "evidence_ids": sorted({x.get("id") for x in events if x.get("novel") is True and (x.get("pattern_key") or x.get("competency") or "unknown") == key and x.get("id")}),
                "recommended_action": "OBSERVE",
            })

    # Repeated help requests on the same pattern indicate a knowledge/competency gap.
    help_groups = {}
    for e in events:
        if e.get("kind") == "help_request":
            key = e.get("pattern_key") or e.get("competency") or "unknown"
            help_groups.setdefault(key, []).append(e)
    for key, group in help_groups.items():
        if len(group) >= 3:
            add({
                "id": f"signal:repeated-help:{key}",
                "type": "repeated_help",
                "status": "candidate",
                "pattern_key": key,
                "evidence_ids": sorted(e["id"] for e in group if e.get("id")),
                "recommended_action": "FORM_HYPOTHESIS",
            })

    # Human corrections converging on the same pattern are strong learning evidence.
    correction_groups = {}
    for e in events:
        if e.get("kind") == "human_correction":
            key = e.get("pattern_key") or e.get("competency") or "unknown"
            correction_groups.setdefault(key, []).append(e)
    for key, group in correction_groups.items():
        if len(group) >= 2:
            add({
                "id": f"signal:convergent-corrections:{key}",
                "type": "convergent_corrections",
                "status": "candidate",
                "pattern_key": key,
                "evidence_ids": sorted(e["id"] for e in group if e.get("id")),
                "recommended_action": "FORM_HYPOTHESIS",
            })

    # A prediction/rule mismatch is a surprise signal; one event is enough to investigate,
    # but never enough to rewrite the rule automatically.
    for e in events:
        if e.get("kind") == "rule_mismatch" and e.get("rule_id"):
            rid = e["rule_id"]
            add({
                "id": f"signal:rule-surprise:{rid}",
                "type": "rule_surprise",
                "status": "candidate",
                "rule_id": rid,
                "evidence_ids": sorted({x.get("id") for x in events if x.get("kind") == "rule_mismatch" and x.get("rule_id") == rid and x.get("id")}),
                "recommended_action": "REVIEW_RULE",
            })

    # Repeated unexplained success can deserve study, while avoiding overfitting to one success.
    success_groups = {}
    for e in events:
        if e.get("kind") == "success" and e.get("explained") is False:
            key = e.get("pattern_key") or e.get("competency") or "unknown"
            success_groups.setdefault(key, []).append(e)
    for key, group in success_groups.items():
        if len(group) >= 3:
            add({
                "id": f"signal:unexplained-success:{key}",
                "type": "unexplained_success",
                "status": "candidate",
                "pattern_key": key,
                "evidence_ids": sorted(e["id"] for e in group if e.get("id")),
                "recommended_action": "FORM_HYPOTHESIS",
            })

    return ("NO_CHANGE" if out == existing_signals else "CHANGE"), out
