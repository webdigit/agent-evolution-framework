from aef.learning_lifecycle import observe, derive_hypothesis, confirm_hypothesis, derive_rule, derive_principle


def test_single_observation_does_not_become_hypothesis():
    obs=[]; hyps=[]
    _, obs = observe(obs, observation_id="O1", summary="A happened", pattern_key="p")
    status, hyps, hid = derive_hypothesis(obs, hyps, pattern_key="p")
    assert status == "INSUFFICIENT_EVIDENCE"
    assert hyps == [] and hid is None


def test_two_observations_can_create_one_stable_hypothesis():
    obs=[]; hyps=[]
    _, obs = observe(obs, observation_id="O1", summary="A", pattern_key="p")
    _, obs = observe(obs, observation_id="O2", summary="B", pattern_key="p")
    s1, hyps, hid = derive_hypothesis(obs, hyps, pattern_key="p")
    s2, hyps2, hid2 = derive_hypothesis(obs, hyps, pattern_key="p")
    assert s1 == "CHANGE" and s2 == "NO_CHANGE"
    assert hid == hid2 == "hypothesis:p"
    assert len(hyps2) == 1


def test_hypothesis_needs_three_confirmations_for_rule_by_default():
    obs=[]; hyps=[]; rules=[]
    for i in (1,2):
        _, obs = observe(obs, observation_id=f"O{i}", summary="x", pattern_key="p")
    _, hyps, hid = derive_hypothesis(obs, hyps, pattern_key="p")
    for _ in range(2):
        _, hyps = confirm_hypothesis(hyps, hid)
    status, rules, _ = derive_rule(hyps, rules, hypothesis_id=hid)
    assert status == "INSUFFICIENT_EVIDENCE"
    _, hyps = confirm_hypothesis(hyps, hid)
    status, rules, rid = derive_rule(hyps, rules, hypothesis_id=hid)
    assert status == "CHANGE" and rid == "rule:p"


def test_explicit_human_validation_can_promote_hypothesis_to_rule():
    obs=[]; hyps=[]; rules=[]
    for i in (1,2):
        _, obs = observe(obs, observation_id=f"O{i}", summary="x", pattern_key="p")
    _, hyps, hid = derive_hypothesis(obs, hyps, pattern_key="p")
    _, hyps = confirm_hypothesis(hyps, hid, explicit_human_validation=True)
    status, rules, rid = derive_rule(hyps, rules, hypothesis_id=hid)
    assert status == "CHANGE" and rid == "rule:p"


def test_principle_never_promotes_without_human_approval():
    obs=[]; hyps=[]; rules=[]; principles=[]
    for i in (1,2):
        _, obs = observe(obs, observation_id=f"O{i}", summary="x", pattern_key="p")
    _, hyps, hid = derive_hypothesis(obs, hyps, pattern_key="p")
    _, hyps = confirm_hypothesis(hyps, hid, explicit_human_validation=True)
    _, rules, rid = derive_rule(hyps, rules, hypothesis_id=hid)
    status, principles, pid = derive_principle(rules, principles, rule_id=rid, human_approved=False)
    assert status == "REQUIRE_HUMAN_APPROVAL" and principles == [] and pid is None


def test_principle_creation_is_idempotent_after_human_approval():
    obs=[]; hyps=[]; rules=[]; principles=[]
    for i in (1,2):
        _, obs = observe(obs, observation_id=f"O{i}", summary="x", pattern_key="p")
    _, hyps, hid = derive_hypothesis(obs, hyps, pattern_key="p")
    _, hyps = confirm_hypothesis(hyps, hid, explicit_human_validation=True)
    _, rules, rid = derive_rule(hyps, rules, hypothesis_id=hid)
    s1, principles, pid = derive_principle(rules, principles, rule_id=rid, human_approved=True)
    s2, principles2, pid2 = derive_principle(rules, principles, rule_id=rid, human_approved=True)
    assert s1 == "CHANGE" and s2 == "NO_CHANGE"
    assert pid == pid2 and len(principles2) == 1
