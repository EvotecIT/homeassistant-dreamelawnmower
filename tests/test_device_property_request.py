"""Device RPC selection shared by synchronous and native async transports."""

import pytest

from dreame_lawn_mower_client._loader import load_internal_module

build_request = load_internal_module(
    "device_property_read"
).build_device_property_request
Property = load_internal_module("device_types").DreameMowerProperty


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("fresh", [False, True])
def test_request_keeps_task_evidence_and_excludes_actions(ready, fresh):
    mapping = {
        Property.STATE: {"siid": 2, "piid": 1},
        Property.STATUS: {"siid": 2, "piid": 2},
        Property.BATTERY_LEVEL: {"siid": 3, "piid": 1},
        Property.WARN_STATUS: {"siid": 3, "aiid": 1},
    }
    properties = [*mapping, Property.ERROR]
    result = build_request(
        properties, mapping, {Property.STATE.value: 0},
        ready=ready, require_fresh_state=fresh,
    )
    expected = [{"did": str(Property.STATE.value), "siid": 2, "piid": 1}]
    if not ready or fresh:
        expected.append({"did": str(Property.STATUS.value), "siid": 2, "piid": 2})
    if not ready:
        expected.append({
            "did": str(Property.BATTERY_LEVEL.value), "siid": 3, "piid": 1,
        })
    if fresh:
        expected.append({"did": "100001", "siid": 1, "piid": 1})
    assert result == expected
    assert mapping[Property.STATE] == {"siid": 2, "piid": 1}
