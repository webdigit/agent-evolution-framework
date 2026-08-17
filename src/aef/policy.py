LEVELS = {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}
RISKS = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}


def supervision_required(global_level, competency_level, tasks_since_review=0, every_tasks=None, incident=False):
    if incident:
        return True
    effective = min(LEVELS[global_level], LEVELS[competency_level])
    if effective == 1:
        return True
    if every_tasks is not None and tasks_since_review >= every_tasks:
        return True
    return False


def should_ask_for_help(*, confidence, risk, competency_level, missing_material_info=False,
                        conflicting_sources=False, irreversible=False, tool_anomaly=False):
    if missing_material_info or conflicting_sources or tool_anomaly:
        return True
    if irreversible and RISKS[risk] >= 3:
        return True
    thresholds = {"L1": 0.90, "L2": 0.82, "L3": 0.72, "L4": 0.62, "L5": 0.52}
    return confidence < thresholds[competency_level]


def exploration_decision(*, mode, risk, reversible, hard_block=False, requires_approval=False):
    if hard_block:
        return "DENY_EXPLORATION"
    if mode in {"OBSERVE", "SHADOW", "SIMULATE"}:
        return f"EXPLORE_{mode}"
    if mode != "LIVE":
        return "DENY_EXPLORATION"
    if RISKS[risk] >= 4:
        return "REQUIRE_APPROVAL"
    if not reversible and RISKS[risk] >= 2:
        return "REQUIRE_APPROVAL"
    if requires_approval:
        return "REQUIRE_APPROVAL"
    return "EXPLORE_LIVE"
