from __future__ import annotations

from copy import deepcopy
from typing import Any

from .learning_lifecycle import derive_principle, derive_rule


class DerivationConflictError(ValueError):
    """Raised when a derived record would conflict with persisted state."""

    code = "derivation_conflict"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        self.details = details or {}
        super().__init__(message)


def hypothesis_eligible_for_rule(hypothesis: dict[str, Any], *, minimum_confirmations: int = 3) -> bool:
    if hypothesis.get("status") != "candidate":
        return False
    if hypothesis.get("explicit_human_validation"):
        return True
    return hypothesis.get("confirmations", 0) >= minimum_confirmations


def apply_eligible_rule_derivations(
    state: dict[str, Any],
    *,
    minimum_confirmations: int = 3,
) -> tuple[str, dict[str, Any], list[str]]:
    """Derive rules for every hypothesis whose gate is open."""
    out = deepcopy(state)
    hypotheses = out.get("hypotheses") or []
    rules = list(out.get("rules") or [])
    derived: list[str] = []
    changed = False

    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        hypothesis_id = hypothesis.get("id")
        if not isinstance(hypothesis_id, str):
            continue
        if not hypothesis_eligible_for_rule(
            hypothesis, minimum_confirmations=minimum_confirmations,
        ):
            continue
        status, next_rules, rule_id = derive_rule(
            hypotheses,
            rules,
            hypothesis_id=hypothesis_id,
            minimum_confirmations=minimum_confirmations,
        )
        if status == "RULE_CONFLICT":
            raise DerivationConflictError(
                f"rule {rule_id or hypothesis_id.removeprefix('hypothesis:')} conflicts with persisted state.",
                details={"hypothesis_id": hypothesis_id},
            )
        if status == "CHANGE" and rule_id is not None:
            changed = True
            rules = next_rules
            derived.append(rule_id)

    out["rules"] = rules
    return ("CHANGE" if changed else "NO_CHANGE"), out, derived


def apply_principle_derivations(
    state: dict[str, Any],
    rule_ids: list[str],
) -> tuple[str, dict[str, Any], list[str]]:
    """Derive principles for cited active rules with human approval already bound."""
    out = deepcopy(state)
    rules = out.get("rules") or []
    principles = list(out.get("principles") or [])
    derived: list[str] = []
    changed = False

    for rule_id in rule_ids:
        status, next_principles, principle_id = derive_principle(
            rules,
            principles,
            rule_id=rule_id,
            human_approved=True,
        )
        if status == "PRINCIPLE_CONFLICT":
            raise DerivationConflictError(
                f"principle for {rule_id} conflicts with persisted state.",
                details={"rule_id": rule_id},
            )
        if status == "CHANGE" and principle_id is not None:
            changed = True
            principles = next_principles
            derived.append(principle_id)

    out["principles"] = principles
    return ("CHANGE" if changed else "NO_CHANGE"), out, derived
