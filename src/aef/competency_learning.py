from copy import deepcopy

from .identifiers import validate_competency_id


def ensure_competency(agent, competency_id, *, title=None, source="discovered"):
    """Create an unknown competency conservatively at L1.

    Existing competencies are never reset. New competencies start unproven and
    supervised regardless of the agent's global career level.
    """
    validate_competency_id(competency_id)
    existing = agent.get("competencies", {})
    if competency_id in existing:
        return "NO_CHANGE", deepcopy(agent)
    out = deepcopy(agent)
    competencies = out.setdefault("competencies", {})
    competencies[competency_id] = {
        "id": competency_id,
        "title": title or competency_id,
        "level": "L1",
        "xp": 0,
        "cases": 0,
        "trust": None,
        "complex_cases": 0,
        "recent_significant_errors": 0,
        "probation": False,
        "source": source,
    }
    return "CHANGE", out


def propose_transfer(agent, source_competency, target_competency, *, similarity, max_initial_trust=0.60):
    """Return a conservative transfer proposal, never a promotion.

    Transfer may seed hypotheses or orientation metadata, but must not copy
    maturity, XP, case counts, or proven Trust into a new competency.
    """
    validate_competency_id(source_competency)
    validate_competency_id(target_competency)
    src = agent.get("competencies", {}).get(source_competency)
    tgt = agent.get("competencies", {}).get(target_competency)
    if not src or not tgt:
        return {"status": "NOT_APPLICABLE", "reason": "missing-competency"}
    if similarity < 0.50:
        return {"status": "REJECT_TRANSFER", "reason": "low-similarity"}
    source_trust = src.get("trust")
    confidence = 0.0 if source_trust is None else min(max_initial_trust, source_trust * similarity)
    return {
        "status": "TRANSFER_HINT",
        "source": source_competency,
        "target": target_competency,
        "similarity": similarity,
        "orientation_confidence": round(confidence, 4),
        "effects": {
            "copy_level": False,
            "copy_xp": False,
            "copy_cases": False,
            "copy_trust": False,
            "allow_hypothesis_seeding": True,
        },
    }


def seed_transfer_hypothesis(existing_records, *, source_competency, target_competency, statement):
    """Create a stable, explicitly unvalidated transfer hypothesis."""
    validate_competency_id(source_competency)
    validate_competency_id(target_competency)
    from .knowledge import upsert_by_id
    record = {
        "id": f"transfer:{source_competency}->{target_competency}:{_slug(statement)}",
        "status": "candidate",
        "summary": statement,
        "evidence_ids": [],
        "source_competency": source_competency,
        "target_competency": target_competency,
        "transfer": True,
        "validated_in_target": False,
    }
    return upsert_by_id(existing_records, record)


def _slug(text):
    chars = []
    for ch in text.lower():
        if ch.isalnum():
            chars.append(ch)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-")[:80]
