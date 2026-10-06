"""Startup contracts for the mower client."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device as device_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DeviceUpdateFailedException,
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


def test_bounded_update_skips_reconnection_and_attempts_http_readback() -> None:
    readback_error = RuntimeError("stop after bounded readback dispatch")
    mower = SimpleNamespace(
        _update_running=False,
        _update_interval=10,
        cloud_connected=False,
        device_connected=False,
        connect_cloud=Mock(),
        connect_device=Mock(),
        capability=SimpleNamespace(backup_map=False),
        status=SimpleNamespace(active=False),
        _consumable_change=False,
        _last_settings_request=10**20,
        _map_manager=None,
        _protocol=SimpleNamespace(dreame_cloud=True),
        _request_properties=Mock(side_effect=readback_error),
    )

    mower._select_update_properties = lambda: (
        DreameMowerDevice._select_update_properties(mower)
    )

    with pytest.raises(
        DeviceUpdateFailedException,
        match="stop after bounded readback dispatch",
    ):
        DreameMowerDevice.update(
            mower,
            force_request_properties=True,
            deadline=123.0,
        )

    mower.connect_cloud.assert_not_called()
    mower.connect_device.assert_not_called()
    assert mower._request_properties.call_args.kwargs == {
        "deadline": 123.0,
        "require_fresh_state": True,
    }


def test_background_map_failure_still_schedules_next_refresh(monkeypatch) -> None:
    """A failed worker attempt must not permanently stop map updates."""
    manager = object.__new__(DreameMapMowerMapManager)
    update_timer = Mock()
    manager._update_timer = update_timer
    manager._update_interval = 30
    manager.update = Mock(side_effect=RuntimeError("temporary failure"))
    manager.schedule_update = Mock()
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager.time",
        Mock(time=Mock(side_effect=(100.0, 101.0))),
    )

    manager._update_task()

    update_timer.cancel.assert_called_once_with()
    manager.schedule_update.assert_called_once_with(29.0)


def test_authoritative_read_does_not_silently_skip_a_busy_device_update():
    mower = SimpleNamespace(_update_running=True, _update_interval=10)
    with pytest.raises(DeviceUpdateFailedException, match="another update"):
        DreameMowerDevice.update(mower, force_request_properties=True)
    assert DreameMowerDevice.update(mower) is None


def test_disconnect_quiesces_map_before_protocol_teardown() -> None:
    """A blocked protocol close must not leave the map timer alive."""
    order: list[str] = []
    mower = SimpleNamespace(
        disconnected=False,
        schedule_update=Mock(side_effect=lambda _wait: order.append("device")),
        _map_manager=SimpleNamespace(
            disconnect=Mock(side_effect=lambda: order.append("map")),
        ),
        _protocol=SimpleNamespace(
            disconnect=Mock(side_effect=lambda: order.append("protocol")),
        ),
        _property_changed=Mock(side_effect=lambda: order.append("property")),
    )

    DreameMowerDevice.disconnect(mower)

    assert mower.disconnected is True
    assert order == ["device", "map", "protocol", "property"]


@pytest.mark.parametrize("running", [False, True])
def test_poll_selection_preserves_settings_and_idle_map_cadence(monkeypatch, running):
    """Frequent state polls must not repeatedly request settings or idle map lists."""
    now = 100.0
    monkeypatch.setattr(device_module.time, "time", lambda: now)
    mower = SimpleNamespace(
        capability=SimpleNamespace(backup_map=False, dnd_task=False),
        status=SimpleNamespace(active=running, running=running),
        _consumable_change=False,
        _last_settings_request=90.0,
        _last_map_list_request=39.0,
        _map_manager=object(),
        _read_write_properties=[DreameMowerProperty.VOLUME],
    )

    first = DreameMowerDevice._select_update_properties(mower)
    assert DreameMowerProperty.STATE in first
    assert DreameMowerProperty.VOLUME in first
    assert DreameMowerProperty.DND in first
    assert (DreameMowerProperty.CLEANING_TIME in first) is running
    assert (DreameMowerProperty.MAP_LIST in first) is (not running)
    assert mower._last_settings_request == 100.0
    assert mower._last_map_list_request == (39.0 if running else 100.0)

    now = 105.0
    second = DreameMowerDevice._select_update_properties(mower)
    assert DreameMowerProperty.STATE in second
    assert DreameMowerProperty.VOLUME not in second
    assert DreameMowerProperty.DND not in second
    assert DreameMowerProperty.MAP_LIST not in second
    assert mower._last_settings_request == 100.0
