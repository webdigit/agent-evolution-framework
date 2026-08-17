from copy import deepcopy

from aef.operations import discover_capabilities, consolidate_knowledge


def test_discover_arbitrary_connector_and_replay():
    discovered = [{"id":"orbital-db","status":"available","capabilities":[
        {"id":"orbital-db.read","operation":"read","risk":"R0","reversible":True,"available":True}
    ]}]
    status, once = discover_capabilities({"connectors":[]}, discovered)
    assert status == "CHANGE"
    assert once["connectors"][0]["id"] == "orbital-db"
    status, twice = discover_capabilities(once, discovered)
    assert status == "NO_CHANGE"
    assert twice == once


def test_discovery_preserves_governance_annotations_when_runtime_metadata_refreshes():
    registry = {"connectors":[{"id":"x","status":"available","capabilities":[{
        "id":"x.delete","operation":"delete","risk":"R4","reversible":False,
        "minimum_level":"L5","hard_approval":True,"available":True
    }]}]}
    discovered = [{"id":"x","status":"available","capabilities":[{
        "id":"x.delete","operation":"remove","risk":"R0","reversible":True,"available":True
    }]}]
    _, out = discover_capabilities(registry, discovered)
    cap = out["connectors"][0]["capabilities"][0]
    assert cap["operation"] == "remove"
    assert cap["reversible"] is True
    # Discovery must never silently lower governance risk/approval annotations.
    assert cap["risk"] == "R4"
    assert cap["hard_approval"] is True


def test_disappeared_connector_is_preserved_as_unavailable():
    registry = {"connectors":[{"id":"x","status":"available","capabilities":[{
        "id":"x.read","operation":"read","risk":"R0","reversible":True,"available":True
    }]}]}
    status, out = discover_capabilities(registry, [])
    assert status == "CHANGE"
    assert out["connectors"][0]["status"] == "unavailable"
    assert out["connectors"][0]["capabilities"][0]["available"] is False


def test_discovery_recursively_merges_opaque_native_metadata_without_aliasing():
    registry = {
        "extension": {"preserved": True},
        "connectors": [{
            "id": "x", "status": "available", "connector_extension": "keep",
            "capabilities": [{
                "id": "x.read", "operation": "old", "risk": "R3",
                "reversible": False, "available": True,
                "capability_extension": {"keep": True},
                "native_metadata": {
                    "provider": {
                        "region": "eu",
                        "flags": {"existing": True, "replace": "old"},
                    },
                    "scalar": "old",
                    "items": ["old"],
                    "untouched": {"nested": "preserve"},
                },
            }],
        }],
    }
    discovered = [{
        "id": "x", "status": "available", "capabilities": [{
            "id": "x.read", "operation": "read", "risk": "R0",
            "reversible": True, "available": True,
            "native_metadata": {
                "provider": {"flags": {"replace": "new", "added": True}},
                "scalar": 42,
                "items": ["new"],
            },
        }],
    }]
    registry_before = deepcopy(registry)
    discovered_before = deepcopy(discovered)

    status, once = discover_capabilities(registry, discovered)
    replay_status, twice = discover_capabilities(once, discovered)

    assert status == "CHANGE"
    assert replay_status == "NO_CHANGE"
    assert twice == once
    capability = once["connectors"][0]["capabilities"][0]
    assert capability["native_metadata"] == {
        "provider": {
            "region": "eu",
            "flags": {"existing": True, "replace": "new", "added": True},
        },
        "scalar": 42,
        "items": ["new"],
        "untouched": {"nested": "preserve"},
    }
    assert capability["risk"] == "R3"
    assert capability["capability_extension"] == {"keep": True}
    assert once["connectors"][0]["connector_extension"] == "keep"
    assert once["extension"] == {"preserved": True}
    assert registry == registry_before
    assert discovered == discovered_before


def test_consolidate_specializes_rule_and_replay_is_no_change():
    state = {"rules":[{"id":"rule:r","type":"rule","status":"active","pattern_key":"r"}]}
    reviews = [{"rule_id":"rule:r","contradictions":2,"contexts":[{"market":"B2B"}],"reason":"B2C diverged"}]
    status, once, decisions = consolidate_knowledge(state, rule_reviews=reviews)
    assert status == "CHANGE"
    assert decisions[0]["decision"] == "SPECIALIZE"
    status, twice, decisions2 = consolidate_knowledge(once, rule_reviews=reviews)
    assert status == "NO_CHANGE"
    assert twice == once


def test_consolidate_without_new_evidence_is_no_change():
    state = {"rules":[{"id":"rule:r","type":"rule","status":"active","pattern_key":"r"}]}
    status, out, decisions = consolidate_knowledge(state, rule_reviews=[])
    assert status == "NO_CHANGE"
    assert out == state
    assert decisions == []
