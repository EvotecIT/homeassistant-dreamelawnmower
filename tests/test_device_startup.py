"""Startup contracts for the mower client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device as device_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager import (
    DreameMapMowerMapManager,
)


def test_connect_device_defers_initial_map_request(monkeypatch) -> None:
    """The first state snapshot must not wait for cloud map acquisition."""
    monkeypatch.setattr(
        device_module,
        "DreameMowerDeviceInfo",
        lambda _info: SimpleNamespace(
            mac_address="00:00:00:00:00:00",
            model="dreame.mower",
            firmware_version="1.0",
        ),
    )
    map_manager = Mock()
    mower = SimpleNamespace(
        _protocol=SimpleNamespace(
            connect=Mock(return_value={"connected": True}),
            cloud=object(),
        ),
        _message_callback=Mock(),
        _connected_callback=Mock(),
        _request_properties=Mock(),
        _property_changed=Mock(),
        _map_manager=map_manager,
        _map_update_interval=10,
        _ready=False,
        available=False,
        device_connected=True,
        cloud_connected=False,
        mac=None,
        status=SimpleNamespace(
            running=False,
            docked=True,
            started=False,
            current_map=None,
        ),
        capability=SimpleNamespace(),
    )

    DreameMowerDevice.connect_device(mower)

    mower._request_properties.assert_called_once_with()
    map_manager.set_update_interval.assert_called_once_with(10)
    map_manager.schedule_update.assert_not_called()
    map_manager.update.assert_not_called()
    assert mower.available is True
    assert mower._ready is True


def test_background_map_failure_still_schedules_next_refresh(monkeypatch) -> None:
    """A failed worker attempt must not permanently stop map updates."""
    manager = object.__new__(DreameMapMowerMapManager)
    update_timer = Mock()
    manager._update_timer = update_timer
    manager._update_interval = 30
    manager.update = Mock(side_effect=RuntimeError("temporary failure"))
    manager.schedule_update = Mock()
    timestamps = iter((100.0, 101.0))
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager.time.time",
        lambda: next(timestamps),
    )

    manager._update_task()

    update_timer.cancel.assert_called_once_with()
    manager.schedule_update.assert_called_once_with(29.0)
