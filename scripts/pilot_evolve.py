from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aef.filesystem import apply_workspace, load_workspace
from aef.operations import audit_project, consolidate_knowledge
from aef.career_cycle import career_cycle_step
from aef.competency_learning import ensure_competency
from aef.learning_lifecycle import observe, derive_hypothesis, confirm_hypothesis, derive_rule
from aef.release import apply_framework_release

LEDGER_PATH = ".agent/state/pilot-evolution.json"
CAREER_PATH = ".agent/state/career.json"
COMPETENCIES_PATH = ".agent/state/competencies.json"
KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"


def _fresh_agent(project):
    files = project.setdefault("files", {})
    career = deepcopy(files.get(CAREER_PATH))
    competencies = deepcopy(files.get(COMPETENCIES_PATH))
    if not isinstance(career, dict):
        career = {
            "level": "L1", "xp": 0, "cases": 0, "trust": None,
            "complex_cases": 0, "recent_significant_errors": 0,
            "probation": False,
        }
    if not isinstance(competencies, dict):
        competencies = {}
    return {"career": career, "competencies": competencies}


def _load_knowledge(project):
    value = deepcopy(project.get("files", {}).get(KNOWLEDGE_PATH))
    if not isinstance(value, dict):
        value = {"observations": [], "hypotheses": [], "rules": [], "principles": []}
    for key in ("observations", "hypotheses", "rules", "principles"):
        value.setdefault(key, [])
    return value


def _load_ledger(project):
    value = deepcopy(project.get("files", {}).get(LEDGER_PATH))
    if not isinstance(value, dict):
        value = {"scenario": "pilot-evolution-v1", "completed_phases": [], "events": []}
    value.setdefault("completed_phases", [])
    value.setdefault("events", [])
    return value


def _mark(ledger, phase, details):
    if phase not in ledger["completed_phases"]:
        ledger["completed_phases"].append(phase)
        ledger["events"].append({"phase": phase, "details": deepcopy(details)})


def _migration(mid, from_v, to_v, key):
    def transform(project):
        out = deepcopy(project)
        out.setdefault("files", {})[f".agent/state/{key}.json"] = {"version": to_v}
        return out

    return {
        "id": mid,
        "from_version": from_v,
        "to_version": to_v,
        "transform": transform,
        "postcondition": lambda p: p.get("files", {}).get(
            f".agent/state/{key}.json", {}
        ).get("version") == to_v,
    }


def run(workspace: Path) -> int:
    workspace = workspace.resolve()
    if not (workspace / ".agent" / "manifest.json").exists():
        print("ERROR: this workspace has not been initialized by AEF yet.")
        return 1

    project = load_workspace(workspace)
    initial_snapshot = deepcopy(project)
    ledger = _load_ledger(project)
    agent = _fresh_agent(project)
    knowledge = _load_knowledge(project)
    run_events = []

    # 1. Birth of a new competency.
    if "01-competency-birth" not in ledger["completed_phases"]:
        status, agent = ensure_competency(
            agent, "record-classification", title="Record classification", source="pilot"
        )
        assert status == "CHANGE"
        details = deepcopy(agent["competencies"]["record-classification"])
        _mark(ledger, "01-competency-birth", details)
        run_events.append(("01-competency-birth", status))

    # 2. Ten safe tasks -> local/global L2 under accelerated lab policy.
    if "02-onboarding-safe-tasks" not in ledger["completed_phases"]:
        supervised = 0
        for _ in range(10):
            result = career_cycle_step(
                agent,
                {"competency": "record-classification", "risk": "R0", "difficulty": "D3"},
                reward=1,
            )
            assert result["status"] == "COMPLETED"
            supervised += int(result["supervision_required"])
            agent = result["agent"]
        details = {
            "career_level": agent["career"]["level"],
            "competency_level": agent["competencies"]["record-classification"]["level"],
            "trust": agent["competencies"]["record-classification"]["trust"],
            "xp": agent["competencies"]["record-classification"]["xp"],
            "supervised_tasks": supervised,
        }
        _mark(ledger, "02-onboarding-safe-tasks", details)
        run_events.append(("02-onboarding-safe-tasks", "COMPLETED"))

    # 3. First R1 action after demonstrated local evidence.
    if "03-first-r1" not in ledger["completed_phases"]:
        result = career_cycle_step(
            agent,
            {"competency": "record-classification", "risk": "R1", "difficulty": "D2"},
            reward=1,
        )
        assert result["status"] == "COMPLETED"
        agent = result["agent"]
        _mark(ledger, "03-first-r1", {"permission": result["permission"]})
        run_events.append(("03-first-r1", result["status"]))

    # 4. Incident -> probation.
    if "04-incident-probation" not in ledger["completed_phases"]:
        for _ in range(2):
            result = career_cycle_step(
                agent,
                {"competency": "record-classification", "risk": "R0", "difficulty": "D2"},
                reward=-2,
                successful=False,
            )
            assert result["status"] == "COMPLETED"
            agent = result["agent"]
        assert agent["competencies"]["record-classification"]["probation"] is True
        _mark(ledger, "04-incident-probation", {
            "trust": agent["competencies"]["record-classification"]["trust"],
            "recent_significant_errors": agent["competencies"]["record-classification"]["recent_significant_errors"],
        })
        run_events.append(("04-incident-probation", "PROBATION"))

    # 5. Probation must reduce autonomy without mutating state.
    if "05-probation-gate" not in ledger["completed_phases"]:
        before = deepcopy(agent)
        blocked = career_cycle_step(
            agent,
            {"competency": "record-classification", "risk": "R1", "difficulty": "D2"},
            reward=1,
        )
        assert blocked["status"] == "REQUIRE_APPROVAL"
        assert blocked["agent"] == before
        _mark(ledger, "05-probation-gate", {"status": blocked["status"], "state_mutated": False})
        run_events.append(("05-probation-gate", blocked["status"]))

    # 6. Evidence-based recovery.
    if "06-recovery" not in ledger["completed_phases"]:
        for _ in range(2):
            result = career_cycle_step(
                agent,
                {"competency": "record-classification", "risk": "R0", "difficulty": "D2"},
                reward=2,
                successful=True,
                successful_recovery_cases=5,
            )
            assert result["status"] == "COMPLETED"
            agent = result["agent"]
        assert agent["competencies"]["record-classification"]["probation"] is False
        _mark(ledger, "06-recovery", {
            "trust": agent["competencies"]["record-classification"]["trust"],
            "recent_significant_errors": agent["competencies"]["record-classification"]["recent_significant_errors"],
        })
        run_events.append(("06-recovery", "COMPLETED"))

    # 7. Observation -> hypothesis -> rule.
    if "07-learning" not in ledger["completed_phases"]:
        observations = knowledge["observations"]
        hypotheses = knowledge["hypotheses"]
        rules = knowledge["rules"]

        for i in (1, 2):
            _, observations = observe(
                observations,
                observation_id=f"pilot-obs-{i}",
                summary="Ambiguous records need source verification",
                pattern_key="verify-ambiguous-source",
            )
        _, hypotheses, hypothesis_id = derive_hypothesis(
            observations, hypotheses, pattern_key="verify-ambiguous-source"
        )
        for _ in range(3):
            _, hypotheses = confirm_hypothesis(hypotheses, hypothesis_id)
        _, rules, rule_id = derive_rule(hypotheses, rules, hypothesis_id=hypothesis_id)

        knowledge["observations"] = observations
        knowledge["hypotheses"] = hypotheses
        knowledge["rules"] = rules
        _mark(ledger, "07-learning", {
            "hypothesis_id": hypothesis_id,
            "rule_id": rule_id,
            "observation_count": 2,
        })
        run_events.append(("07-learning", "RULE_FORMED"))

    # 8. Consolidate the rule into a contextual workflow rule.
    if "08-consolidate" not in ledger["completed_phases"]:
        rule_id = "rule:verify-ambiguous-source"
        status, consolidated, decisions = consolidate_knowledge(
            {"rules": knowledge["rules"]},
            rule_reviews=[{
                "rule_id": rule_id,
                "contradictions": 1,
                "contexts": [{"record_type": "ambiguous"}],
                "reason": "Extra verification is necessary only for ambiguous records",
                "evidence_ids": ["pilot-obs-1", "pilot-obs-2"],
            }],
        )
        assert status in {"CHANGE", "NO_CHANGE"}
        knowledge["rules"] = consolidated["rules"]
        rule = next(r for r in knowledge["rules"] if r["id"] == rule_id)
        assert rule["status"] == "specialized"
        _mark(ledger, "08-consolidate", {
            "decision": decisions[0]["decision"],
            "rule_status": rule["status"],
            "context": rule.get("context"),
        })
        run_events.append(("08-consolidate", status))

    # Persist evolution state to the real filesystem.
    desired = deepcopy(project)
    desired.setdefault("files", {})[CAREER_PATH] = deepcopy(agent["career"])
    desired["files"][COMPETENCIES_PATH] = deepcopy(agent["competencies"])
    desired["files"][KNOWLEDGE_PATH] = deepcopy(knowledge)
    desired["files"][LEDGER_PATH] = deepcopy(ledger)
    diff_evolution = apply_workspace(workspace, project, desired)

    # Reload, proving persistence rather than in-memory success.
    persisted = load_workspace(workspace)
    assert persisted["files"][CAREER_PATH] == agent["career"]
    assert persisted["files"][COMPETENCIES_PATH] == agent["competencies"]
    assert persisted["files"][KNOWLEDGE_PATH] == knowledge

    # 9. Audit must be read-only.
    before_audit = deepcopy(persisted)
    audit1 = audit_project(persisted)
    audit2 = audit_project(persisted)
    assert audit1 == audit2
    assert persisted == before_audit

    # 10. Real schema + managed-file upgrade on disk.
    migrations = [
        _migration("schema-100-110", "1.0.0", "1.1.0", "evaluation"),
        _migration("schema-110-120", "1.1.0", "1.2.0", "learning-signals"),
    ]
    status_upgrade, upgraded, meta_upgrade = apply_framework_release(
        persisted,
        target_version="1.2.0",
        migrations=migrations,
        managed_updates={".agent/core/learning.md": "# AEF Learning v1.2\n"},
    )
    assert status_upgrade in {"CHANGE", "NO_CHANGE"}
    diff_upgrade = apply_workspace(workspace, persisted, upgraded)

    # 11. Reload and replay upgrade: must be NO_CHANGE and no filesystem diff.
    reloaded = load_workspace(workspace)
    status_replay, replayed, meta_replay = apply_framework_release(
        reloaded,
        target_version="1.2.0",
        migrations=migrations,
        managed_updates={".agent/core/learning.md": "# AEF Learning v1.2\n"},
    )
    diff_replay = apply_workspace(workspace, reloaded, replayed)
    replay_pass = (
        status_replay == "NO_CHANGE"
        and diff_replay == {"created": [], "modified": [], "removed": []}
    )

    result = {
        "workspace": str(workspace),
        "result": "PASS" if replay_pass else "FAIL",
        "new_events_this_run": [{"phase": p, "status": s} for p, s in run_events],
        "evolution_write": diff_evolution,
        "audit": audit1,
        "upgrade": {"status": status_upgrade, "diff": diff_upgrade, "meta": meta_upgrade},
        "upgrade_replay": {"status": status_replay, "diff": diff_replay, "meta": meta_replay},
        "final": {
            "schema_version": reloaded["files"][".agent/manifest.json"]["schema_version"],
            "career": reloaded["files"][CAREER_PATH],
            "competency": reloaded["files"][COMPETENCIES_PATH]["record-classification"],
            "rule": next(
                r for r in reloaded["files"][KNOWLEDGE_PATH]["rules"]
                if r["id"] == "rule:verify-ambiguous-source"
            ),
            "completed_phases": reloaded["files"][LEDGER_PATH]["completed_phases"],
        },
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\nAEF evolution pilot: " + result["result"])
    return 0 if replay_pass else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    args = parser.parse_args()
    return run(Path(args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
