from __future__ import annotations

from copy import deepcopy
from typing import Any

from .learning_lifecycle import confirm_hypothesis


CONFIRMATION_ELIGIBLE_KINDS = frozenset({"human_correction", "rule_mismatch"})
CONFIRMATION_IGNORED_KINDS = frozenset({"success", "help_request"})
CONFIRMATION_SOURCE_RECORDS_KEY = "confirmation_source_records"


def _candidate_hypotheses_by_pattern(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in state.get("hypotheses") or []:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "candidate":
            continue
        pattern_key = item.get("pattern_key")
        hypothesis_id = item.get("id")
        if isinstance(pattern_key, str) and isinstance(hypothesis_id, str):
            mapping[pattern_key] = item
    return mapping


def _source_record_key(source: dict[str, str]) -> tuple[str, str]:
    return (source["record_id"], source["digest"])


def _already_confirmed(hypothesis: dict[str, Any], source: dict[str, str]) -> bool:
    target = _source_record_key(source)
    for entry in hypothesis.get(CONFIRMATION_SOURCE_RECORDS_KEY) or []:
        if not isinstance(entry, dict):
            continue
        record_id = entry.get("record_id")
        digest = entry.get("digest")
        if isinstance(record_id, str) and isinstance(digest, str):
            if _source_record_key({"record_id": record_id, "digest": digest}) == target:
                return True
    return False


def _append_confirmation_source(
    hypotheses: list[Any],
    hypothesis_id: str,
    source: dict[str, str],
) -> list[Any]:
    out = deepcopy(hypotheses)
    for hypothesis in out:
        if hypothesis.get("id") != hypothesis_id:
            continue
        records = list(hypothesis.get(CONFIRMATION_SOURCE_RECORDS_KEY) or [])
        records.append(deepcopy(source))
        records.sort(key=lambda entry: (entry["record_id"], entry["digest"]))
        hypothesis[CONFIRMATION_SOURCE_RECORDS_KEY] = records
        return out
    return out


def apply_ingest_confirmations(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    citations: dict[str, dict[str, str]],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Increment hypothesis confirmations from eligible ingest events (voie A).

    At most one confirmation per candidate hypothesis per intake. Only
    human_correction and rule_mismatch events count; success and help_request
    are reported as ignored kinds.
    """
    out = deepcopy(state)
    hypotheses_by_pattern = _candidate_hypotheses_by_pattern(out)
    report: dict[str, Any] = {
        "confirmations_applied": [],
        "kinds_ignored": [],
    }
    changed = False
    confirmed_this_intake: set[str] = set()

    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        kind = event.get("kind")
        if kind in CONFIRMATION_IGNORED_KINDS:
            if isinstance(event_id, str):
                report["kinds_ignored"].append({"event_id": event_id, "kind": kind})
            continue
        if kind not in CONFIRMATION_ELIGIBLE_KINDS:
            continue
        pattern_key = event.get("pattern_key")
        if not isinstance(pattern_key, str) or not isinstance(event_id, str):
            continue
        hypothesis = hypotheses_by_pattern.get(pattern_key)
        if hypothesis is None:
            continue
        hypothesis_id = hypothesis["id"]
        if hypothesis_id in confirmed_this_intake:
            continue
        source = citations.get(event_id)
        if not isinstance(source, dict):
            continue
        record_id = source.get("record_id")
        digest = source.get("digest")
        if not isinstance(record_id, str) or not isinstance(digest, str):
            continue
        source_record = {"record_id": record_id, "digest": digest}
        if _already_confirmed(hypothesis, source_record):
            continue
        status, next_hypotheses = confirm_hypothesis(
            out.get("hypotheses") or [],
            hypothesis_id,
            explicit_human_validation=False,
        )
        if status != "CHANGE":
            continue
        out["hypotheses"] = _append_confirmation_source(
            next_hypotheses,
            hypothesis_id,
            source_record,
        )
        hypothesis = next(
            item for item in out["hypotheses"] if item.get("id") == hypothesis_id
        )
        hypotheses_by_pattern[pattern_key] = hypothesis
        confirmed_this_intake.add(hypothesis_id)
        changed = True
        report["confirmations_applied"].append({
            "hypothesis_id": hypothesis_id,
            "record_id": record_id,
            "event_id": event_id,
            "kind": kind,
        })

    return ("CHANGE" if changed else "NO_CHANGE"), out, report
