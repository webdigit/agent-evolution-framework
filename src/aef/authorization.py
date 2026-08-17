LEVELS = {"L1":1,"L2":2,"L3":3,"L4":4,"L5":5}
RISKS = {"R0":0,"R1":1,"R2":2,"R3":3,"R4":4}

DEFAULT_RISK_FLOORS = {
    "R0": {"level":"L1", "trust":None},
    "R1": {"level":"L2", "trust":0.80},
    "R2": {"level":"L3", "trust":0.88},
    "R3": {"level":"L4", "trust":0.94},
    "R4": {"level":"L5", "trust":0.98},
}


def execution_permission(*, global_level, competency_level, trust, risk,
                         probation=False, hard_effect=None, irreversible=False,
                         risk_floors=None):
    if hard_effect == "deny":
        return "DENY"
    if hard_effect == "escalate":
        return "ESCALATE"
    if hard_effect == "require_approval":
        return "REQUIRE_APPROVAL"

    if probation and RISKS[risk] >= 1:
        return "REQUIRE_APPROVAL"

    floors = risk_floors or DEFAULT_RISK_FLOORS
    req = floors[risk]
    effective_level = min(LEVELS[global_level], LEVELS[competency_level])
    if effective_level < LEVELS[req["level"]]:
        return "REQUIRE_APPROVAL"
    if req["trust"] is not None and (trust is None or trust < req["trust"]):
        return "REQUIRE_APPROVAL"
    if risk == "R4" or (irreversible and RISKS[risk] >= 3):
        return "REQUIRE_APPROVAL"
    return "ALLOW_WITH_LOG" if RISKS[risk] >= 2 else "ALLOW"
