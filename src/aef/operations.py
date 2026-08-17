from copy import deepcopy
from .init_profiles import DEFAULT_CORE_FILES, get_default_core_files, get_init_profile
from .migrations import apply_migration
from .strict_json import InvalidStrictJSONError, validate_strict_json
from .transaction_guard import mutation_guard_metadata
from .consolidation import validate_consolidation_document
from .knowledge_state import (
    EVIDENCE_COLLECTIONS,
    InvalidKnowledgeStateError,
    validate_knowledge_state,
)


DISCOVERY_REGISTRY_PATH = ".agent/integrations/registry.json"
KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"
CONNECTOR_STATUSES = {"available", "unavailable", "deprecated", "unknown", "restricted"}
CAPABILITY_RISKS = {"R0", "R1", "R2", "R3", "R4"}


class InvalidDiscoverySnapshotError(ValueError):
    """Raised when an explicit connector discovery snapshot is invalid."""


class InvalidDiscoveryRegistryError(ValueError):
    """Raised when the persisted connector registry is invalid."""


def _non_empty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_connector_document(document, *, snapshot):
    error_type = InvalidDiscoverySnapshotError if snapshot else InvalidDiscoveryRegistryError
    if not isinstance(document, dict) or not isinstance(document.get("connectors"), list):
        raise error_type("invalid connector discovery document")
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise error_type("connector discovery document is not strict JSON") from exc

    connector_ids = set()
    normalized = []
    for connector in document["connectors"]:
        if not isinstance(connector, dict):
            raise error_type("invalid connector entry")
        connector_id = connector.get("id")
        status = connector.get("status")
        capabilities = connector.get("capabilities")
        if (
            not _non_empty_text(connector_id)
            or connector_id in connector_ids
            or status not in CONNECTOR_STATUSES
            or not isinstance(capabilities, list)
        ):
            raise error_type("invalid connector entry")
        connector_ids.add(connector_id)
        capability_ids = set()
        normalized_capabilities = []
        for capability in capabilities:
            if not isinstance(capability, dict):
                raise error_type("invalid capability entry")
            capability_id = capability.get("id")
            if (
                not _non_empty_text(capability_id)
                or capability_id in capability_ids
                or not _non_empty_text(capability.get("operation"))
                or capability.get("risk") not in CAPABILITY_RISKS
                or not isinstance(capability.get("reversible"), bool)
                or (
                    "available" in capability
                    and not isinstance(capability["available"], bool)
                )
            ):
                raise error_type("invalid capability entry")
            capability_ids.add(capability_id)
            if snapshot:
                normalized_capability = {
                    key: deepcopy(capability[key])
                    for key in (
                        "id", "operation", "risk", "reversible", "available",
                        "native_metadata",
                    )
                    if key in capability
                }
                normalized_capability.setdefault("available", True)
            else:
                normalized_capability = deepcopy(capability)
            normalized_capabilities.append(normalized_capability)
        normalized_connector = {
            "id": connector_id,
            "status": status,
            "capabilities": sorted(
                normalized_capabilities, key=lambda item: item["id"]
            ),
        }
        normalized.append(normalized_connector)
    return sorted(normalized, key=lambda item: item["id"])


def validate_discovery_snapshot(snapshot):
    """Validate and return authority-neutral runtime connector inventory."""
    return {"connectors": _validate_connector_document(snapshot, snapshot=True)}


def _merge_opaque_metadata(existing, discovered):
    """Recursively merge opaque objects; explicit non-objects replace values."""
    if not isinstance(existing, dict) or not isinstance(discovered, dict):
        return deepcopy(discovered)
    merged = deepcopy(existing)
    for key, value in discovered.items():
        if key in merged:
            merged[key] = _merge_opaque_metadata(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged

def _empty_manifest(instance_id, *, framework="aef", framework_version="0.1.0",
                    schema_version="1.0.0", created_at="1970-01-01T00:00:00Z"):
    return {
        "framework": framework,
        "framework_version": framework_version,
        "schema_version": schema_version,
        "instance_id": instance_id,
        "created_at": created_at,
    }


def init_project(project, *, instance_id, answers=None, required_decisions=None, core_files=None,
                 created_at="1970-01-01T00:00:00Z", profile=None):
    """Create-or-reconcile a synthetic project state.

    Project is a dict with `files` and optional `decisions`. Unknown files are preserved.
    Framework files are created only when missing; project content is never overwritten here.
    """
    out = deepcopy(project)
    transaction_guard = mutation_guard_metadata(out)
    if transaction_guard is not None:
        return "BLOCKED", out, {
            **transaction_guard, "unresolved_decisions": [],
        }
    answers = deepcopy(answers or {})
    profile_definition = get_init_profile(profile) if profile is not None else None
    profile_decisions = profile_definition.get("required_decisions", []) if profile_definition else []
    profile_decision_ids = [item["id"] for item in profile_decisions if item.get("required")]
    required_decisions = list(dict.fromkeys(profile_decision_ids + list(required_decisions or [])))
    existing_decisions = out.get("decisions", {}).get("decisions", [])

    manifest = out.get("files", {}).get(".agent/manifest.json")
    if manifest is not None:
        actual_framework = manifest.get("framework") if isinstance(manifest, dict) else None
        if actual_framework != "aef":
            return "BLOCKED", deepcopy(project), {
                "reason": "framework_mismatch",
                "expected_framework": "aef",
                "actual_framework": actual_framework,
                "unresolved_decisions": [],
            }
        actual_instance_id = manifest.get("instance_id")
        if actual_instance_id != instance_id:
            return "BLOCKED", deepcopy(project), {
                "reason": "instance_id_mismatch",
                "expected_instance_id": actual_instance_id,
                "requested_instance_id": instance_id,
                "unresolved_decisions": [],
            }
        if profile_definition is not None:
            expected_framework_version = profile_definition["framework_version"]
            actual_framework_version = manifest.get("framework_version")
            if actual_framework_version != expected_framework_version:
                return "BLOCKED", deepcopy(project), {
                    "reason": "framework_version_mismatch",
                    "expected_version": expected_framework_version,
                    "actual_version": actual_framework_version,
                    "message": "Use UPGRADE to align the workspace framework version before INIT.",
                    "unresolved_decisions": [],
                }
            expected_schema_version = profile_definition["schema_version"]
            actual_schema_version = manifest.get("schema_version")
            if actual_schema_version != expected_schema_version:
                return "BLOCKED", deepcopy(project), {
                    "reason": "schema_version_mismatch",
                    "expected_version": expected_schema_version,
                    "actual_version": actual_schema_version,
                    "message": "Use UPGRADE to align the workspace schema version before INIT.",
                    "unresolved_decisions": [],
                }

    for decision_id, value in answers.items():
        existing = next((item for item in existing_decisions if item.get("id") == decision_id), None)
        if existing is not None and existing.get("status") == "resolved" and existing.get("value") != value:
            return "BLOCKED", deepcopy(project), {
                "reason": "decision_conflict",
                "decision_id": decision_id,
                "unresolved_decisions": [],
            }

    missing = [d for d in required_decisions if d not in answers and not any(
        x.get("id") == d and x.get("status") == "resolved" for x in existing_decisions
    )]
    if missing:
        return "BLOCKED", deepcopy(project), {"unresolved_decisions": missing}

    for decision in profile_decisions:
        decision_id = decision["id"]
        if decision_id not in answers:
            continue
        value = answers[decision_id]
        if decision.get("value_type") == "string" and not isinstance(value, str):
            return "BLOCKED", deepcopy(project), {
                "reason": "invalid_decision",
                "decision_id": decision_id,
                "unresolved_decisions": [],
            }
        if decision.get("allow_empty") is False and isinstance(value, str) and not value.strip():
            return "BLOCKED", deepcopy(project), {
                "reason": "invalid_decision",
                "decision_id": decision_id,
                "unresolved_decisions": [],
            }

    out.setdefault("files", {})
    out.setdefault("decisions", {"decisions": []})
    changed = False
    for decision_id, value in sorted(answers.items()):
        existing = next((x for x in out["decisions"]["decisions"] if x.get("id") == decision_id), None)
        desired = {"id": decision_id, "status": "resolved", "value": value, "source": "human-confirmed"}
        if existing is None:
            out["decisions"]["decisions"].append(desired); changed = True
        elif existing != desired:
            existing.clear(); existing.update(desired); changed = True

    if ".agent/manifest.json" not in out["files"]:
        manifest_options = {
            "framework": profile_definition["framework"] if profile_definition else "aef",
            "framework_version": profile_definition["framework_version"] if profile_definition else "0.1.0",
            "schema_version": profile_definition["schema_version"] if profile_definition else "1.0.0",
        }
        out["files"][".agent/manifest.json"] = _empty_manifest(
            instance_id, created_at=created_at, **manifest_options
        )
        changed = True

    initial_files = profile_definition.get("initial_files", {}) if profile_definition else {
        ".agent/state/migrations.json": {"applied": []},
    }
    for path, content in sorted(initial_files.items()):
        if path not in out["files"]:
            out["files"][path] = deepcopy(content)
            changed = True

    selected_core_files = core_files or (
        profile_definition["core_files"] if profile_definition else get_default_core_files()
    )
    for path, content in sorted(selected_core_files.items()):
        if path not in out["files"]:
            out["files"][path] = content
            changed = True

    return ("CHANGE" if changed else "NO_CHANGE"), out, {"unresolved_decisions": []}


def migrate_project(project, *, migration_id, transform, from_version="legacy", to_version="1.0.0", postcondition=None):
    """Replay-safe migration preserving unknown/project-owned files.

    A postcondition allows recovery from the classic crash window where the
    migration effects were persisted but its ledger entry was not. When the
    postcondition is already true, AEF repairs the ledger without replaying the
    transform.
    """
    out = deepcopy(project)
    out.setdefault("files", {})
    ledger = deepcopy(out["files"].get(".agent/state/migrations.json", {"applied": []}))
    already_applied = any(x["id"] == migration_id and x["status"] == "applied" for x in ledger["applied"])
    if already_applied:
        return "NO_CHANGE", out

    if postcondition is not None and postcondition(out):
        ledger["applied"].append({
            "id": migration_id,
            "from_version": from_version,
            "to_version": to_version,
            "status": "applied",
            "recovered_from_postcondition": True,
        })
        out["files"][".agent/state/migrations.json"] = ledger
        return "CHANGE", out

    status, new_project, new_ledger = apply_migration(out, ledger, migration_id, from_version, to_version, transform)
    if status == "NO_CHANGE":
        return "NO_CHANGE", out
    if postcondition is not None and not postcondition(new_project):
        return "FAILED", out
    new_project.setdefault("files", {})
    new_project["files"][".agent/state/migrations.json"] = new_ledger
    return "CHANGE", new_project


def upgrade_project(project, *, target_version, migrations):
    """Apply an ordered, preflighted migration path to target_version.

    The complete path is resolved before the first mutation. This prevents a
    missing later migration from leaving an otherwise healthy project halfway
    upgraded. Re-running at the target is a no-op.
    """
    out = deepcopy(project)
    manifest = deepcopy(out.get("files", {}).get(".agent/manifest.json"))
    if not manifest:
        return "BLOCKED", out, {"reason": "missing_manifest"}
    current = manifest["schema_version"]
    if current == target_version:
        return "NO_CHANGE", out, {"applied": []}

    def vt(v): return tuple(int(x) for x in v.split("."))
    if vt(target_version) < vt(current):
        return "BLOCKED", out, {"reason": "implicit_downgrade_forbidden"}

    # Preflight the entire path before changing state.
    # Registry sanity: migration IDs are globally unique.
    ids = [m["id"] for m in migrations]
    if len(ids) != len(set(ids)):
        return "BLOCKED", out, {"reason": "duplicate_migration_id", "applied": []}

    path = []
    cursor = current
    seen = set()
    while cursor != target_version:
        if cursor in seen:
            return "BLOCKED", out, {"reason": "migration_cycle_detected", "current_version": cursor, "applied": []}
        seen.add(cursor)
        candidates = [m for m in migrations if m["from_version"] == cursor]
        if len(candidates) > 1:
            return "BLOCKED", out, {"reason": "ambiguous_migration_path", "current_version": cursor, "applied": []}
        candidate = candidates[0] if candidates else None
        if candidate is None or vt(candidate["to_version"]) > vt(target_version):
            return "BLOCKED", out, {"reason": "migration_path_missing", "current_version": cursor, "applied": []}
        if vt(candidate["to_version"]) <= vt(cursor):
            return "BLOCKED", out, {"reason": "non_forward_migration", "migration_id": candidate["id"], "applied": []}
        path.append(candidate)
        cursor = candidate["to_version"]

    applied = []
    changed = False
    for candidate in path:
        try:
            status, next_out = migrate_project(
                out, migration_id=candidate["id"], transform=candidate["transform"],
                from_version=candidate["from_version"], to_version=candidate["to_version"],
                postcondition=candidate.get("postcondition"),
            )
        except Exception as exc:
            # Transform runs against a deep copy, so current `out` is still the
            # last committed safe state. Expose deterministic failure metadata.
            return "FAILED", out, {"reason": "migration_failed", "migration_id": candidate["id"], "error_type": type(exc).__name__, "applied": applied}
        if status == "FAILED":
            return "FAILED", out, {"reason": "migration_postcondition_failed", "migration_id": candidate["id"], "applied": applied}
        if status == "CHANGE":
            changed = True
            applied.append(candidate["id"])
        out = next_out
        out["files"][".agent/manifest.json"]["schema_version"] = candidate["to_version"]

    return ("CHANGE" if changed else "NO_CHANGE"), out, {"applied": applied}


def audit_project(project):
    """Read-only structural audit. Never mutates project state."""
    files = project.get("files", {})
    manifest = files.get(".agent/manifest.json")
    findings = []
    if not manifest:
        findings.append({"id": "missing-manifest", "severity": "error"})
    if ".agent/state/migrations.json" not in files:
        findings.append({"id": "missing-migration-ledger", "severity": "warning"})
    if mutation_guard_metadata(project) is not None:
        findings.append({"id": "evaluation-recovery-required", "severity": "error"})
    knowledge_path = ".agent/knowledge/knowledge.json"
    if knowledge_path not in files:
        findings.append({"id": "missing-knowledge-state", "severity": "error"})
    else:
        from .knowledge_state import InvalidKnowledgeStateError
        from .schema_validation import validate_persisted_knowledge
        try:
            validate_persisted_knowledge(files[knowledge_path])
        except InvalidKnowledgeStateError:
            findings.append({"id": "invalid-knowledge-state", "severity": "error"})
    return {
        "status": "PASS" if not any(f["severity"] == "error" for f in findings) else "FAIL",
        "schema_version": manifest.get("schema_version") if manifest else None,
        "findings": findings,
    }


def discover_capabilities(registry, discovered_connectors):
    """Reconcile runtime connector/capability discovery by stable IDs.

    Policy annotations already present on known capabilities are preserved. Missing
    connectors/capabilities become unavailable rather than being deleted.
    """
    current = deepcopy(registry or {"connectors": []})
    out = deepcopy(current)
    existing_connectors = {c["id"]: c for c in out.setdefault("connectors", [])}
    discovered_ids = {c["id"] for c in discovered_connectors}

    for dconn in discovered_connectors:
        conn = existing_connectors.get(dconn["id"])
        if conn is None:
            conn = {"id": dconn["id"], "status": dconn.get("status", "available"), "capabilities": []}
            out["connectors"].append(conn)
            existing_connectors[dconn["id"]] = conn
        else:
            conn["status"] = dconn.get("status", "available")

        existing_caps = {c["id"]: c for c in conn.get("capabilities", [])}
        dcap_ids = {c["id"] for c in dconn.get("capabilities", [])}
        for dcap in dconn.get("capabilities", []):
            existing = existing_caps.get(dcap["id"])
            if existing is None:
                conn.setdefault("capabilities", []).append(deepcopy(dcap))
            else:
                # Runtime technical fields refresh; governance annotations survive.
                protected = {k: deepcopy(v) for k, v in existing.items() if k not in {
                    "operation", "reversible", "available", "native_metadata"
                }}
                refreshed = deepcopy(existing)
                for k in ("operation", "reversible", "available"):
                    if k in dcap:
                        refreshed[k] = deepcopy(dcap[k])
                if "native_metadata" in dcap:
                    refreshed["native_metadata"] = _merge_opaque_metadata(
                        existing.get("native_metadata"), dcap["native_metadata"]
                    )
                refreshed.update(protected)
                existing.clear(); existing.update(refreshed)
        for cap in conn.get("capabilities", []):
            if cap["id"] not in dcap_ids:
                cap["available"] = False
        conn["capabilities"] = sorted(conn.get("capabilities", []), key=lambda c: c["id"])

    for conn in out["connectors"]:
        if conn["id"] not in discovered_ids:
            conn["status"] = "unavailable"
            for cap in conn.get("capabilities", []):
                cap["available"] = False
    out["connectors"] = sorted(out["connectors"], key=lambda c: c["id"])
    return ("NO_CHANGE" if out == current else "CHANGE"), out


def discover_project(project, snapshot):
    """Reconcile an explicit connector snapshot into one initialized workspace.

    Discovery updates inventory only. It never changes decisions, progression,
    policies, or any other source of execution authority.
    """
    source = deepcopy(project)
    transaction_guard = mutation_guard_metadata(source)
    if transaction_guard is not None:
        return "BLOCKED", source, {
            **transaction_guard, "authority_granted": False,
        }
    normalized_snapshot = validate_discovery_snapshot(snapshot)
    files = source.get("files")
    manifest = files.get(".agent/manifest.json") if isinstance(files, dict) else None
    if not isinstance(manifest, dict) or manifest.get("framework") != "aef":
        return "BLOCKED", source, {
            "reason": "workspace_not_initialized",
            "authority_granted": False,
        }

    registry = files.get(DISCOVERY_REGISTRY_PATH, {"connectors": []})
    _validate_connector_document(registry, snapshot=False)
    status, reconciled = discover_capabilities(
        registry, normalized_snapshot["connectors"]
    )
    out = deepcopy(source)
    if status == "CHANGE":
        out["files"][DISCOVERY_REGISTRY_PATH] = reconciled
    final_registry = reconciled if status == "CHANGE" else registry
    connectors = final_registry["connectors"]
    meta = {
        "registry_path": DISCOVERY_REGISTRY_PATH,
        "connector_count": len(connectors),
        "available_connector_count": sum(
            connector.get("status") == "available" for connector in connectors
        ),
        "capability_count": sum(
            len(connector.get("capabilities", [])) for connector in connectors
        ),
        "authority_granted": False,
    }
    return status, out, meta


def consolidate_knowledge(state, *, rule_reviews=None):
    """Deterministic conservative consolidation pass.

    V1 delegates rule lifecycle decisions to explicit review instructions generated
    from evidence. Replaying the same review instructions is idempotent.
    """
    from .rule_lifecycle import review_rule
    out = deepcopy(state)
    out.setdefault("rules", [])
    changed = False
    decisions = []
    for review in sorted(rule_reviews or [], key=lambda r: r["rule_id"]):
        decision, rules, affected = review_rule(
            out["rules"],
            rule_id=review["rule_id"],
            contradictions=review.get("contradictions", 0),
            contexts=review.get("contexts"),
            replacement=review.get("replacement"),
            reason=review.get("reason", "consolidation"),
            evidence_ids=review.get("evidence_ids"),
            retire_threshold=review.get("retire_threshold", 3),
        )
        if rules != out["rules"]:
            changed = True
        out["rules"] = rules
        decisions.append({"rule_id": review["rule_id"], "decision": decision, "affected": affected})
    return ("CHANGE" if changed else "NO_CHANGE"), out, decisions


def _consolidation_event(review):
    event = {
        "review_id": review["id"],
        "rule_id": review["rule_id"],
        "action": review["action"],
        "reason": review["reason"],
        "evidence_ids": sorted(review["evidence_ids"]),
        "approval": deepcopy(review["approval"]),
    }
    if review["action"] == "specialize":
        event["context"] = deepcopy(review["context"])
    elif review["action"] == "supersede":
        event["replacement"] = deepcopy(review["replacement"])
    return event


def _events_by_review_id(rules):
    found = {}
    for rule in rules:
        lifecycle = rule.get("lifecycle")
        if not isinstance(lifecycle, dict):
            continue
        for event in lifecycle.values():
            if isinstance(event, dict) and isinstance(event.get("review_id"), str):
                found.setdefault(event["review_id"], []).append({
                    "rule_id": rule.get("id"), "event": event,
                    "legacy": "rule_id" not in event,
                })
    return found


def consolidate_project(project, review_document):
    """Apply one fully preflighted V1 rule-review lot to project knowledge only."""
    source = deepcopy(project)
    transaction_guard = mutation_guard_metadata(source)
    if transaction_guard is not None:
        return "BLOCKED", source, {
            **transaction_guard, "decisions": [], "authority_granted": False,
        }
    document = validate_consolidation_document(review_document)
    files = source.get("files")
    manifest = files.get(".agent/manifest.json") if isinstance(files, dict) else None
    if not isinstance(manifest, dict) or manifest.get("framework") != "aef":
        return "BLOCKED", source, {
            "reason": "workspace_not_initialized", "decisions": [],
            "authority_granted": False,
        }
    if KNOWLEDGE_PATH not in files:
        raise InvalidKnowledgeStateError("knowledge state is missing")
    knowledge = files[KNOWLEDGE_PATH]
    validate_knowledge_state(knowledge)
    rules = knowledge["rules"]

    evidence_index = {}
    all_knowledge_ids = set()
    for collection in EVIDENCE_COLLECTIONS:
        for record in knowledge.get(collection, []):
            evidence_index.setdefault(record["id"], []).append(collection)
            all_knowledge_ids.add(record["id"])
    for principle in knowledge.get("principles", []):
        all_knowledge_ids.add(principle["id"])
    prior_events = _events_by_review_id(rules)
    rule_by_id = {rule["id"]: rule for rule in rules}
    replay_ids = set()
    replacement_ids = set()

    # Validate every state-dependent precondition before constructing transitions.
    for review in document["reviews"]:
        rule = rule_by_id.get(review["rule_id"])
        if rule is None:
            return "BLOCKED", source, {
                "reason": "rule_not_found", "review_id": review["id"],
                "decisions": [], "authority_granted": False,
            }
        existing_events = prior_events.get(review["id"], [])
        if existing_events:
            if any(item["legacy"] for item in existing_events):
                return "BLOCKED", source, {
                    "reason": "legacy_review_identity_unverifiable",
                    "review_id": review["id"], "decisions": [],
                    "authority_granted": False,
                }
            expected = _consolidation_event(review) if review["action"] != "keep" else None
            if (
                len(existing_events) != 1
                or existing_events[0]["rule_id"] != review["rule_id"]
                or existing_events[0]["event"] != expected
            ):
                return "BLOCKED", source, {
                    "reason": "review_id_conflict", "review_id": review["id"],
                    "decisions": [], "authority_granted": False,
                }
            replay_ids.add(review["id"])
            continue
        for evidence_id in review["evidence_ids"]:
            matches = evidence_index.get(evidence_id, [])
            if len(matches) != 1:
                return "BLOCKED", source, {
                    "reason": (
                        "ambiguous_evidence_reference" if matches
                        else "missing_evidence_reference"
                    ),
                    "review_id": review["id"], "evidence_id": evidence_id,
                    "decisions": [], "authority_granted": False,
                }
        if review["action"] != "keep":
            if rule["status"] in {"superseded", "retired"}:
                return "BLOCKED", source, {
                    "reason": "rule_transition_conflict", "review_id": review["id"],
                    "decisions": [], "authority_granted": False,
                }
            if review["action"] == "specialize" and rule["status"] == "specialized":
                return "BLOCKED", source, {
                    "reason": "rule_already_specialized", "review_id": review["id"],
                    "decisions": [], "authority_granted": False,
                }
            if review["action"] == "supersede":
                replacement_id = review["replacement"]["id"]
                if replacement_id in all_knowledge_ids or replacement_id in replacement_ids:
                    return "BLOCKED", source, {
                        "reason": "knowledge_identifier_conflict", "review_id": review["id"],
                        "decisions": [], "authority_granted": False,
                    }
                replacement_ids.add(replacement_id)

    from .rule_lifecycle import retire_rule, specialize_rule, supersede_rule
    candidate = deepcopy(knowledge)
    decisions = []
    changed_ids = []
    for review in document["reviews"]:
        if review["id"] in replay_ids:
            decisions.append({
                "review_id": review["id"], "rule_id": review["rule_id"],
                "decision": "NO_CHANGE", "affected_rule_ids": [],
            })
            continue
        action = review["action"]
        if action == "keep":
            decisions.append({
                "review_id": review["id"], "rule_id": review["rule_id"],
                "decision": "KEEP", "affected_rule_ids": [],
            })
            continue
        event = _consolidation_event(review)
        if action == "specialize":
            _, candidate["rules"] = specialize_rule(
                candidate["rules"], rule_id=review["rule_id"],
                context=review["context"], reason=review["reason"],
                evidence_ids=review["evidence_ids"],
            )
            target = next(r for r in candidate["rules"] if r["id"] == review["rule_id"])
            target["lifecycle"]["specialized"] = event
            affected = [review["rule_id"]]
        elif action == "supersede":
            _, candidate["rules"], replacement_id = supersede_rule(
                candidate["rules"], rule_id=review["rule_id"],
                replacement=review["replacement"], reason=review["reason"],
                evidence_ids=review["evidence_ids"],
            )
            target = next(r for r in candidate["rules"] if r["id"] == review["rule_id"])
            target["lifecycle"]["superseded"] = event
            affected = [review["rule_id"], replacement_id]
        else:
            _, candidate["rules"] = retire_rule(
                candidate["rules"], rule_id=review["rule_id"],
                reason=review["reason"], evidence_ids=review["evidence_ids"],
            )
            target = next(r for r in candidate["rules"] if r["id"] == review["rule_id"])
            target["lifecycle"]["retired"] = event
            affected = [review["rule_id"]]
        changed_ids.extend(affected)
        decisions.append({
            "review_id": review["id"], "rule_id": review["rule_id"],
            "decision": action.upper(), "affected_rule_ids": affected,
        })

    validate_knowledge_state(candidate)
    status = "CHANGE" if candidate != knowledge else "NO_CHANGE"
    out = deepcopy(source)
    if status == "CHANGE":
        out["files"][KNOWLEDGE_PATH] = candidate
    return status, out, {
        "reason": None,
        "decisions": decisions,
        "review_count": len(document["reviews"]),
        "changed_rule_ids": sorted(set(changed_ids)),
        "authority_granted": False,
    }
