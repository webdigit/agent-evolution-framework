from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
import uuid
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._version import __version__

from .decisions import (
    ROLE_DECISION_ID,
    InvalidDecisionsDocumentError,
    validate_decisions_document,
)
from .filesystem import (
    CompetencyDeclarationRecoveryRequiredError,
    EvaluationRecoveryRequiredError,
    UpgradeRecoveryRequiredError,
    WorkspaceContentionError,
    apply_workspace,
    load_workspace,
    plan_workspace,
)
from .upgrade_plan import MigrationFailure
from .upgrade_ops import run_upgrade
from .runtime_discovery import DECISION_INSTALL_REQUIRED, INSTALL_REQUIRED_EXIT
from .runtime_doctor import diagnose_runtime
from .consolidation import InvalidConsolidationInputError
from .knowledge_state import InvalidKnowledgeStateError
from .evaluation_engine import (
    InvalidCareerStateError,
    InvalidCompetencyStateError,
    InvalidEvaluationDecisionsError,
    evaluate_project,
    list_project_recommendations,
    refresh_project_recommendations,
)
from .evaluation_transaction import (
    InvalidEvaluationTransactionError,
    apply_evaluation_transaction,
    recover_evaluation_transaction,
)
from .promotion_recommendations import InvalidPromotionRecommendationStateError
from .claude_filesystem import (
    ClaudeIntegrationFilesystemError,
    apply_claude_bridge,
    claude_bridge_diff,
    read_claude_bridge,
    validate_claude_doctrine_files,
)
from .claude_integration import (
    CLAUDE_BRIDGE_PATH,
    validate_claude_integration_workspace,
)
from .guidance_filesystem import (
    GuidanceFilesystemError,
    apply_guidance_file,
    door_path,
    guidance_diff,
    read_guidance_file,
)
from .guidance_integration import (
    AGENTS_PATH,
    plan_claude_door,
    plan_door_integration,
)
from .operations import (
    InvalidDiscoveryRegistryError,
    InvalidDiscoverySnapshotError,
    audit_project,
    discover_project,
    consolidate_project,
    init_project,
)
from .record_document import InvalidRecordSubmissionError, build_persisted_record
from .record_store import InvalidRecordStoreError, persist_record
from .ingest_intake import IngestBlockedError, InvalidIngestSubmissionError
from .competency_declaration import (
    CompetencyDeclarationBlockedError,
    InvalidCompetencyDeclarationError,
)
from .competency_declaration_ops import plan_declare, recover_declaration
from .competency_declaration_transaction import InvalidCompetencyDeclarationTransactionError
from .ingest_ops import plan_ingest
from .strict_json import DuplicateJSONKeyError, reject_duplicate_keys


API_VERSION = "aef.cli/v1"
ROLE_DECISION = ROLE_DECISION_ID


class CLIInputError(ValueError):
    """A stable, intentionally public CLI input error."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.public_message = message
        self.details = details or {}
        super().__init__(message)


def _distribution_version() -> str:
    return __version__


def _envelope(
    *,
    command: str,
    workspace: str | Path,
    status: str,
    ok: bool,
    dry_run: bool,
    result: dict[str, Any],
    meta: dict[str, Any],
    diff: dict[str, list[str]] | None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "command": command,
        "ok": ok,
        "status": status,
        "workspace": Path(workspace).resolve().as_posix(),
        "dry_run": dry_run,
        "result": result,
        "meta": meta,
        "diff": diff,
        "error": error,
    }


def _write_envelope(envelope: dict[str, Any], *, compact: bool) -> None:
    if compact:
        payload = json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    else:
        payload = json.dumps(envelope, ensure_ascii=True, sort_keys=True, indent=2)
    _write_stdout(payload + "\n")


def _write_stdout(payload: str) -> None:
    """Write once, escaping characters unsupported by the terminal encoding."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe_payload = payload.encode(encoding, "backslashreplace").decode(encoding)
    sys.stdout.write(safe_payload)


def _escape_human_value(value: Any) -> str:
    """Make one dynamic terminal value visible and single-line safe."""
    try:
        text = str(value)
    except Exception:
        text = "unavailable"
    named = {
        "\n": r"\n",
        "\r": r"\r",
        "\t": r"\t",
        "\b": r"\b",
        "\f": r"\f",
        "\x1b": r"\x1b",
    }
    escaped = []
    for character in text:
        if character in named:
            escaped.append(named[character])
            continue
        codepoint = ord(character)
        category = unicodedata.category(character)
        if (
            codepoint <= 0x1F
            or 0x7F <= codepoint <= 0x9F
            or category in {"Cc", "Cf", "Zl", "Zp"}
        ):
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
            continue
        escaped.append(character)
    return "".join(escaped)


def _doctor_context_lines(result: dict[str, Any]) -> list[str]:
    """Trust-qualifying doctor fields shared across PASS, INSTALL_REQUIRED, and BLOCKED."""
    lines: list[str] = []
    lines.append(f"Platform  : {_escape_human_value(result.get('platform', 'unknown'))}")
    lines.append(f"Arch      : {_escape_human_value(result.get('architecture', 'unknown'))}")
    lines.append(f"Interpreter : {_escape_human_value(result.get('interpreter', 'unknown'))}")
    found = _escape_human_value(result.get("found_package_version") or "none")
    expected = result.get("expected_package_version")
    lines.append(f"Found     : {found}")
    if expected is not None:
        lines.append(f"Expected  : {_escape_human_value(expected)}")
    running = result.get("running_module_version")
    if running:
        lines.append(f"Running   : {_escape_human_value(running)}")
    lines.append(f"Venv      : {_escape_human_value(result.get('venv_status', 'unknown'))}")
    lines.append(f"Method    : {_escape_human_value(result.get('discovery_method', 'none'))}")
    declared_source = result.get("declared_version_source")
    if declared_source:
        lines.append(f"Source    : {_escape_human_value(declared_source)}")
    if result.get("discovery_method") == "declared_env":
        lines.append("Trust     : tree read only (pip install not verified)")
    mismatch = result.get("declared_env_mismatch")
    if mismatch:
        lines.append(
            "Declared  : "
            + _escape_human_value(mismatch.get("path", "?"))
            + " ("
            + _escape_human_value(mismatch.get("version", "?"))
            + ", skipped)",
        )
    artifact = result.get("local_artifact")
    if artifact and artifact not in {"absent", ""}:
        lines.append(f"Artifact  : {_escape_human_value(artifact)}")
    offline_basis = result.get("offline_basis")
    if offline_basis:
        lines.append(
            f"Offline   : {_escape_human_value(offline_basis)} "
            "(self-attested checksum from the workspace)",
        )
    init = result.get("workspace_compatible")
    if init is True:
        lines.append("Workspace init : yes")
    elif init is False:
        lines.append("Workspace init : no")
    elif init is None:
        lines.append("Workspace init : unknown (unreadable manifest)")
    network = result.get("network_required")
    if network is True:
        lines.append("Network   : yes")
    elif network is False:
        lines.append("Network   : no")
    return lines


def _display_workspace(envelope: dict[str, Any]) -> str:
    return str(Path(envelope["workspace"]))


def _human_finding(finding: Any) -> str:
    if isinstance(finding, str):
        return _escape_human_value(finding)
    if isinstance(finding, dict):
        for public_field in ("id", "message"):
            public_value = finding.get(public_field)
            if isinstance(public_value, str) and public_value:
                if public_field == "id":
                    public_value = public_value.replace("-", " ")
                return _escape_human_value(public_value)
        return "Unidentified audit finding"
    return "Unidentified audit finding"


_INCOMPLETE_HUMAN_RESULT = (
    "[ERROR] AEF returned an incomplete result\n\n"
    "Some result details are unavailable.\n"
)


def _valid_human_envelope(envelope: Any) -> bool:
    """Check only the common protocol shape needed by the renderer."""
    try:
        if not isinstance(envelope, dict):
            return False
        required = {"command", "status", "workspace", "result", "meta", "diff", "error"}
        if not required.issubset(envelope):
            return False
        command = envelope["command"]
        status = envelope["status"]
        if command not in {
            "INIT", "AUDIT", "DISCOVER", "CONSOLIDATE", "EVALUATE", "INTEGRATE",
            "RECORD", "UPGRADE", "DOCTOR", "INGEST", "COMPETENCY_DECLARE",
        } or not isinstance(status, str):
            return False
        allowed = {
            "INIT": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "AUDIT": {"PASS", "FAIL", "FAILED", "ERROR"},
            "DISCOVER": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "CONSOLIDATE": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "EVALUATE": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "INTEGRATE": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "RECORD": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "UPGRADE": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "DOCTOR": {
                "PASS", "INSTALL_REQUIRED", "BLOCKED", "FAILED", "ERROR",
            },
            "INGEST": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
            "COMPETENCY_DECLARE": {"CHANGE", "NO_CHANGE", "BLOCKED", "FAILED", "ERROR"},
        }
        if status not in allowed[command]:
            return False
        if not isinstance(envelope["workspace"], str) or not envelope["workspace"]:
            return False
        if not isinstance(envelope["result"], dict) or not isinstance(envelope["meta"], dict):
            return False
        diff = envelope["diff"]
        if diff is not None:
            if not isinstance(diff, dict):
                return False
            if not all(
                isinstance(diff.get(field), list)
                for field in ("created", "modified", "removed")
            ):
                return False
        error = envelope["error"]
        if status == "ERROR":
            if not isinstance(error, dict):
                return False
            if not isinstance(error.get("code"), str) or not isinstance(
                error.get("message"), str
            ):
                return False
        elif error is not None:
            return False
        if command == "AUDIT" and status in {"PASS", "FAIL"}:
            if not isinstance(envelope["result"].get("findings"), list):
                return False
        return True
    except Exception:
        return False


def _render_human(envelope: dict[str, Any]) -> str:
    """Render the common protocol envelope without reimplementing business logic."""
    if not _valid_human_envelope(envelope):
        return _INCOMPLETE_HUMAN_RESULT
    try:
        command = envelope["command"]
        status = envelope["status"]
        result = envelope["result"]
        workspace = _escape_human_value(_display_workspace(envelope))

        if status == "ERROR":
            error = envelope["error"]
            message = _escape_human_value(error["message"])
            code = _escape_human_value(error["code"])
            return f"[ERROR] {message}\n\nCode      : {code}\n"

        if status == "BLOCKED" and envelope["meta"].get("reason") == "upgrade_recovery_required":
            return (
                "[BLOCKED] AEF upgrade recovery is required\n\n"
                f"Workspace : {workspace}\n"
                "Reason    : upgrade recovery required\n"
            )

        if command == "INIT":
            if status == "CHANGE":
                created = len(envelope["diff"]["created"])
                role = _escape_human_value(result.get("role") or "unknown")
                framework_version = _escape_human_value(
                    result.get("framework_version", "unknown")
                )
                created_text = _escape_human_value(created)
                return (
                    "[OK] AEF initialized\n\n"
                    f"Role      : {role}\n"
                    f"Version   : {framework_version}\n"
                    f"Workspace : {workspace}\n"
                    f"Files     : {created_text} created\n"
                )
            if status == "NO_CHANGE":
                framework_version = _escape_human_value(
                    result.get("framework_version", "unknown")
                )
                return (
                    "[OK] AEF is already initialized\n\n"
                    f"Version   : {framework_version}\n"
                    f"Workspace : {workspace}\n"
                    "Changes   : none\n"
                )
            if status == "BLOCKED":
                unresolved = result.get("unresolved_decisions", [])
                if isinstance(unresolved, list) and ROLE_DECISION in unresolved:
                    return (
                        "[BLOCKED] AEF initialization requires a primary role\n\n"
                        "Run:\n"
                        "aef init --role generalist-agent\n"
                    )
                reason = envelope["meta"].get("reason", "blocked")
                readable = _escape_human_value(reason).replace("_", " ")
                return (
                    "[BLOCKED] AEF initialization blocked\n\n"
                    f"Reason    : {readable}\n"
                    f"Workspace : {workspace}\n"
                )

        if command == "AUDIT":
            findings = result["findings"]
            if status == "PASS":
                return (
                    "[OK] AEF audit passed\n\n"
                    f"Workspace : {workspace}\n"
                    "Findings  : none\n"
                )
            if status == "FAIL":
                lines = "\n".join(f"- {_human_finding(item)}" for item in findings)
                return f"[FAILED] AEF audit found problems\n\n{lines}\n"

        if command == "DISCOVER":
            connectors = _escape_human_value(result.get("connectors", "unknown"))
            capabilities = _escape_human_value(
                result.get("capabilities", "unknown")
            )
            if status == "CHANGE":
                heading = (
                    "[OK] Connector discovery would update the registry"
                    if envelope.get("dry_run") is True
                    else "[OK] Connector discovery updated the registry"
                )
                return (
                    f"{heading}\n\n"
                    f"Workspace   : {workspace}\n"
                    f"Connectors  : {connectors}\n"
                    f"Capabilities: {capabilities}\n"
                    "Authority   : unchanged\n"
                )
            if status == "NO_CHANGE":
                return (
                    "[OK] Connector discovery found no changes\n\n"
                    f"Workspace   : {workspace}\n"
                    f"Connectors  : {connectors}\n"
                    f"Capabilities: {capabilities}\n"
                    "Authority   : unchanged\n"
                    "Changes     : none\n"
                )
            if status == "BLOCKED":
                return (
                    "[BLOCKED] Connector discovery requires an initialized AEF workspace\n\n"
                    f"Workspace : {workspace}\n"
                )

        if command == "CONSOLIDATE":
            reviews = _escape_human_value(result.get("reviews", "unknown"))
            changed = _escape_human_value(len(result.get("rules_changed", [])))
            if status == "CHANGE":
                heading = (
                    "[OK] AEF knowledge would be consolidated"
                    if envelope.get("dry_run") is True
                    else "[OK] AEF knowledge consolidated"
                )
                suffix = " would change" if envelope.get("dry_run") is True else " changed"
                return (
                    f"{heading}\n\nWorkspace : {workspace}\nReviews   : {reviews}\n"
                    f"Rules     : {changed}{suffix}\nAuthority : unchanged\n"
                )
            if status == "NO_CHANGE":
                return (
                    "[OK] AEF knowledge needs no consolidation\n\n"
                    f"Workspace : {workspace}\nReviews   : {reviews}\n"
                    "Changes   : none\nAuthority : unchanged\n"
                )
            if status == "BLOCKED":
                reason = _escape_human_value(envelope["meta"].get("reason", "blocked"))
                return (
                    "[BLOCKED] AEF knowledge consolidation is blocked\n\n"
                    f"Reason    : {reason.replace('_', ' ')}\nWorkspace : {workspace}\n"
                )

        if command == "EVALUATE":
            pending = _escape_human_value(result.get("pending_recommendations", 0))
            if status == "CHANGE":
                if result.get("recovery_action"):
                    action = _escape_human_value(result["recovery_action"])
                    heading = (
                        "[OK] AEF evaluation recovery would be applied"
                        if envelope.get("dry_run") is True
                        else "[OK] AEF evaluation recovery completed"
                    )
                    return f"{heading}\n\nWorkspace : {workspace}\nAction    : {action}\n"
                heading = (
                    "[OK] AEF evaluation would be applied"
                    if envelope.get("dry_run") is True
                    else "[OK] AEF evaluation completed"
                )
                approved = _escape_human_value(len(result.get("approved", [])))
                rejected = _escape_human_value(len(result.get("rejected", [])))
                levels = _escape_human_value(len(result.get("levels_changed", [])))
                return (
                    f"{heading}\n\nApproved : {approved}\nRejected : {rejected}\n"
                    f"Levels   : {levels} changed\nPending  : {pending}\n"
                )
            if status == "NO_CHANGE":
                recommendations = result.get("recommendations")
                if isinstance(recommendations, list):
                    if not recommendations:
                        return (
                            "[OK] No promotion recommendations require review\n\n"
                            f"Workspace : {workspace}\nPending   : none\n"
                        )
                    lines = []
                    for item in recommendations:
                        scope = item.get("scope")
                        label = "Career" if scope == "career" else (
                            "Competency " + _escape_human_value(item.get("competency_id", "unknown"))
                        )
                        levels = (
                            _escape_human_value(item.get("from_level", "unknown"))
                            + " -> " + _escape_human_value(item.get("to_level", "unknown"))
                        )
                        stale = " [stale]" if item.get("stale") is True else ""
                        lines.append(f"- {label}: {levels}{stale}")
                    recovery = (
                        "\nRecovery : required" if envelope["meta"].get("recovery_required") else ""
                    )
                    return (
                        "[OK] Promotion recommendations require review\n\n"
                        f"Workspace : {workspace}\nPending   : {pending}\n\n"
                        + "\n".join(lines) + recovery + "\n"
                    )
                return (
                    "[OK] AEF evaluation needs no changes\n\n"
                    f"Workspace : {workspace}\nChanges   : none\nPending   : {pending}\n"
                )
            if status == "BLOCKED":
                reason = _escape_human_value(envelope["meta"].get("reason", "blocked"))
                return (
                    "[BLOCKED] AEF evaluation cannot proceed\n\n"
                    f"Reason    : {reason.replace('_', ' ')}\nWorkspace : {workspace}\n"
                )

        if command == "INTEGRATE":
            scope = _escape_human_value(result.get("scope", "project"))
            doctrine = _escape_human_value(result.get("doctrine_files", 0))
            enforcement = _escape_human_value(
                result.get("enforcement", "guidance_only")
            ).replace("_", " ")
            if status == "BLOCKED":
                reason = _escape_human_value(envelope["meta"].get("reason", "blocked"))
                return (
                    "[BLOCKED] Guidance integration cannot be updated safely\n\n"
                    f"Reason    : {reason.replace('_', ' ')}\n"
                    f"Workspace : {workspace}\n"
                )
            if result.get("status_only") is True:
                warnings = result.get("warnings", [])
                warning_text = (
                    "none" if not warnings else ", ".join(
                        _escape_human_value(item) for item in warnings
                    )
                )
                integration = _escape_human_value(result.get("integration", "guidance"))
                if not result.get("installed"):
                    return (
                        f"[OK] Guidance integration ({integration}) is not installed\n\n"
                        f"Workspace : {workspace}\nWarnings  : {warning_text}\n"
                    )
                audit = _escape_human_value(result.get("audit", "unknown"))
                reviews = _escape_human_value(result.get("pending_reviews", "unknown"))
                return (
                    f"[OK] Guidance integration ({integration}) is healthy\n\n"
                    f"Doctrine : loaded\nAudit    : {audit}\n"
                    f"Reviews  : {reviews} pending\nWarnings : {warning_text}\n"
                )
            action = result.get("action")
            integration = _escape_human_value(result.get("integration", "guidance"))
            if status == "CHANGE":
                if action == "remove":
                    heading = (
                        f"[OK] Guidance integration ({integration}) would be removed"
                        if envelope.get("dry_run") else
                        f"[OK] Guidance integration ({integration}) removed"
                    )
                else:
                    heading = (
                        f"[OK] Guidance integration ({integration}) would be installed"
                        if envelope.get("dry_run") else
                        f"[OK] Guidance integration ({integration}) installed"
                    )
                return (
                    f"{heading}\n\nScope       : {scope}\n"
                    f"Doctrine    : {doctrine} files linked\n"
                    f"Workspace   : {workspace}\nEnforcement : {enforcement}\n"
                )
            if action == "remove":
                return (
                    f"[OK] Guidance integration ({integration}) is not installed\n\n"
                    "Changes : none\n"
                )
            return (
                f"[OK] Guidance integration ({integration}) is already installed\n\n"
                "Changes : none\n"
            )

        if command == "UPGRADE":
            if status == "CHANGE":
                if envelope.get("dry_run"):
                    heading = "AEF upgrade plan is ready"
                elif envelope["result"].get("recovery_action") in {"rollback", "finalize"}:
                    heading = "AEF recovered the upgrade"
                else:
                    heading = "AEF upgraded the workspace"
                return (
                    f"[OK] {heading}\n\n"
                    f"Workspace : {workspace}\n"
                )
            if status == "NO_CHANGE":
                return (
                    "[OK] AEF workspace is already current\n\n"
                    f"Workspace : {workspace}\n"
                    "Changes   : none\n"
                )
            if status == "BLOCKED":
                reason = _escape_human_value(envelope["meta"].get("reason", "blocked"))
                return (
                    "[BLOCKED] AEF upgrade is blocked\n\n"
                    f"Reason    : {reason}\n"
                    f"Workspace : {workspace}\n"
                )
            if status == "FAILED":
                return (
                    "[FAILED] AEF upgrade failed\n\n"
                    f"Workspace : {workspace}\n"
                )

        if command == "DOCTOR":
            if status == "PASS":
                observations = result.get("observations") or []
                lines = ["[OK] AEF runtime is ready\n"]
                lines.extend(_doctor_context_lines(result))
                if observations:
                    lines.append(
                        f"Notes     : {_escape_human_value(', '.join(str(item) for item in observations))}"
                    )
                lines.append(f"Workspace : {workspace}")
                return "\n".join(lines) + "\n"
            if status == "INSTALL_REQUIRED":
                command_line = _escape_human_value(result.get("install_command") or "")
                observations = result.get("observations") or []
                lines = [
                    "[INSTALL_REQUIRED] No compatible AEF runtime\n",
                ]
                lines.extend(_doctor_context_lines(result))
                if observations:
                    lines.append(
                        f"Notes     : {_escape_human_value(', '.join(str(item) for item in observations))}"
                    )
                lines.extend([
                    f"Install   : {command_line}",
                    "Action    : run the Install command manually after review",
                    f"Workspace : {workspace}",
                ])
                return "\n".join(lines) + "\n"
            if status == "BLOCKED":
                cause = _escape_human_value(
                    envelope["meta"].get("blocked_cause")
                    or result.get("blocked_cause")
                    or "unknown"
                )
                blocked_path = envelope["meta"].get("blocked_path") or result.get("blocked_path")
                observations = result.get("observations") or []
                lines = [
                    "[BLOCKED] AEF runtime diagnosis is blocked\n",
                    f"Cause     : {cause}",
                ]
                if blocked_path:
                    lines.append(f"Path      : {_escape_human_value(blocked_path)}")
                lines.extend(_doctor_context_lines(result))
                if observations:
                    lines.append(
                        f"Notes     : {_escape_human_value(', '.join(str(item) for item in observations))}"
                    )
                lines.append(f"Workspace : {workspace}")
                return "\n".join(lines) + "\n"

        if command == "RECORD":
            record_id = _escape_human_value(result.get("record_id", "unknown"))
            if status == "CHANGE":
                return (
                    "[OK] AEF recorded a declaration\n\n"
                    f"Record    : {record_id}\n"
                    f"Workspace : {workspace}\n"
                )
            if status == "NO_CHANGE":
                return (
                    "[OK] AEF record is unchanged\n\n"
                    f"Record    : {record_id}\n"
                    f"Workspace : {workspace}\n"
                    "Changes   : none\n"
                )
            if status == "BLOCKED":
                return (
                    "[BLOCKED] AEF record conflicts with an existing file\n\n"
                    f"Record    : {record_id}\n"
                    "Reason    : record conflict\n"
                    f"Workspace : {workspace}\n"
                )

        if command == "INGEST":
            records = result.get("records") or []
            cited = _escape_human_value(
                ", ".join(str(item) for item in records) if records else "none"
            )
            events_accepted = _escape_human_value(result.get("events_accepted", 0))
            projected = result.get("projected") if isinstance(result.get("projected"), dict) else {}
            signals = _escape_human_value(len(projected.get("signals") or []))
            if status == "CHANGE":
                heading = (
                    "AEF ingest plan is ready"
                    if envelope.get("dry_run")
                    else "AEF ingested declared events"
                )
                return (
                    f"[OK] {heading}\n\n"
                    f"Records   : {cited}\n"
                    f"Events    : {events_accepted}\n"
                    f"Signals   : {signals}\n"
                    f"Workspace : {workspace}\n"
                )
            if status == "NO_CHANGE":
                return (
                    "[OK] AEF ingest is unchanged\n\n"
                    f"Records   : {cited}\n"
                    f"Workspace : {workspace}\n"
                    "Changes   : none\n"
                )
            if status == "BLOCKED":
                reason = _escape_human_value(envelope["meta"].get("reason", "blocked"))
                return (
                    "[BLOCKED] AEF ingest is blocked\n\n"
                    f"Reason    : {reason}\n"
                    f"Workspace : {workspace}\n"
                )

        if command == "COMPETENCY_DECLARE":
            competency_id = _escape_human_value(result.get("competency_id", "unknown"))
            recovery = result.get("recovery_action")
            if recovery is not None:
                if status == "CHANGE":
                    heading = (
                        "AEF competency declaration recovery is ready"
                        if envelope.get("dry_run")
                        else "AEF competency declaration recovery completed"
                    )
                    action = _escape_human_value(recovery)
                    return (
                        f"[OK] {heading}\n\n"
                        f"Action    : {action}\n"
                        f"Workspace : {workspace}\n"
                    )
                if status == "NO_CHANGE":
                    return (
                        "[OK] No competency declaration recovery required\n\n"
                        f"Workspace : {workspace}\n"
                    )
            projected = result.get("projected") if isinstance(result.get("projected"), dict) else {}
            level = _escape_human_value(projected.get("level", "L1"))
            if status == "CHANGE":
                heading = (
                    "AEF competency declaration plan is ready"
                    if envelope.get("dry_run")
                    else "AEF declared competency at L1"
                )
                return (
                    f"[OK] {heading}\n\n"
                    f"Competency: {competency_id}\n"
                    f"Level     : {level}\n"
                    f"Workspace : {workspace}\n"
                )
            if status == "NO_CHANGE":
                return (
                    "[OK] AEF competency declaration is unchanged\n\n"
                    f"Competency: {competency_id}\n"
                    f"Workspace : {workspace}\n"
                    "Changes   : none\n"
                )
            if status == "BLOCKED":
                reason = _escape_human_value(envelope["meta"].get("reason", "blocked"))
                return (
                    "[BLOCKED] AEF competency declaration is blocked\n\n"
                    f"Reason    : {reason}\n"
                    f"Workspace : {workspace}\n"
                )

        marker = "FAILED" if status == "FAILED" else "ERROR"
        safe_status = _escape_human_value(status)
        return f"[{marker}] AEF command ended with status {safe_status}\n"
    except Exception:
        return _INCOMPLETE_HUMAN_RESULT


def _write_stderr(message: str) -> None:
    """Best-effort ASCII diagnostic that must never escape the CLI boundary."""
    try:
        sys.stderr.write(message.encode("ascii", "replace").decode("ascii"))
        sys.stderr.flush()
    except Exception:
        pass


def _exit_code(command: str, status: str) -> int:
    if status in {"CHANGE", "NO_CHANGE", "PASS"}:
        return 0
    if command == "AUDIT" and status == "FAIL":
        return 1
    if status == DECISION_INSTALL_REQUIRED:
        return INSTALL_REQUIRED_EXIT
    if status == "BLOCKED":
        return 4
    # Reserved for a business operation returning FAILED. Neither INIT nor
    # AUDIT currently has such a reachable result; do not synthesize one.
    if status == "FAILED":
        return 5
    return 70


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aef",
        description="Manage project-local Agent Evolution Framework state.",
        epilog="Automation should pass --json explicitly. Run 'aef COMMAND --help' for details.",
    )
    parser.add_argument(
        "--workspace", default=".", metavar="PATH",
        help="project workspace root (default: current directory)",
    )
    parser.add_argument(
        "--compact", action="store_true",
        help="emit compact JSON (implies --json; incompatible with --human)",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="force JSON output")
    output.add_argument("--human", action="store_true", help="force human-readable output")
    parser.add_argument("--verbose", action="store_true", help="emit filtered diagnostics on stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_distribution_version()}")

    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser(
        "init", help="initialize an AEF V1 workspace",
        description="Initialize the official AEF V1 profile in one project.",
    )
    init_parser.add_argument("--instance-id", metavar="ID", help="explicit stable workspace identity")
    init_parser.add_argument("--role", metavar="ROLE", help="required primary agent role")
    init_parser.add_argument("--created-at", metavar="RFC3339", help="explicit creation timestamp")
    init_parser.add_argument("--dry-run", action="store_true", help="render the exact plan without writing")
    commands.add_parser(
        "audit", help="audit an AEF workspace",
        description="Validate required persisted AEF state without modifying it.",
    )
    discover_parser = commands.add_parser(
        "discover", help="reconcile an explicit connector snapshot",
        description="Reconcile a strict-JSON connector snapshot without granting authority.",
    )
    discover_parser.add_argument("--snapshot", required=True, metavar="FILE", help="connector snapshot document")
    discover_parser.add_argument("--dry-run", action="store_true", help="render the exact plan without writing")
    consolidate_parser = commands.add_parser(
        "consolidate", help="apply explicit rule lifecycle reviews",
        description="Review existing knowledge-rule lifecycles using an explicit JSON document.",
    )
    consolidate_parser.add_argument("--reviews", required=True, metavar="FILE", help="consolidation review document")
    consolidate_parser.add_argument("--dry-run", action="store_true", help="render the exact plan without writing")
    evaluate_parser = commands.add_parser(
        "evaluate", help="review pending promotion recommendations",
        description="List recommendations, apply explicit human decisions, or recover a transaction.",
    )
    evaluation_action = evaluate_parser.add_mutually_exclusive_group()
    evaluation_action.add_argument("--list", action="store_true", dest="list_only", help="list pending recommendations without writing")
    evaluation_action.add_argument("--decisions", metavar="FILE", help="explicit human decision document")
    evaluation_action.add_argument("--refresh", action="store_true", help="refresh recommendation state")
    evaluation_action.add_argument("--recover", action="store_true", help="recover an interrupted EVALUATE transaction")
    evaluate_parser.add_argument("--dry-run", action="store_true", help="render the exact plan without writing")
    record_parser = commands.add_parser(
        "record", help="persist an explicit declared-fact recording",
        description="Validate and persist a declared-fact recording without granting authority.",
    )
    record_parser.add_argument(
        "--recording", required=True, metavar="FILE",
        help="declared-fact recording document",
    )
    record_parser.add_argument("--dry-run", action="store_true", help="render the exact plan without writing")
    integrate_parser = commands.add_parser(
        "integrate", help="manage project-local integrations",
        description=(
            "Manage project-local guidance doors. Guidance only — not permission, "
            "hooks, or host settings. Doctrinal rules live in AGENTS.md; "
            "CLAUDE.md and GEMINI.md are doorbells."
        ),
    )
    integrations = integrate_parser.add_subparsers(
        dest="integration", required=True
    )

    def _door_parser(name: str, help_text: str, description: str):
        parser = integrations.add_parser(
            name, help=help_text, description=description,
        )
        parser.add_argument(
            "--scope", default="project", metavar="project",
            help="integration scope (V1 supports project only; default: project)",
        )
        action = parser.add_mutually_exclusive_group()
        action.add_argument(
            "--status", action="store_true", dest="status_only",
            help="inspect status without writing",
        )
        action.add_argument(
            "--remove", action="store_true",
            help="remove only the managed AEF segment for this door",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="render the exact guidance change without writing",
        )
        return parser

    _door_parser(
        "agents",
        "manage the shared AGENTS.md guidance segment",
        "Install, inspect, or remove the managed AGENTS.md segment (citations only).",
    )
    _door_parser(
        "claude",
        "manage the Claude root doorbell (legacy .claude bridge recognized)",
        (
            "Install or remove the root CLAUDE.md doorbell pointing at AGENTS.md. "
            "Status also reports a brownfield .claude/CLAUDE.md bridge without rewriting it."
        ),
    )
    _door_parser(
        "gemini",
        "manage the GEMINI.md doorbell",
        "Install, inspect, or remove the managed GEMINI.md doorbell (no doctrine rules).",
    )
    _door_parser(
        "all",
        "manage AGENTS.md plus Claude and Gemini doorbells",
        (
            "Apply, inspect, or remove the shared commun and root doorbells together. "
            "Does not create or rewrite a legacy .claude/CLAUDE.md bridge."
        ),
    )
    upgrade_parser = commands.add_parser(
        "upgrade",
        help="verify or apply workspace schema evolution",
        description=(
            "Check, preview, apply, or recover workspace upgrades for the "
            "installed package. This is not a package update."
        ),
    )
    upgrade_parser.add_argument(
        "--check", action="store_true",
        help="show the upgrade plan without writing",
    )
    upgrade_parser.add_argument(
        "--recover", action="store_true",
        help="recover an interrupted UPGRADE transaction",
    )
    upgrade_parser.add_argument(
        "--dry-run", action="store_true",
        help="compute the projected result without writing",
    )
    doctor_parser = commands.add_parser(
        "doctor",
        help="diagnose the AEF Python runtime",
        description=(
            "Diagnose whether a compatible AEF runtime is executable. "
            "Read-only: does not modify .agent/, create environments, or run pip. "
            "When installation is required, the result includes a copyable command "
            "for the operator to run manually."
        ),
    )
    ingest_parser = commands.add_parser(
        "ingest",
        help="ingest declared learning events from persisted records",
        description=(
            "Cite persisted records and declare normalized learning events. "
            "Derives learning signals, observations, and candidate hypotheses only. "
            "Does not grant authority, create XP, or write records."
        ),
    )
    ingest_parser.add_argument(
        "--intake", required=True, metavar="FILE",
        help="ingest intake document citing persisted record_id values",
    )
    ingest_parser.add_argument(
        "--dry-run", action="store_true",
        help="render the exact knowledge plan without writing",
    )
    competency_parser = commands.add_parser(
        "competency",
        help="govern competency birth transitions",
        description=(
            "Declare an initial competency at L1 with human approval and cited records. "
            "Does not promote, grant authority, or write records."
        ),
    )
    competency_commands = competency_parser.add_subparsers(
        dest="competency_command", required=True,
    )
    declare_parser = competency_commands.add_parser(
        "declare",
        help="declare a competency at L1",
        description=(
            "Validate or apply a competency declaration document. "
            "Requires persisted records and an explicit human decision. "
            "Creates L1 only; never XP, Trust, or permissions."
        ),
    )
    declare_parser.add_argument(
        "--declaration", metavar="FILE",
        help="competency declaration document",
    )
    declare_parser.add_argument(
        "--recover", action="store_true",
        help="recover an interrupted competency declaration transaction",
    )
    declare_parser.add_argument(
        "--dry-run", action="store_true",
        help="render the exact competency plan without writing",
    )
    return parser


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.compact and args.human:
        parser.error("argument --compact: not allowed with argument --human")
    if args.command == "integrate" and args.status_only and args.dry_run:
        parser.error("argument --dry-run: not allowed with argument --status")
    if args.command == "upgrade" and args.check and args.recover:
        parser.error("argument --check: not allowed with argument --recover")
    if args.command == "upgrade" and args.check and args.dry_run:
        parser.error("argument --check: not allowed with argument --dry-run")
    if args.command == "competency" and args.competency_command == "declare":
        if args.recover and args.declaration:
            parser.error("argument --declaration: not allowed with argument --recover")
        if not args.recover and not args.declaration:
            parser.error("argument --declaration is required unless --recover is set")
    return args


def _output_mode(args: argparse.Namespace) -> str:
    if args.human:
        return "human"
    if args.json or args.compact:
        return "json"
    return "human" if getattr(sys.stdout, "isatty", lambda: False)() else "json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CLIInputError("invalid_timestamp", "Creation timestamp must be valid RFC 3339.")
    try:
        if "T" not in value:
            raise ValueError
        parsed = value[:-1] + "+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(parsed)
        if timestamp.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise CLIInputError(
            "invalid_timestamp", "Creation timestamp must be valid RFC 3339."
        ) from exc
    return value


def _validate_json_documents(workspace: Path) -> None:
    agent_dir = workspace / ".agent"
    if not agent_dir.exists():
        return
    skip = {
        ".agent/state/upgrade-transaction.json",
    }
    for path in sorted(agent_dir.rglob("*.json")):
        if path.is_file():
            relative = path.relative_to(workspace).as_posix()
            if relative in skip:
                continue
            json.loads(path.read_text(encoding="utf-8"))


def _existing_role(current: dict[str, Any]) -> str | None:
    for decision in current.get("decisions", {}).get("decisions", []):
        if decision.get("id") == ROLE_DECISION and decision.get("status") == "resolved":
            value = decision.get("value")
            return value if isinstance(value, str) else None
    return None


def _prompt_role() -> str | None:
    if not sys.stdin.isatty():
        return None
    _write_stderr("Primary role [generalist-agent]: ")
    response = sys.stdin.readline()
    if response == "":
        return None
    return response.strip() or "generalist-agent"


def _init_result(state: dict[str, Any], instance_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    manifest = state.get("files", {}).get(".agent/manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    return {
        "instance_id": manifest.get("instance_id", instance_id),
        "framework_version": manifest.get("framework_version", "1.0.0"),
        "schema_version": manifest.get("schema_version", "1.0.0"),
        "role": _existing_role(state),
        "unresolved_decisions": meta.get("unresolved_decisions", []),
    }


def _run_init(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    _validate_json_documents(workspace)
    current = load_workspace(workspace)
    decisions_path = ".agent/state/decisions.json"
    if decisions_path in current.get("files", {}):
        validate_decisions_document(current["files"][decisions_path])
    manifest = current.get("files", {}).get(".agent/manifest.json")
    manifest = manifest if isinstance(manifest, dict) else {}

    if args.instance_id is not None and not args.instance_id.strip():
        raise CLIInputError("invalid_instance_id", "Instance ID must be a non-empty string.")

    if args.dry_run and not manifest:
        missing = [
            name
            for name, value in (("instance_id", args.instance_id), ("created_at", args.created_at))
            if value is None or (name == "created_at" and not value.strip())
        ]
        if missing:
            raise CLIInputError(
                "dry_run_requires_stable_inputs",
                (
                    "INIT dry-run requires --instance-id and --created-at. "
                    "Reuse the same values when applying the initialization."
                ),
                {
                    "missing": missing,
                    "required_options": ["--instance-id", "--created-at"],
                    "reuse_for_apply": True,
                },
            )

    if args.created_at is not None and not args.created_at.strip():
        raise CLIInputError("invalid_timestamp", "Creation timestamp must be valid RFC 3339.")

    instance_id = (
        args.instance_id if args.instance_id is not None
        else manifest.get("instance_id") or str(uuid.uuid4())
    )
    created_at = _validate_timestamp(
        args.created_at if args.created_at is not None
        else manifest.get("created_at") or _utc_now()
    )
    role = args.role
    if role is None and _existing_role(current) is None:
        role = _prompt_role()
    answers = {ROLE_DECISION: role} if role is not None else {}

    status, desired, meta = init_project(
        current,
        instance_id=instance_id,
        answers=answers,
        created_at=created_at,
        profile="aef-v1",
    )
    diff = plan_workspace(current, desired)
    if not args.dry_run and status in {"CHANGE", "NO_CHANGE"}:
        diff = apply_workspace(workspace, current, desired)
    envelope = _envelope(
        command="INIT",
        workspace=workspace,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE"},
        dry_run=args.dry_run,
        result=_init_result(desired, instance_id, meta),
        meta=meta,
        diff=diff,
    )
    return envelope, _exit_code("INIT", status)


def _run_audit(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    _validate_json_documents(workspace)
    result = audit_project(load_workspace(workspace), root=workspace)
    status = result["status"]
    envelope = _envelope(
        command="AUDIT",
        workspace=workspace,
        status=status,
        ok=status == "PASS",
        dry_run=False,
        result={"schema_version": result["schema_version"], "findings": result["findings"]},
        meta={},
        diff=None,
    )
    return envelope, _exit_code("AUDIT", status)


def _load_recording(path: str | Path) -> Any:
    try:
        return _load_snapshot(path)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CLIInputError(
            "invalid_json",
            "The recording document is not valid JSON.",
        ) from exc


def _run_record(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    document = _load_recording(args.recording)
    try:
        persisted = build_persisted_record(document)
    except InvalidRecordSubmissionError as exc:
        raise CLIInputError(exc.code, str(exc)) from exc
    try:
        status, relative, digest = persist_record(
            workspace, persisted, dry_run=args.dry_run
        )
    except InvalidRecordStoreError as exc:
        raise CLIInputError(exc.code, str(exc)) from exc
    result = {
        "record_id": persisted["record_id"],
        "path": relative,
        "digest": digest,
    }
    if status == "CHANGE":
        diff: dict[str, list[str]] | None = {
            "created": [relative], "modified": [], "removed": [],
        }
        meta: dict[str, Any] = {}
    elif status == "NO_CHANGE":
        diff = {"created": [], "modified": [], "removed": []}
        meta = {}
    else:
        diff = None
        meta = {"reason": "record_conflict"}
    envelope = _envelope(
        command="RECORD",
        workspace=workspace,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE"},
        dry_run=args.dry_run,
        result=result,
        meta=meta,
        diff=diff,
    )
    return envelope, _exit_code("RECORD", status)


def _load_intake(path: str | Path) -> Any:
    try:
        return _load_snapshot(path)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CLIInputError(
            "invalid_json",
            "The ingest document is not valid JSON.",
        ) from exc


def _load_declaration(path: str | Path) -> Any:
    try:
        return _load_snapshot(path)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CLIInputError(
            "invalid_json",
            "The competency declaration document is not valid JSON.",
        ) from exc


def _run_ingest(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    document = _load_intake(args.intake)
    try:
        status, result, meta, diff = plan_ingest(
            workspace, document, dry_run=args.dry_run
        )
    except IngestBlockedError as exc:
        envelope = _envelope(
            command="INGEST",
            workspace=workspace,
            status="BLOCKED",
            ok=False,
            dry_run=args.dry_run,
            result={},
            meta={"reason": exc.code, **({"details": exc.details} if exc.details else {})},
            diff=None,
        )
        return envelope, _exit_code("INGEST", "BLOCKED")
    except WorkspaceContentionError as exc:
        envelope = _envelope(
            command="INGEST",
            workspace=workspace,
            status="BLOCKED",
            ok=False,
            dry_run=args.dry_run,
            result={},
            meta={"reason": exc.code},
            diff=None,
        )
        return envelope, _exit_code("INGEST", "BLOCKED")
    envelope = _envelope(
        command="INGEST",
        workspace=workspace,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE"},
        dry_run=args.dry_run,
        result=result,
        meta=meta,
        diff=diff,
    )
    return envelope, _exit_code("INGEST", status)


def _run_competency_declare(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    try:
        if args.recover:
            status, result, meta, diff = recover_declaration(
                workspace, dry_run=args.dry_run,
            )
        else:
            document = _load_declaration(args.declaration)
            status, result, meta, diff = plan_declare(
                workspace, document, dry_run=args.dry_run,
            )
    except CompetencyDeclarationBlockedError as exc:
        envelope = _envelope(
            command="COMPETENCY_DECLARE",
            workspace=workspace,
            status="BLOCKED",
            ok=False,
            dry_run=args.dry_run,
            result={},
            meta={"reason": exc.code, **({"details": exc.details} if exc.details else {})},
            diff=None,
        )
        return envelope, _exit_code("COMPETENCY_DECLARE", "BLOCKED")
    envelope = _envelope(
        command="COMPETENCY_DECLARE",
        workspace=workspace,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE"},
        dry_run=args.dry_run,
        result=result,
        meta=meta,
        diff=diff,
    )
    return envelope, _exit_code("COMPETENCY_DECLARE", status)


def _load_snapshot(path: str | Path) -> Any:
    raw = Path(path).read_text(encoding="utf-8")

    def reject_constant(value):
        raise json.JSONDecodeError(f"invalid JSON constant: {value}", raw, 0)

    try:
        return json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except DuplicateJSONKeyError as exc:
        raise CLIInputError(
            "duplicate_json_key",
            f"Duplicate JSON key {exc.key!r} is not allowed.",
            details={"key": exc.key},
        ) from exc


def _read_interactive_value(prompt: str) -> str | None:
    _write_stderr(prompt)
    value = sys.stdin.readline()
    if value == "":
        return None
    value = value.strip()
    return value or None


def _interactive_decision_id(recommendation_id, decision, actor, timestamp):
    payload = json.dumps(
        [recommendation_id, decision, actor, timestamp],
        ensure_ascii=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"evaluation:interactive:sha256-{digest}"


def _collect_interactive_decisions(recommendations):
    decisions = []
    total = len(recommendations)
    for index, item in enumerate(recommendations, start=1):
        evidence = item.get("current_evidence") or {}
        scope = "career" if item.get("scope") == "career" else "competency"
        competency = (
            "" if scope == "career"
            else f"\nCompetency    : {_escape_human_value(item.get('competency_id', 'unknown'))}"
        )
        reasons = ", ".join(item.get("readiness", {}).get("reasons", [])) or "none"
        summary = (
            f"\nPromotion recommendation {index} of {total}\n\n"
            f"Scope         : {scope}{competency}\n"
            f"Current level : {_escape_human_value(item.get('current_level', 'unknown'))}\n"
            f"Proposed level: {_escape_human_value(item.get('to_level', 'unknown'))}\n"
            f"XP            : {_escape_human_value(evidence.get('xp', 'unknown'))}\n"
            f"Cases         : {_escape_human_value(evidence.get('cases', 'unknown'))}\n"
            f"Trust         : {_escape_human_value(evidence.get('trust', 'unknown'))}\n"
            f"Complex cases : {_escape_human_value(evidence.get('complex_cases', 'unknown'))}\n"
            f"Recent errors : {_escape_human_value(evidence.get('recent_significant_errors', 'unknown'))}\n"
            f"Probation     : {_escape_human_value(item.get('probation', 'unknown'))}\n"
            f"Readiness     : {_escape_human_value(reasons)}\n\n"
        )
        _write_stderr(summary)
        choice = _read_interactive_value("Approve / Reject / Later [a/r/l]: ")
        if choice is None or choice.lower() not in {"a", "approve", "r", "reject"}:
            continue
        decision = "approve" if choice.lower() in {"a", "approve"} else "reject"
        actor = _read_interactive_value("Actor: ")
        if actor is None:
            continue
        reason = _read_interactive_value("Reason: ")
        if reason is None:
            continue
        confirmation = _read_interactive_value(
            f"Confirm {decision} [y/N]: "
        )
        if confirmation is None or confirmation.lower() not in {"y", "yes"}:
            continue
        timestamp = _utc_now()
        entry = {
            "id": _interactive_decision_id(
                item["id"], decision, actor, timestamp
            ),
            "recommendation_id": item["id"],
            "decision": decision,
            "reason": reason,
            "expected_evidence_digest": item["evidence_digest"],
        }
        if decision == "approve":
            entry["expected_current_evidence_digest"] = item[
                "current_evidence_digest"
            ]
            entry["approval"] = {
                "approved": True, "source": "human", "actor": actor,
                "approved_at": timestamp,
            }
        else:
            entry["rejection"] = {
                "rejected": True, "source": "human", "actor": actor,
                "rejected_at": timestamp,
            }
        decisions.append(entry)
    return {"protocol": "aef.evaluate/v1", "decisions": decisions}


def _run_discover(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    _validate_json_documents(workspace)
    current = load_workspace(workspace)
    snapshot = _load_snapshot(args.snapshot)
    status, desired, meta = discover_project(current, snapshot)
    diff = plan_workspace(current, desired)
    if not args.dry_run and status in {"CHANGE", "NO_CHANGE"}:
        diff = apply_workspace(workspace, current, desired)
    result = {
        "registry_path": meta.get("registry_path", ".agent/integrations/registry.json"),
        "connectors": meta.get("connector_count", 0),
        "available_connectors": meta.get("available_connector_count", 0),
        "capabilities": meta.get("capability_count", 0),
        "authority_granted": False,
    }
    envelope = _envelope(
        command="DISCOVER",
        workspace=workspace,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE"},
        dry_run=args.dry_run,
        result=result,
        meta=meta,
        diff=diff,
    )
    return envelope, _exit_code("DISCOVER", status)


def _run_consolidate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    _validate_json_documents(workspace)
    current = load_workspace(workspace)
    reviews = _load_snapshot(args.reviews)
    status, desired, meta = consolidate_project(current, reviews)
    diff = plan_workspace(current, desired)
    if not args.dry_run and status in {"CHANGE", "NO_CHANGE"}:
        diff = apply_workspace(workspace, current, desired)
    result = {
        "knowledge_path": ".agent/knowledge/knowledge.json",
        "reviews": meta.get("review_count", len(reviews.get("reviews", [])) if isinstance(reviews, dict) else 0),
        "decisions": meta.get("decisions", []),
        "rules_changed": meta.get("changed_rule_ids", []),
        "authority_granted": False,
    }
    envelope = _envelope(
        command="CONSOLIDATE", workspace=workspace, status=status,
        ok=status in {"CHANGE", "NO_CHANGE"}, dry_run=args.dry_run,
        result=result, meta=meta, diff=diff,
    )
    return envelope, _exit_code("CONSOLIDATE", status)


def _evaluation_result(meta: dict[str, Any]) -> dict[str, Any]:
    decisions = meta.get("decisions", [])
    return {
        "recommendations": meta.get("recommendations"),
        "pending_recommendations": meta.get(
            "pending_recommendations", len(meta.get("recommendations", []))
        ),
        "decisions_processed": len(decisions),
        "approved": [
            item["recommendation_id"] for item in decisions
            if item.get("decision") == "APPROVE"
        ],
        "rejected": [
            item["recommendation_id"] for item in decisions
            if item.get("decision") == "REJECT"
        ],
        "levels_changed": meta.get("levels_changed", []),
        "refreshed": meta.get("refreshed", []),
        "recovery_action": meta.get("action") if meta.get("action") != "none" else None,
    }


def _run_evaluate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    _validate_json_documents(workspace)
    current = load_workspace(workspace)
    if args.list_only:
        if args.dry_run:
            raise CLIInputError("invalid_input", "Evaluation listing does not use dry-run.")
        status, desired, meta = list_project_recommendations(current)
        diff = {"created": [], "modified": [], "removed": []}
    elif args.recover:
        status, desired, meta = recover_evaluation_transaction(
            workspace, current, dry_run=args.dry_run
        )
        diff = plan_workspace(current, desired)
    elif args.refresh:
        status, desired, meta = refresh_project_recommendations(current)
        diff = plan_workspace(current, desired)
        if not args.dry_run and status in {"CHANGE", "NO_CHANGE"}:
            diff = apply_workspace(workspace, current, desired)
    elif args.decisions:
        decisions = _load_snapshot(args.decisions)
        status, desired, meta = evaluate_project(current, decisions)
        diff = plan_workspace(current, desired)
        if status in {"CHANGE", "NO_CHANGE"}:
            diff, _ = apply_evaluation_transaction(
                workspace, current, desired, decisions, dry_run=args.dry_run
            )
    else:
        if (
            args.dry_run
            or _output_mode(args) != "human"
            or not sys.stdin.isatty()
            or not sys.stdout.isatty()
        ):
            raise CLIInputError(
                "interactive_input_required",
                "Interactive evaluation requires a terminal.",
            )
        list_status, _, list_meta = list_project_recommendations(current)
        if list_status == "BLOCKED":
            status, desired, meta = list_status, current, list_meta
            diff = {"created": [], "modified": [], "removed": []}
        else:
            decisions = _collect_interactive_decisions(list_meta["recommendations"])
            status, desired, meta = evaluate_project(current, decisions)
            diff = plan_workspace(current, desired)
            if status in {"CHANGE", "NO_CHANGE"}:
                diff, _ = apply_evaluation_transaction(
                    workspace, current, desired, decisions, dry_run=False
                )
    result = _evaluation_result(meta)
    envelope = _envelope(
        command="EVALUATE", workspace=workspace, status=status,
        ok=status in {"CHANGE", "NO_CHANGE"}, dry_run=args.dry_run,
        result=result, meta=meta, diff=diff,
    )
    return envelope, _exit_code("EVALUATE", status)


def _claude_settings_warnings(workspace: Path) -> list[str]:
    warnings = []
    for name in ("settings.json", "settings.local.json"):
        path = workspace / ".claude" / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            warnings.append(f"unmanaged_{name.replace('.', '_')}_invalid")
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            warnings.append(f"unmanaged_{name.replace('.', '_')}_invalid")
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            json.loads(
                raw,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError("non-standard JSON constant")
                ),
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            warnings.append(f"unmanaged_{name.replace('.', '_')}_invalid")
    return warnings


def _run_integrate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.integration not in {"agents", "claude", "gemini", "all"}:
        raise CLIInputError("unsupported_integration", "The integration is unsupported.")
    if args.scope != "project":
        raise CLIInputError(
            "unsupported_integration_scope",
            "Only project-scoped guidance integration is supported.",
            {"scope": args.scope},
        )
    workspace = Path(args.workspace).resolve()
    _validate_json_documents(workspace)
    current = load_workspace(workspace)
    doctrine_error = None
    if validate_claude_integration_workspace(current) is None:
        try:
            validate_claude_doctrine_files(workspace)
        except ClaudeIntegrationFilesystemError:
            doctrine_error = "missing_aef_doctrine"

    doors = (
        ["agents", "claude", "gemini"] if args.integration == "all"
        else [args.integration]
    )
    if args.remove and args.integration == "all":
        doors = ["gemini", "claude", "agents"]

    aggregate_status = "NO_CHANGE"
    aggregate_diff = {"created": [], "modified": [], "removed": []}
    door_results: dict[str, Any] = {}
    last_meta: dict[str, Any] = {}
    primary_installed = False

    def _merge_diff(diff: dict[str, list[str]]) -> None:
        for key in ("created", "modified", "removed"):
            for path in diff.get(key, []):
                if path not in aggregate_diff[key]:
                    aggregate_diff[key].append(path)

    def _apply_one(door: str) -> tuple[str, dict[str, Any]]:
        nonlocal aggregate_status, primary_installed, last_meta
        try:
            if door == "claude":
                root_existing = read_guidance_file(workspace, door_path("claude"))
                legacy_existing = read_claude_bridge(workspace)
                status, _, meta = plan_claude_door(
                    current, root_existing, legacy_existing,
                    remove=args.remove, status_only=args.status_only,
                )
            else:
                existing = read_guidance_file(workspace, door_path(door))
                status, _, meta = plan_door_integration(
                    current, existing, door=door,
                    remove=args.remove, status_only=args.status_only,
                )
        except (GuidanceFilesystemError, ClaudeIntegrationFilesystemError) as exc:
            raise CLIInputError(
                "invalid_guidance_file",
                "A guidance instruction file is invalid.",
            ) from exc

        if doctrine_error is not None and not args.status_only:
            status = "BLOCKED"
            meta = {
                **meta, "reason": doctrine_error, "doctrine_files": 0,
                "bridge_healthy": False, "workspace_compatible": False,
            }
        elif doctrine_error is not None and args.status_only:
            meta = {
                **meta, "reason": doctrine_error, "doctrine_files": 0,
                "bridge_healthy": False, "workspace_compatible": False,
            }
            status = "BLOCKED"

        desired_bytes = meta.get("desired_bytes")
        relative = meta.get("bridge_path") or door_path(door)
        existing_for_diff: bytes | None
        if door == "claude" and meta.get("target") == "legacy_bridge":
            existing_for_diff = read_claude_bridge(workspace)
            relative = CLAUDE_BRIDGE_PATH
            diff = (
                claude_bridge_diff(existing_for_diff, desired_bytes)
                if status == "CHANGE" else {"created": [], "modified": [], "removed": []}
            )
        else:
            existing_for_diff = read_guidance_file(workspace, relative)
            diff = (
                guidance_diff(relative, existing_for_diff, desired_bytes)
                if status == "CHANGE" and desired_bytes is not None
                else {"created": [], "modified": [], "removed": []}
            )

        if status == "CHANGE" and not args.dry_run and not args.status_only:
            if door == "claude" and meta.get("target") == "legacy_bridge":
                diff = apply_claude_bridge(workspace, existing_for_diff, desired_bytes)
            else:
                diff = apply_guidance_file(
                    workspace, relative, existing_for_diff, desired_bytes,
                )

        if status == "CHANGE":
            aggregate_status = "CHANGE"
            _merge_diff(diff)
        elif status == "BLOCKED" and aggregate_status != "CHANGE":
            aggregate_status = "BLOCKED"

        installed = meta.get("bridge", {}).get("state") == "installed"
        if door == "claude" and meta.get("legacy_bridge", {}).get("state") == "installed":
            installed = installed or True
        if args.remove and status == "CHANGE":
            installed = False
        elif not args.remove and status == "CHANGE":
            installed = True
        if installed:
            primary_installed = True

        door_results[door] = {
            "status": status,
            "path": relative,
            "bridge": meta.get("bridge"),
            "legacy_bridge": meta.get("legacy_bridge"),
            "reason": meta.get("reason"),
            "installed": installed,
        }
        last_meta = meta
        return status, meta

    # Co-install AGENTS.md before doorbells on install (not status/remove).
    if (
        not args.status_only
        and not args.remove
        and args.integration in {"claude", "gemini", "all"}
    ):
        agents_existing = read_guidance_file(workspace, AGENTS_PATH)
        agents_status, _, agents_meta = plan_door_integration(
            current, agents_existing, door="agents",
        )
        if doctrine_error is None and agents_status == "CHANGE":
            agents_diff = guidance_diff(
                AGENTS_PATH, agents_existing, agents_meta["desired_bytes"],
            )
            if not args.dry_run:
                apply_guidance_file(
                    workspace, AGENTS_PATH, agents_existing, agents_meta["desired_bytes"],
                )
            aggregate_status = "CHANGE"
            _merge_diff(agents_diff)
            door_results["agents"] = {
                "status": "CHANGE",
                "path": AGENTS_PATH,
                "bridge": agents_meta.get("bridge"),
                "installed": True,
                "co_installed": True,
            }

    for door in doors:
        if (
            not args.remove
            and not args.status_only
            and door == "agents"
            and door_results.get("agents", {}).get("co_installed")
        ):
            continue
        _apply_one(door)

    warnings = _claude_settings_warnings(workspace) if args.status_only else []
    audit = audit_project(current, root=workspace) if args.status_only else None
    pending = None
    if args.status_only:
        try:
            _, _, pending_meta = list_project_recommendations(current)
            pending = len(pending_meta.get("recommendations", []))
        except ValueError:
            warnings.append("aef_evaluation_status_unavailable")

    if aggregate_status == "BLOCKED" and last_meta.get("reason") is None and doctrine_error:
        last_meta["reason"] = doctrine_error

    result = {
        "scope": "project",
        "integration": args.integration,
        "action": "remove" if args.remove else "install",
        "status_only": args.status_only,
        "installed": primary_installed if args.status_only or not args.remove else (
            False if args.remove and aggregate_status == "CHANGE" else primary_installed
        ),
        "bridge_healthy": last_meta.get(
            "bridge_healthy", aggregate_status != "BLOCKED"
        ),
        "workspace_compatible": last_meta.get(
            "workspace_compatible", aggregate_status != "BLOCKED"
        ),
        "doctrine_files": last_meta.get("doctrine_files", 0),
        "enforcement": "guidance_only",
        "doors": door_results,
        "audit": audit.get("status", "unknown").lower() if audit else None,
        "pending_reviews": pending,
        "warnings": warnings,
    }
    meta_out = {
        key: value for key, value in last_meta.items()
        if key != "desired_bytes"
    }
    envelope = _envelope(
        command="INTEGRATE", workspace=workspace, status=aggregate_status,
        ok=aggregate_status in {"CHANGE", "NO_CHANGE"}, dry_run=args.dry_run,
        result=result, meta=meta_out, diff=aggregate_diff,
    )
    return envelope, _exit_code("INTEGRATE", aggregate_status)


def _run_command(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "init":
        return _run_init(args)
    if args.command == "audit":
        return _run_audit(args)
    if args.command == "discover":
        return _run_discover(args)
    if args.command == "consolidate":
        return _run_consolidate(args)
    if args.command == "integrate":
        return _run_integrate(args)
    if args.command == "record":
        return _run_record(args)
    if args.command == "upgrade":
        return _run_upgrade(args)
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "competency":
        return _run_competency_declare(args)
    return _run_evaluate(args)


def _doctor_status_from_decision(decision: str) -> tuple[str, bool]:
    if decision == "OK":
        return "PASS", True
    if decision == DECISION_INSTALL_REQUIRED:
        return DECISION_INSTALL_REQUIRED, False
    return "BLOCKED", False


def _run_doctor(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    result = diagnose_runtime(workspace)
    decision = result["decision"]
    status, ok = _doctor_status_from_decision(decision)
    meta: dict[str, Any] = {}
    if result.get("blocked_cause"):
        meta["blocked_cause"] = result["blocked_cause"]
        if result.get("blocked_path"):
            meta["blocked_path"] = result["blocked_path"]
    envelope = _envelope(
        command="DOCTOR",
        workspace=workspace,
        status=status,
        ok=ok,
        dry_run=False,
        result=result,
        meta=meta,
        diff=None,
    )
    return envelope, _exit_code("DOCTOR", status)


def _run_upgrade(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    workspace = Path(args.workspace).resolve()
    if args.recover and args.dry_run:
        mode = "recover_dry_run"
    elif args.recover:
        mode = "recover"
    elif args.check:
        mode = "check"
    elif args.dry_run:
        mode = "dry_run"
    else:
        mode = "apply"
    status, result, extra = run_upgrade(workspace, mode=mode)
    diff = extra.get("diff")
    meta = extra.get("meta") or {}
    envelope = _envelope(
        command="UPGRADE",
        workspace=workspace,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE"},
        dry_run=bool(args.dry_run or args.check),
        result=result,
        meta=meta,
        diff=diff,
    )
    return envelope, _exit_code("UPGRADE", status)


def _error_envelope(args: argparse.Namespace, exc: Exception) -> tuple[dict[str, Any], int]:
    if isinstance(exc, CLIInputError):
        code = exc.code
        message = exc.public_message
        details = exc.details
        exit_code = 3
    elif isinstance(exc, InvalidRecordSubmissionError):
        code = exc.code
        message = str(exc)
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidRecordStoreError):
        code = exc.code
        message = str(exc)
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidIngestSubmissionError):
        code = exc.code
        message = str(exc)
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidCompetencyDeclarationError):
        code = exc.code
        message = str(exc)
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidCompetencyDeclarationTransactionError):
        code = "invalid_competency_declaration_transaction"
        message = "The competency declaration transaction is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, CompetencyDeclarationBlockedError):
        envelope = _envelope(
            command="COMPETENCY_DECLARE",
            workspace=args.workspace,
            status="BLOCKED",
            ok=False,
            dry_run=bool(getattr(args, "dry_run", False)),
            result={},
            meta={"reason": exc.code, **({"details": exc.details} if exc.details else {})},
            diff=None,
        )
        return envelope, 4
    elif isinstance(exc, IngestBlockedError):
        envelope = _envelope(
            command=str(getattr(args, "command", "ingest")).upper(),
            workspace=args.workspace,
            status="BLOCKED",
            ok=False,
            dry_run=bool(getattr(args, "dry_run", False)),
            result={},
            meta={"reason": exc.code},
            diff=None,
        )
        return envelope, 4
    elif isinstance(exc, WorkspaceContentionError):
        envelope = _envelope(
            command=str(getattr(args, "command", "ingest")).upper(),
            workspace=args.workspace,
            status="BLOCKED",
            ok=False,
            dry_run=bool(getattr(args, "dry_run", False)),
            result={},
            meta={"reason": exc.code},
            diff=None,
        )
        return envelope, 4
    elif isinstance(exc, CompetencyDeclarationRecoveryRequiredError):
        envelope = _envelope(
            command="COMPETENCY_DECLARE" if getattr(args, "command", "") == "competency"
            else str(getattr(args, "command", "unknown")).upper(),
            workspace=args.workspace,
            status="BLOCKED",
            ok=False,
            dry_run=bool(getattr(args, "dry_run", False)),
            result={},
            meta={"reason": "competency_declaration_recovery_required"},
            diff=None,
        )
        return envelope, 4
    elif isinstance(exc, InvalidDecisionsDocumentError):
        code = "invalid_decisions_document"
        message = "The decisions document is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidDiscoveryRegistryError):
        code = "invalid_discovery_registry"
        message = "The persisted connector registry is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidDiscoverySnapshotError):
        code = "invalid_discovery_snapshot"
        message = "The connector discovery snapshot is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidConsolidationInputError):
        code = "invalid_consolidation_input"
        message = "The consolidation review document is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidKnowledgeStateError):
        code = "invalid_knowledge_state"
        message = "The persisted knowledge state is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidEvaluationDecisionsError):
        code = "invalid_evaluation_decisions"
        message = "The evaluation decisions document is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidPromotionRecommendationStateError):
        code = "invalid_evaluation_state"
        message = "The persisted evaluation state is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidCareerStateError):
        code = "invalid_career_state"
        message = "The persisted career state is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidCompetencyStateError):
        code = "invalid_competency_state"
        message = "The persisted competency state is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, InvalidEvaluationTransactionError):
        code = "invalid_evaluation_transaction"
        message = "The persisted evaluation transaction is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, EvaluationRecoveryRequiredError):
        code = "evaluation_recovery_required"
        message = "Evaluation recovery is required before workspace mutation."
        details = {}
        exit_code = 4
    elif isinstance(exc, UpgradeRecoveryRequiredError):
        envelope = _envelope(
            command=args.command.upper(),
            workspace=args.workspace,
            status="BLOCKED",
            ok=False,
            dry_run=bool(getattr(args, "dry_run", False)),
            result={},
            meta={"reason": "upgrade_recovery_required"},
            diff=None,
        )
        return envelope, 4
    elif isinstance(exc, MigrationFailure):
        envelope = _envelope(
            command=args.command.upper(),
            workspace=args.workspace,
            status="FAILED",
            ok=False,
            dry_run=bool(getattr(args, "dry_run", False)),
            result={},
            meta={"reason": "migration_failed", "migration_id": exc.migration_id},
            diff=None,
        )
        return envelope, 5
    elif isinstance(exc, ClaudeIntegrationFilesystemError):
        code = "claude_integration_filesystem_error"
        message = "The Claude project integration could not be written safely."
        details = {}
        exit_code = 6
    elif isinstance(exc, GuidanceFilesystemError):
        code = "guidance_filesystem_error"
        message = "The guidance integration could not be written safely."
        details = {}
        exit_code = 6
    elif isinstance(exc, (json.JSONDecodeError, UnicodeError)):
        code = "invalid_json"
        message = "A JSON document is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, ValueError):
        code = "invalid_input"
        message = "The command input or configuration is invalid."
        details = {}
        exit_code = 3
    elif isinstance(exc, OSError):
        code = "filesystem_error"
        message = "The workspace could not be accessed."
        details = {}
        exit_code = 6
    else:
        code = "internal_error"
        message = "An unexpected internal error occurred."
        details = {}
        exit_code = 70
    command_name = args.command.upper()
    if args.command == "competency":
        command_name = "COMPETENCY_DECLARE"
    envelope = _envelope(
        command=command_name,
        workspace=args.workspace,
        status="ERROR",
        ok=False,
        dry_run=bool(getattr(args, "dry_run", False)),
        result={},
        meta={},
        diff=None,
        error={"code": code, "message": message, "details": details},
    )
    return envelope, exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        envelope, exit_code = _run_command(args)
        if args.verbose:
            if isinstance(envelope, dict):
                command = _escape_human_value(envelope.get("command", "unknown"))
                status = _escape_human_value(envelope.get("status", "unknown"))
                _write_stderr(f"aef: {command} {status}\n")
            else:
                _write_stderr("aef: invalid internal result\n")
    except Exception as exc:
        envelope, exit_code = _error_envelope(args, exc)
        error = envelope.get("error")
        if isinstance(error, dict):
            if args.verbose:
                _write_stderr(
                    f"aef: {error['code']} ({type(exc).__name__}): {error['message']}\n"
                )
            else:
                _write_stderr(f"aef: {error['code']}: {error['message']}\n")
        elif envelope.get("status") == "BLOCKED":
            reason = envelope.get("meta", {}).get("reason", "blocked")
            _write_stderr(f"aef: {reason}\n")
    try:
        if _output_mode(args) == "human":
            rendered = _render_human(envelope)
            if rendered == _INCOMPLETE_HUMAN_RESULT:
                exit_code = 70
            _write_stdout(rendered)
        else:
            _write_envelope(envelope, compact=args.compact)
    except (BrokenPipeError, UnicodeError, OSError):
        _write_stderr("aef: output_error: stdout is unavailable.\n")
        return 6
    except Exception:
        _write_stderr("aef: internal_output_error.\n")
        return 70
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
