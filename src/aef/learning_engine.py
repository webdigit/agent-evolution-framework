from copy import deepcopy
from .learning_signals import detect_learning_signals
from .learning_lifecycle import observe, derive_hypothesis


def ingest_events(state, events):
    """Reference autonomous learning intake.

    Detects learning-worthy signals and turns only eligible signals into observations.
    It may propose hypotheses when evidence thresholds are met, but never rules/principles.
    """
    out = deepcopy(state)
    out.setdefault("signals", [])
    out.setdefault("observations", [])
    out.setdefault("hypotheses", [])

    sig_status, out["signals"] = detect_learning_signals(events, out["signals"])
    changed = sig_status == "CHANGE"

    for sig in out["signals"]:
        if sig.get("status") != "candidate":
            continue
        pattern = sig.get("pattern_key") or sig.get("rule_id") or sig["id"]
        # Stable observation per signal; updated evidence lives on the signal itself.
        oid = f"observation:{sig['id']}"
        summary = f"Learning signal detected: {sig['type']}"
        status, observations = observe(
            out["observations"], observation_id=oid, summary=summary,
            pattern_key=pattern, strength="strong" if sig["type"] == "convergent_corrections" else "normal"
        )
        if status == "CHANGE":
            changed = True
        out["observations"] = observations

    # Hypothesis formation remains evidence-gated. Signals with multiple evidence items
    # can be represented by multiple underlying observations in later richer adapters;
    # here we only derive when at least 2 distinct observations share a pattern.
    patterns = sorted({o.get("pattern_key") for o in out["observations"] if o.get("pattern_key")})
    for pattern in patterns:
        status, hypotheses, _ = derive_hypothesis(
            out["observations"], out["hypotheses"], pattern_key=pattern
        )
        if status == "CHANGE":
            changed = True
        out["hypotheses"] = hypotheses

    return ("CHANGE" if changed else "NO_CHANGE"), out
