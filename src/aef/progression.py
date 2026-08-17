from copy import deepcopy

LEVEL_ORDER = ["L1", "L2", "L3", "L4", "L5"]

# Deliberately conservative defaults for the reference lab. These are policy,
# not universal truths, and can be overridden by a deployment later.
DEFAULT_THRESHOLDS = {
    "L2": {"xp": 50, "cases": 10, "trust": 0.80, "complex_cases": 0},
    "L3": {"xp": 200, "cases": 30, "trust": 0.90, "complex_cases": 0},
    "L4": {"xp": 500, "cases": 75, "trust": 0.95, "complex_cases": 10},
    "L5": {"xp": 1000, "cases": 150, "trust": 0.97, "complex_cases": 20},
}

XP_BY_DIFFICULTY = {"D1": 2, "D2": 4, "D3": 8, "D4": 16, "D5": 28}


def _bounded_trust(current, reward):
    # Unproven trust becomes observable after first evaluated case.
    base = 0.75 if current is None else current
    delta = {2: 0.04, 1: 0.02, 0: 0.0, -1: -0.03, -2: -0.08, -3: -0.15}[reward]
    return round(min(1.0, max(0.0, base + delta)), 4)


def record_outcome(state, *, difficulty, reward, successful=True, complex_case=None):
    out = deepcopy(state)
    out["cases"] = out.get("cases", 0) + 1
    if successful:
        out["xp"] = out.get("xp", 0) + XP_BY_DIFFICULTY[difficulty]
    out["trust"] = _bounded_trust(out.get("trust"), reward)
    if complex_case is None:
        complex_case = difficulty in {"D4", "D5"}
    if complex_case and successful:
        out["complex_cases"] = out.get("complex_cases", 0) + 1
    if reward <= -2:
        out["recent_significant_errors"] = out.get("recent_significant_errors", 0) + 1
    return out


def promotion_readiness(state, thresholds=None):
    thresholds = thresholds or DEFAULT_THRESHOLDS
    level = state["level"]
    idx = LEVEL_ORDER.index(level)
    if idx == len(LEVEL_ORDER) - 1:
        return {"eligible": False, "target": None, "reasons": ["already-max-level"]}
    target = LEVEL_ORDER[idx + 1]
    req = thresholds[target]
    reasons = []
    for key in ("xp", "cases", "complex_cases"):
        if state.get(key, 0) < req[key]:
            reasons.append(f"{key}:{state.get(key,0)}/{req[key]}")
    trust = state.get("trust")
    if trust is None or trust < req["trust"]:
        reasons.append(f"trust:{trust}/{req['trust']}")
    if state.get("recent_significant_errors", 0) > 0:
        reasons.append("recent-significant-errors")
    return {"eligible": not reasons, "target": target, "reasons": reasons}


def promote_if_eligible(state, *, human_approval, thresholds=None):
    readiness = promotion_readiness(state, thresholds)
    if not readiness["eligible"] or not human_approval:
        return "NO_CHANGE", deepcopy(state), readiness
    out = deepcopy(state)
    out["level"] = readiness["target"]
    return "CHANGE", out, readiness


def apply_probation_policy(state, *, trust_floor=0.60, error_threshold=2):
    out = deepcopy(state)
    probation = (out.get("trust") is not None and out["trust"] < trust_floor) or out.get("recent_significant_errors", 0) >= error_threshold
    changed = out.get("probation", False) != probation
    out["probation"] = probation
    return ("CHANGE" if changed else "NO_CHANGE"), out
