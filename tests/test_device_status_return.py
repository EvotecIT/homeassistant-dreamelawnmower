"""Returning from zone cleaning only restores existing temporary settings."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dreame_lawn_mower_client._loader import load_internal_module


@pytest.mark.parametrize("temporary", [None, False, "settings"])
def test_zone_return_without_temporary_settings(temporary):
    owner = load_internal_module("device_state")._DreameMowerDeviceStateMixin
    status_type = load_internal_module("device_types").DreameMowerStatus
    device = object.__new__(owner)
    settings = SimpleNamespace(stop=False) if temporary == "settings" else temporary
    device.status = SimpleNamespace(
        started=True,
        cleanup_started=True,
        cleanup_completed=False,
        go_to_zone=settings,
    )
    device.capability = SimpleNamespace(cruising=False)
    device._remote_control = False
    device.get_property = lambda prop: status_type.BACK_HOME.value
    device._restore_go_to_zone_plan = Mock(return_value=iter(()))
    device._map_manager = SimpleNamespace(editor=SimpleNamespace(refresh_map=Mock()))

    device._status_changed(status_type.ZONE_CLEANING.value)

    if temporary == "settings":
        assert settings.stop is True
        assert device.status.cleanup_started is False
        device._restore_go_to_zone_plan.assert_called_once_with(True)
    else:
        assert device.status.cleanup_started is True
        device._restore_go_to_zone_plan.assert_not_called()
    device._map_manager.editor.refresh_map.assert_called_once_with()
