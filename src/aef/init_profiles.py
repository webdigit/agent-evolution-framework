from copy import deepcopy

from .promotion_recommendations import empty_evaluations


_CANONICAL_CORE_FILES = {
    ".agent/core/constitution.md": """# AEF V1 Constitution

1. Explicit hard policies and constraints MUST take precedence over learned behavior, preferences, levels, Trust, and optimization goals.
2. An AEF operation returning BLOCKED MUST return its source state unchanged and its persistence adapter MUST apply no filesystem change.
3. An AEF operation returning FAILED MUST return its last explicitly defined safe state and MUST NOT expose a partial candidate as committed state.
4. Human approval MUST be an explicit input to every transition that requires it; absence of approval MUST NOT be interpreted as approval.
5. Identical state, inputs, policies, approvals, and injected identifiers or time SHOULD produce an identical transition.
6. Replay of an applied transition SHOULD return NO_CHANGE and MUST NOT duplicate durable records.
7. Unknown and project-owned data or files MUST be preserved unless an explicitly authorized operation owns their removal.
8. An agent MUST NOT infer or extend authority from capability, learning, success, global level, adjacent competency, or environmental access alone.
9. AEF activation MUST be local to the explicitly selected project workspace by default.
10. A project MUST NOT implicitly inherit AEF state, authority, policy, or doctrine from a parent directory, a user directory, ~/.claude/, or another project.
11. Filesystem mutations MUST remain confined to authorized paths below the selected workspace.
12. Any integration that executes real actions MUST enforce executable Python policies and approved hooks before execution.
13. Any integration receiving ALLOW_WITH_LOG MUST persist the required durable authorization record before executing the real action; otherwise it MUST block or escalate.
14. Python code and approved hooks are authoritative for executable policies and transitions; JSON is authoritative for persisted current state.
15. Core Markdown files are normative doctrine but MUST NOT be treated as executable policy by themselves.
16. This Constitution takes precedence over every other AEF core document when their interpretations conflict.
""",
    ".agent/core/autonomy.md": """# AEF V1 Autonomy

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
""",
    ".agent/core/learning.md": """# AEF V1 Learning

All rules in the AEF V1 Constitution apply.

1. Durable learning MUST follow the ordered lifecycle: signal → observation → hypothesis → rule → principle.
2. A signal MUST remain a candidate for investigation and MUST NOT directly create a rule or principle.
3. A single observation MUST NOT justify a generalized hypothesis.
4. A hypothesis MUST retain stable evidence identifiers and MUST remain a candidate until its evidence gate is satisfied.
5. A rule MUST originate from an eligible hypothesis and MUST retain its derivation and evidence references.
6. Explicit human validation MAY satisfy a supported rule gate only where Python explicitly permits it.
7. A principle MUST NOT be created without explicit human approval.
8. Reprocessing identical evidence SHOULD return NO_CHANGE and MUST NOT duplicate semantically identical records.
9. A specialized rule MUST retain its identity, explicit context, reason, and evidence references.
10. A replacement rule MUST supersede rather than erase the previous rule.
11. A retired or superseded rule MUST remain in history and MUST NOT remain operational.
12. Contradictory evidence SHOULD trigger review and MUST NOT silently rewrite an active rule.
13. Learning MUST NOT increase level, Trust, execution authority, or permission without separate evaluation and authorization gates.
14. Learning from another project or user scope MUST NOT be imported implicitly into the active project.
""",
    ".agent/core/levels.md": """# AEF V1 Levels

All rules in the AEF V1 Constitution apply.

1. AEF levels MUST use the ordered scale L1, L2, L3, L4, and L5.
2. L1 MUST mean supervised and not yet proven for the applicable scope.
3. L2 through L5 MUST indicate progressively demonstrated maturity under Python-defined evidence gates; they MUST NOT imply unrestricted authority.
4. Global career level and local competency level MUST remain distinct.
5. Effective level for supervision and authorization MUST be the lower of the global and local levels.
6. Every newly discovered competency MUST start at L1 with zero XP, zero cases, zero complex cases, and Trust=None.
7. Transfer MAY seed orientation or an unvalidated hypothesis but MUST NOT copy level, XP, cases, complex exposure, or Trust.
8. A task MUST record outcomes and MAY create pending promotion recommendations, but MUST NOT apply a promotion directly.
9. Promotion readiness MUST satisfy the Python-defined requirements for XP, evaluated cases, Trust, complex exposure, and recent significant errors.
10. A pending recommendation MUST NOT be treated as approval and MUST NOT change a level.
11. Only an EVALUATE operation with explicit approval MAY apply a promotion, and it MUST recalculate readiness before doing so.
12. Promotion MUST advance at most one level per approved evaluation transition.
13. Missing, false, stale, or incorrectly scoped approval MUST leave the level unchanged.
14. A higher level MUST NOT override hard policy, risk controls, probation, approval, logging, or local competency limits.
""",
    ".agent/core/scoring.md": """# AEF V1 Scoring

All rules in the AEF V1 Constitution apply.

1. XP MUST measure accumulated successful experience weighted by task difficulty; it MUST NOT directly authorize execution.
2. Trust MUST represent bounded evidence derived from evaluated outcomes and MUST remain distinct from XP and level.
3. Trust=None MUST mean that Trust has not yet been established, not that Trust is zero or acceptable.
4. The cases field MUST count all evaluated cases, whether successful or unsuccessful.
5. Successful outcomes MAY increase XP; unsuccessful outcomes MUST still increase the evaluated case count.
6. Complex-case exposure MUST count only cases classified as complex and completed successfully.
7. Significant errors MUST remain distinct from ordinary unsuccessful or neutral outcomes.
8. Recent significant errors MUST block promotion readiness while they remain active.
9. Probation MUST constrain autonomy separately from level.
10. Promotion readiness MUST consider XP, evaluated cases, Trust, complex exposure, and recent significant errors together.
11. Promotion readiness MUST create at most a pending recommendation during a task; it MUST NOT constitute promotion or approval.
12. Authorization MUST NOT use progression metrics as a substitute for risk policy or required durable logging.
13. Markdown MUST NOT define numeric thresholds, reward values, or transition formulas.
14. Python MUST remain authoritative for scoring, thresholds, readiness, recovery, and probation calculations.
15. JSON MUST remain authoritative for current XP, Trust, cases, complex exposure, errors, probation, recommendations, and levels.
""",
}

# Backward-compatible public snapshot. The engine never consumes this mutable
# dictionary directly, so callers cannot alter future initializations.
DEFAULT_CORE_FILES = deepcopy(_CANONICAL_CORE_FILES)


class UnknownInitProfileError(ValueError):
    """Raised when an initialization profile ID is not registered."""

    def __init__(self, profile_id):
        self.profile_id = profile_id
        super().__init__(f"unknown init profile: {profile_id}")


_INIT_PROFILES = {
    "aef-v1": {
        "id": "aef-v1",
        "framework": "aef",
        "framework_version": "1.0.0",
        "schema_version": "1.0.0",
        "required_decisions": [{
            "id": "decision.role.primary.v1",
            "required": True,
            "value_type": "string",
            "allow_empty": False,
        }],
        "core_files": deepcopy(_CANONICAL_CORE_FILES),
        "initial_files": {
            ".agent/state/migrations.json": {"applied": []},
            ".agent/state/career.json": {
                "level": "L1",
                "xp": 0,
                "cases": 0,
                "trust": None,
                "complex_cases": 0,
                "recent_significant_errors": 0,
                "status": "active",
                "probation": False,
            },
            ".agent/state/competencies.json": {},
            ".agent/state/evaluations.json": empty_evaluations(),
            ".agent/integrations/registry.json": {"connectors": []},
            ".agent/knowledge/knowledge.json": {
                "signals": [],
                "observations": [],
                "hypotheses": [],
                "rules": [],
                "principles": [],
                "mistakes": [],
            },
        },
    },
}


def get_default_core_files():
    """Return a fresh copy of the canonical core files for legacy initialization."""
    return deepcopy(_CANONICAL_CORE_FILES)


def get_init_profile(profile_id):
    """Return an independent copy of a registered initialization profile."""
    try:
        profile = _INIT_PROFILES[profile_id]
    except (KeyError, TypeError) as exc:
        raise UnknownInitProfileError(profile_id) from exc
    return deepcopy(profile)
