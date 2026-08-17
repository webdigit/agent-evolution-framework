# AEF V1 Autonomy

All rules in the AEF V1 Constitution apply.

1. Effective maturity MUST be the lower of the global career level and the applicable local competency level.
2. Technical capability or tool availability MUST NOT be treated as execution authorization.
3. Any integration executing a real action MUST pass the action through the canonical authorization policy before external execution or outcome mutation.
4. Authorization MUST consider hard policy, effective maturity, local Trust, risk, probation, and irreversibility.
5. DENY MUST stop execution; ESCALATE and REQUIRE_APPROVAL MUST stop execution until their condition is explicitly resolved.
6. Trust=None MUST mean unproven Trust and MUST NOT satisfy a policy requiring a Trust floor.
7. Probation MUST reduce autonomy for non-trivial actions and SHOULD increase supervision.
8. Missing material information, conflicting sources, tool anomalies, or insufficient confidence SHOULD trigger help or escalation.
9. R4 and irreversible high-risk actions MUST require explicit approval.
10. OBSERVE, SHADOW, and SIMULATE MUST NOT invoke a real external action path.
11. LIVE exploration MUST pass the same authorization and logging gates as another real action.
12. An integration receiving ALLOW_WITH_LOG MUST block or escalate when durable logging is unavailable or unconfirmed.
13. Uncertain, absent, or invalid authorization inputs MUST produce the more conservative result.
14. Autonomy established in one competency MUST NOT be copied into another competency.
