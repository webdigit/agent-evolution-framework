from copy import deepcopy
from aef.learning_engine import ingest_events


def empty_state():
    return {"signals": [], "observations": [], "hypotheses": [], "rules": [], "principles": []}


def test_noise_does_not_mutate_learning_state():
    s = empty_state(); before=deepcopy(s)
    status, out = ingest_events(s, [{"id":"N1","kind":"heartbeat"}])
    assert status == "NO_CHANGE" and out == before


def test_learning_signal_becomes_observation_not_rule():
    status, out = ingest_events(empty_state(), [{"id":"E1","novel":True,"pattern_key":"new-case"}])
    assert status == "CHANGE"
    assert len(out["signals"]) == 1 and len(out["observations"]) == 1
    assert out["hypotheses"] == [] and out["rules"] == [] and out["principles"] == []


def test_replay_same_events_is_no_change():
    events=[{"id":f"H{i}","kind":"help_request","pattern_key":"pricing"} for i in range(3)]
    _, once = ingest_events(empty_state(), events)
    status, twice = ingest_events(once, events)
    assert status == "NO_CHANGE"
    assert once == twice


def test_more_evidence_updates_signal_without_duplicate_observation():
    first=[{"id":f"H{i}","kind":"help_request","pattern_key":"pricing"} for i in range(3)]
    _, state = ingest_events(empty_state(), first)
    second=first + [{"id":"H3","kind":"help_request","pattern_key":"pricing"}]
    status, state2 = ingest_events(state, second)
    assert status == "CHANGE"
    assert len(state2["signals"]) == 1
    assert len(state2["observations"]) == 1
    assert state2["signals"][0]["evidence_ids"][-1] == "H3"


def test_agent_does_not_create_principle_from_autonomous_detection():
    events=[]
    for i in range(4):
        events.append({"id":f"C{i}","kind":"human_correction","pattern_key":"tone"})
    _, out = ingest_events(empty_state(), events)
    assert out["principles"] == []
    assert out["rules"] == []
