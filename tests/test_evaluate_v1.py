from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from io import StringIO

import pytest

from conftest import installed_aef_script

from aef.filesystem import apply_workspace, load_workspace, render_workspace_plan


evaluation_engine = pytest.importorskip("aef.evaluation_engine")


PENDING = {
    "id": "promotion:career:global:L1:L2",
    "type": "promotion",
    "scope": "career",
    "competency_id": None,
    "from_level": "L1",
    "to_level": "L2",
    "status": "pending",
    "detected_at": "2026-08-14T10:00:00Z",
    "evidence": {
        "xp": 50,
        "cases": 10,
        "trust": 0.9,
        "complex_cases": 0,
        "recent_significant_errors": 0,
    },
    "evidence_digest": (
        "sha256:0040087530564ecf50925019a020cfe486ccf3c4c49d13fdd6d311432b443d92"
    ),
}


def evaluations(recommendation=None):
    return {
        "schema_version": "1.0.0",
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "promotion_recommendations": (
            [] if recommendation is None else [deepcopy(recommendation)]
        ),
    }


def career(**overrides):
    state = {
        "level": "L1",
        "xp": 50,
        "cases": 10,
        "trust": 0.9,
        "complex_cases": 0,
        "recent_significant_errors": 0,
        "status": "active",
        "probation": False,
    }
    state.update(overrides)
    return state


def project(recommendation=None, *, career_state=None, competencies=None):
    return {
        "files": {
            ".agent/manifest.json": {"framework": "aef"},
            ".agent/state/evaluations.json": evaluations(recommendation),
            ".agent/state/career.json": career_state or career(),
            ".agent/state/competencies.json": competencies or {},
        }
    }


def mixed_project_and_decisions():
    from aef.promotion_recommendations import ensure_pending_promotion

    competency_id = "record-classification"
    competency = career()
    competency["id"] = competency_id
    _, state, career_id, _ = ensure_pending_promotion(
        evaluations(), career(), scope="career", detected_at="2026-08-14T10:00:00Z"
    )
    _, state, competency_recommendation_id, _ = ensure_pending_promotion(
        state, competency, scope="competency", competency_id=competency_id,
        detected_at="2026-08-14T10:00:01Z",
    )
    recommendations = {
        item["id"]: item for item in state["promotion_recommendations"]
    }
    source = project(
        career_state=career(), competencies={competency_id: competency}
    )
    source["files"][".agent/state/evaluations.json"] = state
    decisions = []
    for number, recommendation_id in enumerate(
        (career_id, competency_recommendation_id), start=1
    ):
        recommendation = recommendations[recommendation_id]
        decisions.append({
            "id": f"evaluation:promotion:mixed-{number}",
            "recommendation_id": recommendation_id,
            "decision": "approve",
            "reason": "The current evidence supports this promotion.",
            "expected_evidence_digest": recommendation["evidence_digest"],
            "expected_current_evidence_digest": recommendation["evidence_digest"],
            "approval": {
                "approved": True, "source": "human", "actor": "Alex Example",
                "approved_at": f"2026-08-15T10:00:0{number}Z",
            },
        })
    return source, {"protocol": "aef.evaluate/v1", "decisions": decisions}


def approve_document(**overrides):
    decision = {
        "id": "evaluation:promotion:manual-001",
        "recommendation_id": PENDING["id"],
        "decision": "approve",
        "reason": "The current evidence supports promotion.",
        "expected_evidence_digest": PENDING["evidence_digest"],
        "expected_current_evidence_digest": PENDING["evidence_digest"],
        "approval": {
            "approved": True,
            "source": "human",
            "actor": "Alex Example",
            "approved_at": "2026-08-15T10:00:00Z",
        },
    }
    decision.update(overrides)
    return {"protocol": "aef.evaluate/v1", "decisions": [decision]}


def reject_document(**overrides):
    decision = {
        "id": "evaluation:promotion:manual-002",
        "recommendation_id": PENDING["id"],
        "decision": "reject",
        "reason": "The demonstrated scope remains too narrow.",
        "expected_evidence_digest": PENDING["evidence_digest"],
        "rejection": {
            "rejected": True,
            "source": "human",
            "actor": "Alex Example",
            "rejected_at": "2026-08-15T10:00:00Z",
        },
    }
    decision.update(overrides)
    return {"protocol": "aef.evaluate/v1", "decisions": [decision]}


def test_protocol_contract_is_closed_and_distinguishes_approve_and_reject():
    approve = approve_document()
    reject = reject_document()

    assert evaluation_engine.validate_evaluation_decisions(approve) is approve
    assert evaluation_engine.validate_evaluation_decisions(reject) is reject

    for invalid in (
        {**approve, "unexpected": True},
        {"protocol": "aef.evaluate/v1", "decisions": [{**approve["decisions"][0], "unexpected": True}]},
        {"protocol": "aef.evaluate/v1", "decisions": [{**approve["decisions"][0], "decision": "later"}]},
        {"protocol": "aef.evaluate/v1", "decisions": [{**reject["decisions"][0], "approval": approve["decisions"][0]["approval"]}]},
    ):
        with pytest.raises(evaluation_engine.InvalidEvaluationDecisionsError):
            evaluation_engine.validate_evaluation_decisions(invalid)


def test_listing_pending_recommendations_is_read_only_and_recomputes_readiness():
    source_evaluations = evaluations(PENDING)
    source_career = career(xp=60, cases=12)
    before_evaluations = deepcopy(source_evaluations)
    before_career = deepcopy(source_career)

    listed = evaluation_engine.list_pending_recommendations(
        source_evaluations, source_career, {}
    )

    assert len(listed) == 1
    assert listed[0]["scope"] == "career"
    assert listed[0]["current_evidence"]["xp"] == 60
    assert listed[0]["readiness"] == {"eligible": True, "target": "L2", "reasons": []}
    assert listed[0]["stale"] is False
    assert source_evaluations == before_evaluations
    assert source_career == before_career


def test_probation_makes_a_pending_recommendation_stale():
    listed = evaluation_engine.list_pending_recommendations(
        evaluations(PENDING), career(probation=True), {}
    )

    assert listed[0]["stale"] is True
    assert listed[0]["stale_reason"] == "probation"
    assert listed[0]["readiness"]["eligible"] is False


def test_persisted_promotion_decision_is_closed_and_optional():
    state = evaluations(PENDING)
    state["promotion_decisions"] = [{
        "id": "evaluation:promotion:manual-001",
        "recommendation_id": PENDING["id"],
        "decision": "approve",
        "reason": "The current evidence supports promotion.",
        "source": "human",
        "actor": "Alex Example",
        "decided_at": "2026-08-15T10:00:00Z",
        "recommendation_evidence_digest": PENDING["evidence_digest"],
        "current_evidence": deepcopy(PENDING["evidence"]),
        "current_evidence_digest": PENDING["evidence_digest"],
        "from_level": "L1",
        "to_level": "L2",
    }]

    assert evaluation_engine.validate_evaluation_state(state) is state
    invalid = deepcopy(state)
    invalid["promotion_decisions"][0]["unexpected"] = True
    with pytest.raises(evaluation_engine.InvalidPromotionRecommendationStateError):
        evaluation_engine.validate_evaluation_state(invalid)


def test_historical_evaluation_without_promotion_decisions_is_not_normalized():
    state = evaluations(PENDING)
    state.pop("schema_version")
    before = deepcopy(state)

    assert evaluation_engine.validate_evaluation_state(state) is state
    assert state == before
    assert "promotion_decisions" not in state


def test_rejected_recommendation_gets_new_deterministic_generation_for_new_evidence():
    from aef.promotion_recommendations import ensure_pending_promotion

    old = deepcopy(PENDING)
    old["status"] = "rejected"
    state = evaluations(old)
    new_evidence = career(xp=60, cases=11)

    status, changed, recommendation_id, created = ensure_pending_promotion(
        state, new_evidence, scope="career", detected_at="2026-08-16T10:00:00Z"
    )
    replay = ensure_pending_promotion(
        changed, new_evidence, scope="career", detected_at="2026-08-17T10:00:00Z"
    )

    assert status == "CHANGE"
    assert created is True
    assert recommendation_id.startswith("promotion:career:global:L1:L2:evidence-")
    assert len(recommendation_id.rsplit("-", 1)[1]) == 64
    assert len(changed["promotion_recommendations"]) == 2
    assert replay[0] == "NO_CHANGE"
    assert replay[1] == changed
    assert replay[2] == recommendation_id
    assert replay[3] is False


def test_rejected_recommendation_with_same_evidence_is_not_duplicated():
    from aef.promotion_recommendations import ensure_pending_promotion

    old = deepcopy(PENDING)
    old["status"] = "rejected"
    state = evaluations(old)

    status, unchanged, recommendation_id, created = ensure_pending_promotion(
        state, career(), scope="career", detected_at="2026-08-16T10:00:00Z"
    )

    assert status == "NO_CHANGE"
    assert created is False
    assert recommendation_id == PENDING["id"]
    assert unchanged == state


def test_approve_persists_decision_history_and_exactly_one_level_change():
    source = project(PENDING)
    before = deepcopy(source)

    status, out, meta = evaluation_engine.evaluate_project(
        source, approve_document()
    )

    assert status == "CHANGE"
    assert out["files"][".agent/state/career.json"]["level"] == "L2"
    state = out["files"][".agent/state/evaluations.json"]
    assert state["promotion_recommendations"][0]["status"] == "approved"
    assert state["promotion_decisions"][0]["decision"] == "approve"
    assert state["promotion_decisions"][0]["actor"] == "Alex Example"
    assert state["history"] == [{
        "id": "evaluation:promotion:manual-001",
        "performed_at": "2026-08-15T10:00:00Z",
        "result": "promote",
    }]
    assert meta["levels_changed"] == [{
        "scope": "career", "competency_id": None,
        "from_level": "L1", "to_level": "L2",
    }]
    assert source == before


def test_reject_persists_human_decision_without_changing_level_and_replays():
    source = project(PENDING)
    status, out, meta = evaluation_engine.evaluate_project(
        source, reject_document()
    )
    replay = evaluation_engine.evaluate_project(out, reject_document())

    assert status == "CHANGE"
    assert out["files"][".agent/state/career.json"]["level"] == "L1"
    state = out["files"][".agent/state/evaluations.json"]
    assert state["promotion_recommendations"][0]["status"] == "rejected"
    assert state["promotion_decisions"][0]["decision"] == "reject"
    assert state["history"][0]["result"] == "maintain"
    assert meta["levels_changed"] == []
    assert replay[0] == "NO_CHANGE"
    assert replay[1] == out


def test_stale_approval_blocks_entire_batch_without_mutation():
    source = project(PENDING, career_state=career(probation=True))
    before = deepcopy(source)

    status, out, meta = evaluation_engine.evaluate_project(
        source, approve_document()
    )

    assert status == "BLOCKED"
    assert meta["reason"] == "recommendation_stale"
    assert out == before == source


def test_refresh_withdraws_ineligible_and_supersedes_changed_level():
    withdrawn = project(PENDING, career_state=career(probation=True))
    status, out, meta = evaluation_engine.refresh_project_recommendations(withdrawn)
    assert status == "CHANGE"
    assert out["files"][".agent/state/evaluations.json"][
        "promotion_recommendations"
    ][0]["status"] == "withdrawn"
    assert meta["levels_changed"] == []

    superseded = project(PENDING, career_state=career(level="L2", xp=200, cases=30))
    status, out, _ = evaluation_engine.refresh_project_recommendations(superseded)
    assert status == "CHANGE"
    assert out["files"][".agent/state/evaluations.json"][
        "promotion_recommendations"
    ][0]["status"] == "superseded"


def test_multi_file_evaluation_uses_recoverable_transaction(tmp_path):
    from aef.evaluation_transaction import (
        TRANSACTION_PATH, apply_evaluation_transaction,
    )

    source = project(PENDING)
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    status, desired, _ = evaluation_engine.evaluate_project(
        current, approve_document()
    )

    diff, journal = apply_evaluation_transaction(
        tmp_path, current, desired, approve_document()
    )
    reloaded = load_workspace(tmp_path)

    assert status == "CHANGE"
    assert diff["modified"] == [
        ".agent/state/career.json", ".agent/state/evaluations.json",
    ]
    assert journal["phase"] == "committed"
    assert reloaded["files"][".agent/state/career.json"]["level"] == "L2"
    assert TRANSACTION_PATH not in reloaded["files"]


def test_prepared_transaction_rolls_back_after_interrupted_file_write(tmp_path):
    from aef.evaluation_transaction import (
        TRANSACTION_PATH, apply_evaluation_transaction,
        recover_evaluation_transaction,
    )

    source = project(PENDING)
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, approve_document())

    def interrupt(phase, index):
        if phase == "file" and index == 1:
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        apply_evaluation_transaction(
            tmp_path, current, desired, approve_document(), fault=interrupt
        )
    interrupted = load_workspace(tmp_path)
    assert interrupted["files"][TRANSACTION_PATH]["phase"] == "prepared"

    status, _, meta = recover_evaluation_transaction(
        tmp_path, interrupted
    )
    recovered = load_workspace(tmp_path)

    assert status == "CHANGE"
    assert meta["action"] == "rollback"
    assert recovered["files"][".agent/state/career.json"]["level"] == "L1"
    assert recovered["files"][".agent/state/evaluations.json"] == evaluations(PENDING)
    assert TRANSACTION_PATH not in recovered["files"]


def test_incomplete_transaction_blocks_mutations_but_list_and_audit_remain_safe():
    from aef.evaluation_transaction import TRANSACTION_PATH
    from aef.operations import audit_project, consolidate_project, discover_project

    source = project(PENDING)
    source["files"][TRANSACTION_PATH] = {"opaque": "journal presence is sufficient"}
    before = deepcopy(source)

    listed = evaluation_engine.list_project_recommendations(source)
    evaluated = evaluation_engine.evaluate_project(source, approve_document())
    refreshed = evaluation_engine.refresh_project_recommendations(source)
    discovered = discover_project(source, {"connectors": []})
    consolidated = consolidate_project(
        source, {"protocol": "aef.consolidate/v1", "reviews": []}
    )
    audited = audit_project(source)

    assert listed[0] == "NO_CHANGE"
    assert listed[2]["recovery_required"] is True
    for result in (evaluated, refreshed, discovered, consolidated):
        assert result[0] == "BLOCKED"
        assert result[2]["reason"] == "evaluation_recovery_required"
        assert result[1] == source
    assert {"id": "evaluation-recovery-required", "severity": "error"} in audited["findings"]
    assert audited["status"] == "FAIL"
    assert source == before


def test_cli_list_is_read_only_in_json_and_human_modes(tmp_path):
    source = project(PENDING)
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }

    json_result = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
         "evaluate", "--list"],
        capture_output=True, text=True, check=False,
    )
    human_result = subprocess.run(
        [sys.executable, "-m", "aef", "--human", "--workspace", str(tmp_path),
         "evaluate", "--list"],
        capture_output=True, text=True, check=False,
    )
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }

    assert json_result.returncode == human_result.returncode == 0
    envelope = json.loads(json_result.stdout)
    assert envelope["status"] == "NO_CHANGE"
    assert envelope["result"]["recommendations"][0]["stale"] is False
    assert "Promotion recommendations require review" in human_result.stdout
    assert before == after


def test_cli_decision_dry_run_matches_real_application(tmp_path):
    source = project(PENDING)
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    decisions_path = tmp_path / "evaluation decisions.json"
    decisions_path.write_text(
        json.dumps(approve_document(), ensure_ascii=False), encoding="utf-8"
    )
    before = (tmp_path / ".agent/state/career.json").read_bytes()

    dry = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
         "evaluate", "--decisions", str(decisions_path), "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert (tmp_path / ".agent/state/career.json").read_bytes() == before
    real = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
         "evaluate", "--decisions", str(decisions_path)],
        capture_output=True, text=True, check=False,
    )

    assert dry.returncode == real.returncode == 0
    assert json.loads(dry.stdout)["status"] == "CHANGE"
    assert json.loads(real.stdout)["status"] == "CHANGE"
    assert load_workspace(tmp_path)["files"][".agent/state/career.json"]["level"] == "L2"


def test_interactive_empty_response_is_later_and_never_approval(monkeypatch):
    from aef import cli

    monkeypatch.setattr(cli.sys, "stdin", StringIO("\n"))
    document = cli._collect_interactive_decisions(
        evaluation_engine.list_pending_recommendations(
            evaluations(PENDING), career(), {}
        )
    )

    assert document == {"protocol": "aef.evaluate/v1", "decisions": []}


def test_interactive_approve_requires_actor_reason_and_final_confirmation(monkeypatch):
    from aef import cli

    monkeypatch.setattr(
        cli.sys, "stdin",
        StringIO("a\nAlex Example\nEvidence is sufficient.\ny\n"),
    )
    monkeypatch.setattr(cli, "_utc_now", lambda: "2026-08-15T10:00:00Z")
    document = cli._collect_interactive_decisions(
        evaluation_engine.list_pending_recommendations(
            evaluations(PENDING), career(), {}
        )
    )

    assert evaluation_engine.validate_evaluation_decisions(document) is document
    decision = document["decisions"][0]
    assert decision["decision"] == "approve"
    assert decision["reason"] == "Evidence is sufficient."
    assert decision["approval"] == {
        "approved": True, "source": "human", "actor": "Alex Example",
        "approved_at": "2026-08-15T10:00:00Z",
    }


def test_mixed_career_and_competency_batch_changes_exactly_three_state_files():
    source, decisions = mixed_project_and_decisions()
    status, out, meta = evaluation_engine.evaluate_project(source, decisions)

    assert status == "CHANGE"
    assert out["files"][".agent/state/career.json"]["level"] == "L2"
    assert out["files"][".agent/state/competencies.json"][
        "record-classification"
    ]["level"] == "L2"
    assert len(meta["levels_changed"]) == 2
    assert render_workspace_plan(source, out)[0]["modified"] == [
        ".agent/state/career.json",
        ".agent/state/competencies.json",
        ".agent/state/evaluations.json",
    ]


@pytest.mark.parametrize(("fault_phase", "fault_index", "expected_action"), [
    ("prepared", 0, "rollback"),
    ("file", 1, "rollback"),
    ("file", 2, "rollback"),
    ("file", 3, "rollback"),
    ("committed", 3, "finalize"),
])
def test_transaction_recovers_after_every_persistence_step(
    tmp_path, fault_phase, fault_index, expected_action
):
    from aef.evaluation_transaction import (
        TRANSACTION_PATH, apply_evaluation_transaction,
        recover_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, decisions)

    def interrupt(phase, index):
        if phase == fault_phase and index == fault_index:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        apply_evaluation_transaction(
            tmp_path, current, desired, decisions, fault=interrupt
        )
    interrupted = load_workspace(tmp_path)
    status, _, meta = recover_evaluation_transaction(tmp_path, interrupted)
    recovered = load_workspace(tmp_path)

    assert status == "CHANGE"
    assert meta["action"] == expected_action
    expected_level = "L2" if expected_action == "finalize" else "L1"
    assert recovered["files"][".agent/state/career.json"]["level"] == expected_level
    assert recovered["files"][".agent/state/competencies.json"][
        "record-classification"
    ]["level"] == expected_level
    assert TRANSACTION_PATH not in recovered["files"]


def test_transaction_dry_run_bytes_match_real_application_and_replay(tmp_path):
    from aef.evaluation_transaction import apply_evaluation_transaction

    source, decisions = mixed_project_and_decisions()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, decisions)
    expected_diff, expected_bytes = render_workspace_plan(current, desired)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }

    dry_diff, _ = apply_evaluation_transaction(
        tmp_path, current, desired, decisions, dry_run=True
    )
    assert dry_diff == expected_diff
    assert before == {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    apply_evaluation_transaction(tmp_path, current, desired, decisions)
    for path in expected_diff["modified"]:
        assert (tmp_path / path).read_bytes() == expected_bytes[path].encode("utf-8")

    applied = load_workspace(tmp_path)
    replay_status, replay_desired, _ = evaluation_engine.evaluate_project(
        applied, decisions
    )
    before_replay = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    replay_diff, _ = apply_evaluation_transaction(
        tmp_path, applied, replay_desired, decisions
    )
    assert replay_status == "NO_CHANGE"
    assert replay_diff == {"created": [], "modified": [], "removed": []}
    assert before_replay == {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("launcher", ["module", "script"])
@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_installed_evaluate_list_modes_are_separated_and_read_only(
    tmp_path, launcher, mode
):
    source = project(PENDING)
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    before = (tmp_path / ".agent/state/evaluations.json").read_bytes()
    script = installed_aef_script()
    prefix = [sys.executable, "-m", "aef"] if launcher == "module" else [str(script)]
    option = "--human" if mode == "human" else f"--{mode}"

    completed = subprocess.run(
        [*prefix, option, "--workspace", str(tmp_path), "evaluate", "--list"],
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0
    assert (tmp_path / ".agent/state/evaluations.json").read_bytes() == before
    if mode == "human":
        assert "Promotion recommendations require review" in completed.stdout
        assert '"api_version"' not in completed.stdout
    else:
        assert json.loads(completed.stdout)["command"] == "EVALUATE"
        assert "Promotion recommendations require review" not in completed.stdout
        if mode == "compact":
            assert completed.stdout.count("\n") == 1


def test_non_tty_evaluate_without_explicit_action_never_prompts(tmp_path):
    apply_workspace(tmp_path, load_workspace(tmp_path), project(PENDING))

    completed = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
         "evaluate"],
        input="", capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 3
    assert json.loads(completed.stdout)["error"]["code"] == "interactive_input_required"
    assert "Approve / Reject / Later" not in completed.stderr


def test_evaluate_without_decisions_does_not_promote(tmp_path):
    apply_workspace(tmp_path, load_workspace(tmp_path), project(PENDING))
    career_path = tmp_path / ".agent" / "state" / "career.json"
    evaluations_path = tmp_path / ".agent" / "state" / "evaluations.json"
    before_career = career_path.read_bytes()
    before_evaluations = evaluations_path.read_bytes()
    before_level = json.loads(before_career.decode("utf-8"))["level"]

    completed = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
         "evaluate"],
        input="", capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 3
    assert json.loads(completed.stdout)["error"]["code"] == "interactive_input_required"
    assert career_path.read_bytes() == before_career
    assert evaluations_path.read_bytes() == before_evaluations
    assert json.loads(career_path.read_text(encoding="utf-8"))["level"] == before_level == "L1"


def test_cli_recover_dry_run_then_application_is_idempotent(tmp_path):
    from aef.evaluation_transaction import TRANSACTION_PATH, apply_evaluation_transaction

    source, decisions = mixed_project_and_decisions()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, decisions)

    def interrupt(phase, index):
        if phase == "file" and index == 2:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        apply_evaluation_transaction(
            tmp_path, current, desired, decisions, fault=interrupt
        )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    base = [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
            "evaluate", "--recover"]
    dry = subprocess.run(
        [*base, "--dry-run"], capture_output=True, text=True, check=False
    )
    assert before == {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    real = subprocess.run(base, capture_output=True, text=True, check=False)
    replay = subprocess.run(base, capture_output=True, text=True, check=False)

    assert dry.returncode == real.returncode == replay.returncode == 0
    assert json.loads(dry.stdout)["status"] == "CHANGE"
    assert json.loads(real.stdout)["status"] == "CHANGE"
    assert json.loads(replay.stdout)["status"] == "NO_CHANGE"
    assert TRANSACTION_PATH not in load_workspace(tmp_path)["files"]


def test_persisted_decision_cross_validates_with_official_schema():
    from pathlib import Path as LocalPath
    import jsonschema
    from aef.schema_validation import draft202012_validator

    status, out, _ = evaluation_engine.evaluate_project(
        project(PENDING), approve_document()
    )
    state = out["files"][".agent/state/evaluations.json"]
    schema = json.loads(
        (LocalPath(__file__).resolve().parents[1] / "src/aef/schemas/evaluation.schema.json")
        .read_text(encoding="utf-8")
    )

    assert status == "CHANGE"
    assert evaluation_engine.validate_evaluation_state(state) is state
    draft202012_validator(schema).validate(state)
    assert json.loads(json.dumps(state, allow_nan=False)) == state


def test_recovery_conflict_never_overwrites_foreign_state(tmp_path):
    from aef.evaluation_transaction import (
        apply_evaluation_transaction, recover_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, decisions)

    def interrupt(phase, index):
        if phase == "file" and index == 1:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        apply_evaluation_transaction(
            tmp_path, current, desired, decisions, fault=interrupt
        )
    career_path = tmp_path / ".agent/state/career.json"
    foreign = career_path.read_text(encoding="utf-8").replace('"L2"', '"L4"')
    career_path.write_text(foreign, encoding="utf-8", newline="\n")
    before = career_path.read_bytes()

    status, _, meta = recover_evaluation_transaction(
        tmp_path, load_workspace(tmp_path)
    )

    assert status == "BLOCKED"
    assert meta["reason"] == "evaluation_recovery_conflict"
    assert career_path.read_bytes() == before


def test_refresh_dry_run_and_application_withdraw_without_level_change(tmp_path):
    source = project(PENDING, career_state=career(probation=True))
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    career_before = (tmp_path / ".agent/state/career.json").read_bytes()
    base = [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path),
            "evaluate", "--refresh"]

    dry = subprocess.run(
        [*base, "--dry-run"], capture_output=True, text=True, check=False
    )
    assert load_workspace(tmp_path)["files"][".agent/state/evaluations.json"][
        "promotion_recommendations"
    ][0]["status"] == "pending"
    real = subprocess.run(base, capture_output=True, text=True, check=False)
    replay = subprocess.run(base, capture_output=True, text=True, check=False)

    assert dry.returncode == real.returncode == replay.returncode == 0
    assert json.loads(dry.stdout)["status"] == "CHANGE"
    assert json.loads(real.stdout)["status"] == "CHANGE"
    assert json.loads(replay.stdout)["status"] == "NO_CHANGE"
    assert load_workspace(tmp_path)["files"][".agent/state/evaluations.json"][
        "promotion_recommendations"
    ][0]["status"] == "withdrawn"
    assert (tmp_path / ".agent/state/career.json").read_bytes() == career_before


@pytest.mark.parametrize("mutation", [
    lambda journal: journal.update({"files": [42]}),
    lambda journal: journal.update({"paths": [1, "x"]}),
    lambda journal: journal.update({"paths": [{}]}),
    lambda journal: journal.update({"paths": None}),
    lambda journal: journal.update({"files": None}),
    lambda journal: journal.update({"paths": {"path": "x"}}),
    lambda journal: journal.update({"files": {"path": "x"}}),
    lambda journal: journal.update({"paths": journal["paths"] * 2}),
    lambda journal: journal["files"][0].update({"unexpected": True}),
    lambda journal: journal["files"].append({}),
])
def test_malformed_transaction_structure_always_raises_domain_error(mutation):
    from aef.evaluation_transaction import (
        InvalidEvaluationTransactionError,
        build_evaluation_transaction,
        validate_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    _, desired, _ = evaluation_engine.evaluate_project(source, decisions)
    journal = build_evaluation_transaction(source, desired, decisions)
    mutation(journal)

    with pytest.raises(InvalidEvaluationTransactionError):
        validate_evaluation_transaction(journal)


@pytest.mark.parametrize("mutation", [
    lambda journal: journal.update({"decision_batch_digest": "arbitrary"}),
    lambda journal: journal.update({
        "decision_batch_digest": "sha256:" + "A" * 64,
        "transaction_id": "evaluation-transaction:" + "A" * 64,
    }),
    lambda journal: journal.update({
        "decision_batch_digest": "sha256:" + "a" * 63,
        "transaction_id": "evaluation-transaction:" + "a" * 63,
    }),
    lambda journal: journal.update({"transaction_id": "evaluation-transaction:wrong"}),
    lambda journal: journal.update({"paths": [], "files": []}),
    lambda journal: journal.update({
        "paths": [".agent/state/career.json"], "files": [journal["files"][0]],
    }),
    lambda journal: journal.update({
        "paths": [path for path in journal["paths"] if not path.endswith("evaluations.json")],
        "files": [entry for entry in journal["files"] if not entry["path"].endswith("evaluations.json")],
    }),
    lambda journal: journal.update({
        "paths": sorted([*journal["paths"], ".agent/manifest.json"]),
    }),
    lambda journal: journal["files"][0].update({
        "path": ".agent/state/evaluation-transaction.json"
    }),
    lambda journal: journal["files"][0].update({"before_hash": "sha256:" + "0" * 64}),
    lambda journal: journal["files"][0].update({"after_hash": "sha256:" + "0" * 64}),
])
def test_transaction_identity_and_plan_reject_all_noncanonical_variants(mutation):
    from aef.evaluation_transaction import (
        InvalidEvaluationTransactionError,
        build_evaluation_transaction,
        validate_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    _, desired, _ = evaluation_engine.evaluate_project(source, decisions)
    journal = build_evaluation_transaction(source, desired, decisions)
    mutation(journal)

    with pytest.raises(InvalidEvaluationTransactionError):
        validate_evaluation_transaction(journal)


@pytest.mark.parametrize("invalid_content", [
    "NaN\n",
    "[]\n",
    '{"duplicate":1,"duplicate":2}\n',
    '{"not":"canonical"}\n',
])
def test_transaction_rejects_non_strict_or_noncanonical_embedded_content(invalid_content):
    from aef.evaluation_transaction import (
        InvalidEvaluationTransactionError,
        _sha256_bytes,
        build_evaluation_transaction,
        validate_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    _, desired, _ = evaluation_engine.evaluate_project(source, decisions)
    journal = build_evaluation_transaction(source, desired, decisions)
    journal["files"][0]["before_content"] = invalid_content
    journal["files"][0]["before_hash"] = _sha256_bytes(invalid_content)

    with pytest.raises(InvalidEvaluationTransactionError):
        validate_evaluation_transaction(journal)


def test_duplicate_key_in_embedded_journal_content_keeps_pre_lot1_error_contract():
    """Shared reject_duplicate_keys must not change EVALUATE journal diagnostics."""
    from aef.evaluation_transaction import (
        InvalidEvaluationTransactionError,
        _parse_strict_content,
        _sha256_bytes,
        build_evaluation_transaction,
        validate_evaluation_transaction,
    )

    duplicate = '{"phase":"prepared","phase":"committed"}\n'
    with pytest.raises(
        InvalidEvaluationTransactionError,
        match=r"^invalid evaluation transaction JSON content$",
    ) as raised:
        _parse_strict_content(duplicate)
    assert raised.value.__cause__ is not None

    source, decisions = mixed_project_and_decisions()
    _, desired, _ = evaluation_engine.evaluate_project(source, decisions)
    journal = build_evaluation_transaction(source, desired, decisions)
    journal["files"][0]["before_content"] = duplicate
    journal["files"][0]["before_hash"] = _sha256_bytes(duplicate)
    with pytest.raises(
        InvalidEvaluationTransactionError,
        match=r"^invalid evaluation transaction JSON content$",
    ):
        validate_evaluation_transaction(journal)


def test_valid_nominal_transaction_contract_remains_accepted():
    from aef.evaluation_transaction import (
        build_evaluation_transaction, validate_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    _, desired, _ = evaluation_engine.evaluate_project(source, decisions)
    journal = build_evaluation_transaction(source, desired, decisions)

    assert validate_evaluation_transaction(journal) is journal


def test_recovery_preflights_all_files_before_rolling_back_any(tmp_path):
    from aef.evaluation_transaction import (
        apply_evaluation_transaction, recover_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, decisions)

    def interrupt(phase, index):
        if phase == "file" and index == 2:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        apply_evaluation_transaction(
            tmp_path, current, desired, decisions, fault=interrupt
        )
    career_path = tmp_path / ".agent/state/career.json"
    competencies_path = tmp_path / ".agent/state/competencies.json"
    evaluations_path = tmp_path / ".agent/state/evaluations.json"
    career_before = career_path.read_bytes()
    competencies_before = competencies_path.read_bytes()
    foreign = json.loads(evaluations_path.read_text(encoding="utf-8"))
    foreign["foreign_extension"] = True
    evaluations_path.write_text(
        json.dumps(foreign, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    evaluation_before = evaluations_path.read_bytes()

    status, _, meta = recover_evaluation_transaction(
        tmp_path, load_workspace(tmp_path)
    )

    assert status == "BLOCKED"
    assert meta["reason"] == "evaluation_recovery_conflict"
    assert career_path.read_bytes() == career_before
    assert competencies_path.read_bytes() == competencies_before
    assert evaluations_path.read_bytes() == evaluation_before


@pytest.mark.parametrize("launcher", ["module", "script"])
@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_invalid_transaction_is_public_code_3_in_every_cli_mode(
    tmp_path, launcher, mode
):
    from aef.evaluation_transaction import (
        TRANSACTION_PATH, build_evaluation_transaction,
    )

    source, decisions = mixed_project_and_decisions()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    _, desired, _ = evaluation_engine.evaluate_project(current, decisions)
    journal = build_evaluation_transaction(current, desired, decisions)
    journal["files"] = [42]
    transaction_path = tmp_path / TRANSACTION_PATH
    transaction_path.write_text(
        json.dumps(journal, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    script = installed_aef_script()
    prefix = [sys.executable, "-m", "aef"] if launcher == "module" else [str(script)]
    option = "--human" if mode == "human" else f"--{mode}"

    completed = subprocess.run(
        [*prefix, option, "--workspace", str(tmp_path), "evaluate", "--recover"],
        capture_output=True, text=True, check=False,
    )
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }

    assert completed.returncode == 3
    assert "Traceback" not in completed.stdout + completed.stderr
    assert before == after
    if mode == "human":
        assert "The persisted evaluation transaction is invalid." in completed.stdout
        assert '"api_version"' not in completed.stdout
    else:
        envelope = json.loads(completed.stdout)
        assert envelope["error"]["code"] == "invalid_evaluation_transaction"
        if mode == "compact":
            assert completed.stdout.count("\n") == 1


def _transaction_fixture():
    from aef.evaluation_transaction import build_evaluation_transaction

    source, decisions = mixed_project_and_decisions()
    _, desired, _ = evaluation_engine.evaluate_project(source, decisions)
    journal = build_evaluation_transaction(source, desired, decisions)
    return source, desired, journal


def test_transaction_capability_is_immutable_and_workspace_bound(tmp_path):
    from dataclasses import FrozenInstanceError
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    root_a = tmp_path / "workspace-a"
    root_b = tmp_path / "workspace-b"
    source, _, journal = _transaction_fixture()
    capability = _transaction_write_capability(root_a, journal, "prepare")
    desired = deepcopy(source)
    desired["files"][".agent/state/evaluation-transaction.json"] = journal

    with pytest.raises(FrozenInstanceError):
        capability.phase = "apply"
    with pytest.raises(WorkspacePathError, match="another workspace"):
        _apply_workspace_transaction(
            root_b, source, desired, capability
        )
    assert not root_a.exists()
    assert not root_b.exists()


def test_transaction_capability_cannot_cross_transactions_in_one_workspace(tmp_path):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    source, _, journal = _transaction_fixture()
    other = deepcopy(journal)
    other["decision_batch_digest"] = "sha256:" + "1" * 64
    other["transaction_id"] = "evaluation-transaction:" + "1" * 64
    capability = _transaction_write_capability(tmp_path, journal, "prepare")
    desired = deepcopy(source)
    desired["files"][".agent/state/evaluation-transaction.json"] = other

    with pytest.raises(WorkspacePathError, match="journal changed"):
        _apply_workspace_transaction(
            tmp_path, source, desired, capability
        )
    assert not (tmp_path / ".agent").exists()


@pytest.mark.parametrize("forbidden", [
    ".agent/manifest.json",
    ".agent/knowledge/knowledge.json",
    ".agent/integrations/registry.json",
    ".agent/core/constitution.md",
])
def test_transaction_capability_factory_rejects_forbidden_declared_paths(
    tmp_path, forbidden
):
    from aef.filesystem import WorkspacePathError, _transaction_write_capability

    _, _, journal = _transaction_fixture()
    journal["paths"].append(forbidden)

    with pytest.raises(WorkspacePathError, match="forbidden paths"):
        _transaction_write_capability(tmp_path, journal, "rollback")


def test_transaction_capability_rejects_undeclared_target_and_phase_reuse(tmp_path):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    source, _, journal = _transaction_fixture()
    with pytest.raises(WorkspacePathError, match="was not declared"):
        _transaction_write_capability(
            tmp_path, journal, "apply", target_path=".agent/state/unknown.json"
        )

    prepare = _transaction_write_capability(tmp_path, journal, "prepare")
    invalid_prepare = deepcopy(source)
    invalid_prepare["files"][".agent/state/career.json"] = {"level": "L2"}
    with pytest.raises(WorkspacePathError, match="prepare state"):
        _apply_workspace_transaction(
            tmp_path, source, invalid_prepare, prepare
        )


def test_transaction_capability_detects_replaced_real_journal(tmp_path):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    source, desired, journal = _transaction_fixture()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    prepare_desired = deepcopy(current)
    prepare_desired["files"][".agent/state/evaluation-transaction.json"] = journal
    prepare = _transaction_write_capability(tmp_path, journal, "prepare")
    _apply_workspace_transaction(tmp_path, current, prepare_desired, prepare)
    current = load_workspace(tmp_path)
    apply_capability = _transaction_write_capability(
        tmp_path, journal, "apply", target_path=".agent/state/career.json"
    )
    changed = deepcopy(journal)
    changed["decision_batch_digest"] = "sha256:" + "2" * 64
    changed["transaction_id"] = "evaluation-transaction:" + "2" * 64
    journal_path = tmp_path / ".agent/state/evaluation-transaction.json"
    journal_path.write_text(
        json.dumps(changed, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    career_before = (tmp_path / ".agent/state/career.json").read_bytes()
    step = deepcopy(current)
    step["files"][".agent/state/career.json"] = desired["files"][
        ".agent/state/career.json"
    ]

    with pytest.raises(WorkspacePathError, match="journal changed"):
        _apply_workspace_transaction(
            tmp_path, current, step, apply_capability
        )
    assert (tmp_path / ".agent/state/career.json").read_bytes() == career_before


def test_apply_capability_cannot_be_reused_for_cleanup(tmp_path):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    source, _, journal = _transaction_fixture()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    prepare_desired = deepcopy(current)
    prepare_desired["files"][".agent/state/evaluation-transaction.json"] = journal
    prepare = _transaction_write_capability(tmp_path, journal, "prepare")
    _apply_workspace_transaction(tmp_path, current, prepare_desired, prepare)
    current = load_workspace(tmp_path)
    desired = deepcopy(current)
    desired["files"].pop(".agent/state/evaluation-transaction.json")
    apply_capability = _transaction_write_capability(
        tmp_path, journal, "apply", target_path=".agent/state/career.json"
    )

    with pytest.raises(WorkspacePathError, match="cannot change the journal"):
        _apply_workspace_transaction(
            tmp_path, current, desired, apply_capability, allow_delete=True
        )


def _prepared_transaction(tmp_path):
    from aef.filesystem import (
        _apply_workspace_transaction, _transaction_write_capability,
    )

    source, desired, journal = _transaction_fixture()
    apply_workspace(tmp_path, load_workspace(tmp_path), source)
    current = load_workspace(tmp_path)
    prepared = deepcopy(current)
    prepared["files"][".agent/state/evaluation-transaction.json"] = deepcopy(journal)
    capability = _transaction_write_capability(tmp_path, journal, "prepare")
    _apply_workspace_transaction(tmp_path, current, prepared, capability)
    return load_workspace(tmp_path), desired, journal


@pytest.mark.parametrize("forgery", ["value", "other_file", "remove_journal"])
def test_apply_capability_rejects_every_noncanonical_plan_before_writer(
    tmp_path, monkeypatch, forgery
):
    import aef.filesystem as filesystem

    current, desired, journal = _prepared_transaction(tmp_path)
    target = journal["paths"][0]
    step = deepcopy(current)
    if forgery == "value":
        step["files"][target] = {"forged": True}
    elif forgery == "other_file":
        other = journal["paths"][1]
        step["files"][target] = deepcopy(desired["files"][other])
    else:
        step["files"].pop(".agent/state/evaluation-transaction.json")
    capability = filesystem._transaction_write_capability(
        tmp_path, journal, "apply", target_path=target
    )
    called = False

    def forbidden_writer(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("writer must not run")

    monkeypatch.setattr(filesystem, "_apply_workspace_unchecked", forbidden_writer)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }

    with pytest.raises(filesystem.WorkspacePathError):
        filesystem._apply_workspace_transaction(
            tmp_path, current, step, capability, allow_delete=True
        )

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert called is False
    assert after == before


def test_apply_capability_accepts_exact_canonical_content_regardless_of_key_order(
    tmp_path,
):
    from aef.filesystem import (
        _apply_workspace_transaction, _transaction_write_capability,
    )

    current, desired, journal = _prepared_transaction(tmp_path)
    target = journal["paths"][0]
    step = deepcopy(current)
    value = desired["files"][target]
    step["files"][target] = dict(reversed(list(value.items())))
    capability = _transaction_write_capability(
        tmp_path, journal, "apply", target_path=target
    )

    diff = _apply_workspace_transaction(tmp_path, current, step, capability)

    assert diff["modified"] == [target]
    assert load_workspace(tmp_path)["files"][target] == value


def _rollback_desired(current, journal):
    desired = deepcopy(current)
    for entry in journal["files"]:
        desired["files"][entry["path"]] = json.loads(entry["before_content"])
    desired["files"].pop(".agent/state/evaluation-transaction.json")
    return desired


@pytest.mark.parametrize(
    "forgery",
    ["forged", "after", "extra_removal", "keep_journal"],
)
def test_rollback_capability_rejects_noncanonical_final_plans(tmp_path, forgery):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    current, desired_state, journal = _prepared_transaction(tmp_path)
    target = journal["paths"][0]
    desired = _rollback_desired(current, journal)
    if forgery == "forged":
        desired["files"][target] = {"forged": True}
    elif forgery == "after":
        desired["files"][target] = deepcopy(desired_state["files"][target])
    elif forgery == "extra_removal":
        desired["files"].pop(".agent/manifest.json")
    else:
        desired["files"][".agent/state/evaluation-transaction.json"] = deepcopy(journal)
    capability = _transaction_write_capability(tmp_path, journal, "rollback")
    journal_path = tmp_path / ".agent/state/evaluation-transaction.json"
    journal_before = journal_path.read_bytes()

    with pytest.raises(WorkspacePathError):
        _apply_workspace_transaction(
            tmp_path, current, desired, capability, allow_delete=True
        )

    assert journal_path.read_bytes() == journal_before


@pytest.mark.parametrize("already_restored", [False, True])
def test_rollback_capability_accepts_exact_before_and_partial_recovery(
    tmp_path, already_restored
):
    from aef.filesystem import (
        _apply_workspace_transaction, _transaction_write_capability,
    )

    current, desired_state, journal = _prepared_transaction(tmp_path)
    target = journal["paths"][0]
    if not already_restored:
        step = deepcopy(current)
        step["files"][target] = deepcopy(desired_state["files"][target])
        apply_capability = _transaction_write_capability(
            tmp_path, journal, "apply", target_path=target
        )
        _apply_workspace_transaction(tmp_path, current, step, apply_capability)
        current = load_workspace(tmp_path)
    desired = _rollback_desired(current, journal)
    capability = _transaction_write_capability(tmp_path, journal, "rollback")

    _apply_workspace_transaction(
        tmp_path, current, desired, capability, allow_delete=True
    )

    restored = load_workspace(tmp_path)
    assert ".agent/state/evaluation-transaction.json" not in restored["files"]
    for entry in journal["files"]:
        assert restored["files"][entry["path"]] == json.loads(entry["before_content"])


@pytest.mark.parametrize("phase", ["cleanup", "commit"])
def test_journal_only_phases_reject_arbitrary_business_or_journal_content(
    tmp_path, phase
):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    current, _, journal = _prepared_transaction(tmp_path)
    desired = deepcopy(current)
    capability_journal = journal
    if phase == "cleanup":
        desired["files"].pop(".agent/state/evaluation-transaction.json")
        desired["files"][journal["paths"][0]] = {"forged": True}
        allow_delete = True
    else:
        desired["files"][".agent/state/evaluation-transaction.json"] = deepcopy(journal)
        desired["files"][".agent/state/evaluation-transaction.json"]["phase"] = "committed"
        desired["files"][".agent/state/evaluation-transaction.json"]["unexpected"] = True
        allow_delete = False
    capability = _transaction_write_capability(
        tmp_path, capability_journal, phase
    )

    with pytest.raises(WorkspacePathError):
        _apply_workspace_transaction(
            tmp_path, current, desired, capability, allow_delete=allow_delete
        )


def test_prepare_capability_rejects_arbitrary_journal_content_without_writing(tmp_path):
    from aef.filesystem import (
        WorkspacePathError, _apply_workspace_transaction,
        _transaction_write_capability,
    )

    source, _, journal = _transaction_fixture()
    desired = deepcopy(source)
    desired["files"][".agent/state/evaluation-transaction.json"] = deepcopy(journal)
    desired["files"][".agent/state/evaluation-transaction.json"]["unexpected"] = True
    capability = _transaction_write_capability(tmp_path, journal, "prepare")

    with pytest.raises(WorkspacePathError):
        _apply_workspace_transaction(tmp_path, source, desired, capability)

    assert not (tmp_path / ".agent").exists()
