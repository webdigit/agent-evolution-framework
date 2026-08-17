from aef.learning_signals import detect_learning_signals


def test_novel_situation_creates_observation_signal_not_rule():
    s, signals = detect_learning_signals([{"id":"E1","novel":True,"pattern_key":"p-new"}])
    assert s == "CHANGE"
    assert signals[0]["type"] == "novelty"
    assert signals[0]["recommended_action"] == "OBSERVE"


def test_repeated_help_requests_trigger_learning_gap_signal():
    events=[{"id":f"H{i}","kind":"help_request","pattern_key":"pricing"} for i in range(3)]
    _, signals = detect_learning_signals(events)
    sig = next(x for x in signals if x["type"] == "repeated_help")
    assert sig["evidence_ids"] == ["H0","H1","H2"]


def test_two_convergent_human_corrections_trigger_hypothesis_signal():
    events=[
        {"id":"C1","kind":"human_correction","pattern_key":"tone"},
        {"id":"C2","kind":"human_correction","pattern_key":"tone"},
    ]
    _, signals = detect_learning_signals(events)
    assert any(x["type"] == "convergent_corrections" for x in signals)


def test_single_human_correction_does_not_trigger_generalization_signal():
    _, signals = detect_learning_signals([{"id":"C1","kind":"human_correction","pattern_key":"tone"}])
    assert not any(x["type"] == "convergent_corrections" for x in signals)


def test_rule_mismatch_triggers_review_but_not_rule_replacement():
    _, signals = detect_learning_signals([{"id":"M1","kind":"rule_mismatch","rule_id":"rule:p"}])
    sig = next(x for x in signals if x["type"] == "rule_surprise")
    assert sig["recommended_action"] == "REVIEW_RULE"


def test_repeated_unexplained_successes_trigger_learning_signal():
    events=[{"id":f"S{i}","kind":"success","explained":False,"pattern_key":"shortcut"} for i in range(3)]
    _, signals = detect_learning_signals(events)
    assert any(x["type"] == "unexplained_success" for x in signals)


def test_single_unexplained_success_is_not_enough():
    _, signals = detect_learning_signals([{"id":"S1","kind":"success","explained":False,"pattern_key":"shortcut"}])
    assert not any(x["type"] == "unexplained_success" for x in signals)


def test_signal_detection_is_idempotent():
    events=[{"id":f"H{i}","kind":"help_request","pattern_key":"pricing"} for i in range(3)]
    s1, a = detect_learning_signals(events)
    s2, b = detect_learning_signals(events, a)
    assert s1 == "CHANGE" and s2 == "NO_CHANGE"
    assert a == b and len(a) == 1


def test_new_evidence_updates_existing_signal_without_duplication():
    events=[{"id":f"H{i}","kind":"help_request","pattern_key":"pricing"} for i in range(3)]
    _, a = detect_learning_signals(events)
    events.append({"id":"H3","kind":"help_request","pattern_key":"pricing"})
    s2, b = detect_learning_signals(events, a)
    assert s2 == "CHANGE"
    assert len(b) == 1
    assert b[0]["evidence_ids"] == ["H0","H1","H2","H3"]


def test_unrelated_noise_does_not_create_learning_signal():
    events=[{"id":"N1","kind":"read"},{"id":"N2","kind":"heartbeat"}]
    status, signals = detect_learning_signals(events)
    assert status == "NO_CHANGE" and signals == []
