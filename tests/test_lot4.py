"""Lot 4 — recoverable declaration writes, mutual guards, honest audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aef import filesystem
from aef.competency_declaration import (
    COMPETENCIES_PATH,
    LEDGER_PATH,
    CompetencyDeclarationBlockedError,
)
from aef.competency_declaration_ops import (
    audit_declaration_provenance,
    plan_declare,
    recover_declaration,
)
from aef.competency_declaration_transaction import TRANSACTION_PATH, build_declaration_transaction
from aef.filesystem import (
    EVALUATION_TRANSACTION_PATH,
    UPGRADE_TRANSACTION_PATH,
    CompetencyDeclarationRecoveryRequiredError,
    _apply_workspace_unchecked,
    load_workspace,
)
from aef.identifiers import InvalidCompetencyIdentifierError, validate_competency_id
from aef.record_store import persist_record
from aef.upgrade_ops import run_upgrade
from aef.upgrade_plan import MigrationSpec
from tests.test_competency_declaration_recover import (
    declaration_for,
    init_and_record,
    invoke,
)
from tests.test_upgrade_plan import _identity


def _plant_prepared_declaration_journal(tmp_path, capsys):
    persisted = init_and_record(tmp_path, capsys)
    current = load_workspace(tmp_path)
    desired = json.loads(json.dumps(current))
    desired["files"][COMPETENCIES_PATH] = {
        "dry-run-review": {
            "id": "dry-run-review",
            "title": "Dry-run review",
            "level": "L1",
            "xp": 0,
            "cases": 0,
            "trust": None,
            "complex_cases": 0,
            "recent_significant_errors": 0,
            "probation": False,
            "source": "declared",
        }
    }
    desired["files"][LEDGER_PATH] = {
        "protocol": "aef.competency-declarations/v1",
        "events": [{
            "event_id": "competency-declaration:deadbeef",
            "competency_id": "dry-run-review",
            "declared_at": "2026-08-21T10:00:00Z",
            "decision": declaration_for(persisted)["decision"],
            "records": declaration_for(persisted)["records"],
            "title": "Dry-run review",
            "scope": "s",
            "limits": "l",
            "rationale": "r",
            "declaration_digest": "sha256:" + ("d" * 64),
        }],
    }
    journal = build_declaration_transaction(
        current, desired, "sha256:" + ("d" * 64),
    )
    prepared = json.loads(json.dumps(current))
    prepared["files"][TRANSACTION_PATH] = journal
    _apply_workspace_unchecked(tmp_path, current, prepared, allow_delete=False)
    return persisted, desired, journal


def test_crash_between_business_writes_is_recoverable(tmp_path, capsys, monkeypatch):
    persisted = init_and_record(tmp_path, capsys)
    original = filesystem._atomic_write
    business = {COMPETENCIES_PATH, LEDGER_PATH}
    written: list[str] = []

    def crash_after_first_business(root, rel_path, target, content):
        original(root, rel_path, target, content)
        if rel_path in business:
            written.append(rel_path)
            if len(written) >= 1:
                raise OSError("simulated crash after first business file")

    monkeypatch.setattr(filesystem, "_atomic_write", crash_after_first_business)
    with pytest.raises(OSError, match="simulated crash"):
        plan_declare(tmp_path, declaration_for(persisted), dry_run=False)
    assert written
    assert (tmp_path / Path(*TRANSACTION_PATH.split("/"))).is_file()

    monkeypatch.setattr(filesystem, "_atomic_write", original)
    status, result, _, diff = recover_declaration(tmp_path, dry_run=True)
    assert status == "CHANGE"
    assert result["recovery_action"] == "rollback"
    assert TRANSACTION_PATH in (diff["removed"] + diff["modified"] + diff["created"])
    assert diff != {"created": [], "modified": [], "removed": []}

    status, result, _, _ = recover_declaration(tmp_path, dry_run=False)
    assert status == "CHANGE"
    assert result["recovery_action"] == "rollback"
    assert not (tmp_path / Path(*TRANSACTION_PATH.split("/"))).exists()
    competencies = json.loads(
        (tmp_path / ".agent" / "state" / "competencies.json").read_text(encoding="utf-8")
    )
    assert "dry-run-review" not in competencies

    status, _, _, _ = plan_declare(tmp_path, declaration_for(persisted), dry_run=False)
    assert status == "CHANGE"
    competencies = json.loads(
        (tmp_path / ".agent" / "state" / "competencies.json").read_text(encoding="utf-8")
    )
    assert competencies["dry-run-review"]["source"] == "declared"


def test_upgrade_apply_blocks_on_open_declaration_journal(tmp_path, capsys, monkeypatch):
    _plant_prepared_declaration_journal(tmp_path, capsys)
    monkeypatch.setattr("aef.upgrade_ops.TARGET_WORKSPACE_SCHEMA_VERSION", "1.1.0")
    monkeypatch.setattr("aef.upgrade_compat.TARGET_WORKSPACE_SCHEMA_VERSION", "1.1.0")
    monkeypatch.setattr("aef.upgrade_plan.TARGET_WORKSPACE_SCHEMA_VERSION", "1.1.0")
    injected = (
        MigrationSpec(
            "lot4.1.0.0-1.1.0", "1.0.0", "1.1.0",
            (".agent/manifest.json",), _identity,
        ),
    )
    status, _, extra = run_upgrade(tmp_path, mode="apply", migrations=injected)
    assert status == "BLOCKED"
    assert extra["reason"] == "competency_declaration_recovery_required"
    manifest = json.loads(
        (tmp_path / ".agent" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.get("schema_version") == "1.0.0"


def test_recover_declaration_blocks_on_foreign_journals(tmp_path, capsys):
    _plant_prepared_declaration_journal(tmp_path, capsys)
    eval_path = tmp_path.joinpath(*EVALUATION_TRANSACTION_PATH.split("/"))
    eval_path.write_text("{}", encoding="utf-8")
    with pytest.raises(CompetencyDeclarationBlockedError) as exc:
        recover_declaration(tmp_path, dry_run=True)
    assert exc.value.code == "evaluation_recovery_required"
    eval_path.unlink()

    upgrade_path = tmp_path.joinpath(*UPGRADE_TRANSACTION_PATH.split("/"))
    upgrade_path.write_text("{}", encoding="utf-8")
    with pytest.raises(CompetencyDeclarationBlockedError) as exc:
        recover_declaration(tmp_path, dry_run=True)
    assert exc.value.code == "upgrade_recovery_required"


def test_recover_dry_run_announces_real_journal_diff(tmp_path, capsys):
    _plant_prepared_declaration_journal(tmp_path, capsys)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--recover", "--dry-run",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert envelope["result"]["recovery_action"] == "rollback"
    diff = envelope["diff"]
    assert TRANSACTION_PATH in diff["removed"]
    assert (tmp_path / Path(*TRANSACTION_PATH.split("/"))).is_file()


def test_audit_declared_without_ledger_is_error(tmp_path, capsys):
    init_and_record(tmp_path, capsys)
    competencies = tmp_path / ".agent" / "state" / "competencies.json"
    competencies.write_text(
        json.dumps({
            "ghost-skill": {
                "id": "ghost-skill",
                "title": "Ghost",
                "level": "L1",
                "xp": 0,
                "cases": 0,
                "trust": None,
                "complex_cases": 0,
                "recent_significant_errors": 0,
                "probation": False,
                "source": "declared",
            }
        }),
        encoding="utf-8",
    )
    findings = audit_declaration_provenance(load_workspace(tmp_path), tmp_path)
    assert any(
        item["id"] == "competency-missing-declaration-provenance"
        and item["severity"] == "error"
        and item["competency_id"] == "ghost-skill"
        for item in findings
    )


def test_audit_declared_promoted_with_history_is_clean(tmp_path, capsys):
    persisted = init_and_record(tmp_path, capsys)
    status, _, _, _ = plan_declare(tmp_path, declaration_for(persisted), dry_run=False)
    assert status == "CHANGE"
    competencies_path = tmp_path / ".agent" / "state" / "competencies.json"
    competencies = json.loads(competencies_path.read_text(encoding="utf-8"))
    competencies["dry-run-review"]["level"] = "L2"
    competencies_path.write_text(json.dumps(competencies), encoding="utf-8")
    evaluations_path = tmp_path / ".agent" / "state" / "evaluations.json"
    evaluations = json.loads(evaluations_path.read_text(encoding="utf-8"))
    evaluations["promotion_recommendations"] = [{
        "id": "rec-1",
        "competency_id": "dry-run-review",
        "from_level": "L1",
        "to_level": "L2",
        "status": "applied",
    }]
    evaluations["promotion_decisions"] = [{
        "id": "dec-1",
        "recommendation_id": "rec-1",
        "to_level": "L2",
    }]
    evaluations_path.write_text(json.dumps(evaluations), encoding="utf-8")
    findings = audit_declaration_provenance(load_workspace(tmp_path), tmp_path)
    assert not any(item["id"] == "competency-declaration-level-drift" for item in findings)


def test_audit_declared_level_drift_without_history_is_error(tmp_path, capsys):
    persisted = init_and_record(tmp_path, capsys)
    plan_declare(tmp_path, declaration_for(persisted), dry_run=False)
    competencies_path = tmp_path / ".agent" / "state" / "competencies.json"
    competencies = json.loads(competencies_path.read_text(encoding="utf-8"))
    competencies["dry-run-review"]["level"] = "L2"
    competencies_path.write_text(json.dumps(competencies), encoding="utf-8")
    findings = audit_declaration_provenance(load_workspace(tmp_path), tmp_path)
    assert any(
        item["id"] == "competency-declaration-level-drift"
        and item["competency_id"] == "dry-run-review"
        and item["severity"] == "error"
        for item in findings
    )


def test_open_declaration_journal_blocks_record(tmp_path, capsys):
    persisted, _, _ = _plant_prepared_declaration_journal(tmp_path, capsys)
    with pytest.raises(CompetencyDeclarationRecoveryRequiredError):
        persist_record(tmp_path, persisted, dry_run=True)


@pytest.mark.parametrize("competency_id", ["dry\u00a0run", "caf\u0435-review"])
def test_nbsp_and_latin_cyrillic_mix_are_rejected(competency_id):
    with pytest.raises(InvalidCompetencyIdentifierError):
        validate_competency_id(competency_id)
