"""Auto-switch transport failures restore the last known setting."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dreame_lawn_mower_client._loader import load_internal_module


@pytest.mark.parametrize("reply", ["success", "rejected", "exception"])
def test_auto_switch_write_retains_or_restores_cached_setting(reply):
    owner = load_internal_module("device").DreameMowerDevice
    types = load_internal_module("device_types")
    prop = types.DreameMowerAutoSwitchProperty.CLEANING_ROUTE
    device = object.__new__(owner)
    device.capability = SimpleNamespace(auto_switch_settings=True)
    device.auto_switch_data = {prop.name: 2}
    device._dirty_auto_switch_data = {}
    changes = []
    device._property_changed = lambda: changes.append(
        device.auto_switch_data[prop.name]
    )
    result = [{"code": 0 if reply == "success" else -1}]
    device.property_mapping = {
        types.DreameMowerProperty.AUTO_SWITCH_SETTINGS: {"siid": 4, "piid": 5}
    }
    transport = Mock(
        side_effect=OSError("transport unavailable") if reply == "exception" else None,
        return_value=result,
    )
    device._protocol = SimpleNamespace(set_property=transport)

    returned = device.set_auto_switch_property(prop, 1)

    transport.assert_called_once_with(
        4, 5, f'{{"k":"{prop.value}","v":1}}', retry_count=1
    )
    assert returned == (None if reply == "exception" else result)
    assert changes == ([1] if reply == "success" else [1, 2])
    assert device.auto_switch_data[prop.name] == (1 if reply == "success" else 2)
    assert (prop.name in device._dirty_auto_switch_data) is (reply == "success")
