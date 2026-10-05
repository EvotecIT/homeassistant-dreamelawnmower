"""Startup contracts for the mower client."""

from __future__ import annotations

import json
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
    DreameMowerAutoSwitchProperty,
    DreameMowerCleaningMode,
    DreameMowerCleaningRoute,
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


@pytest.mark.parametrize(
    ("payload", "accepted"),
    [
        ({"privacyAuthed": True}, True),
        ({"privacyAuthed": False}, False),
        ({"aiPrivacyAuthed": True}, True),
        ({"aiPrivacyAuthed": False}, False),
        ({"privacyAuthed": False, "aiPrivacyAuthed": True}, False),
    ],
)
def test_connect_device_updates_ai_policy_status(
    monkeypatch, payload, accepted,
) -> None:
    """Cloud readback updates the flag used by AI command authorization."""
    cloud = Mock(connected=True)
    cloud.get_batch_device_datas.return_value = {
        "prop.s_ai_config": json.dumps(payload)
    }
    protocol = Mock(cloud=cloud, connected=True, dreame_cloud=True)
    protocol.connect.return_value = {
        "model": "dreame.mower.g2408",
        "fw_ver": "4.3.6_0320",
    }
    monkeypatch.setattr(
        device_module, "DreameMowerProtocol", Mock(return_value=protocol)
    )
    monkeypatch.setattr(device_module, "DreameMapMowerMapManager", Mock())
    mower = DreameMowerDevice("Test mower", "192.0.2.1", "test-token")
    mower._map_manager = None
    mower._request_properties = Mock()
    mower.status.ai_policy_accepted = not accepted

    mower.connect_device()

    assert mower.status.ai_policy_accepted is accepted
    cloud.get_batch_device_datas.assert_called_once_with(["prop.s_ai_config"])


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        (0, DreameMowerCleaningMode.MOWING),
        (-1, DreameMowerCleaningMode.UNKNOWN),
        (17, None),
        (None, None),
        (False, None),
        ("0", None),
    ],
)
def test_cleaning_mode_tracks_reported_property(
    monkeypatch, reported, expected,
) -> None:
    """Known modes reach status; unsupported or missing values stay unavailable."""
    monkeypatch.setattr(
        device_module, "DreameMowerProtocol", Mock(return_value=Mock(cloud=None))
    )
    mower = DreameMowerDevice("Test mower", "192.0.2.1", "test-token")
    mower.data[DreameMowerProperty.CLEANING_MODE] = reported
    mower.status.cleaning_mode = (
        DreameMowerCleaningMode.UNKNOWN
        if expected is DreameMowerCleaningMode.MOWING
        else DreameMowerCleaningMode.MOWING
    )

    mower._cleaning_mode_changed()

    assert mower.status.cleaning_mode is expected


@pytest.mark.parametrize(
    ("reported_mode", "route", "normalize"),
    [
        (0, DreameMowerCleaningRoute.QUICK, False),
        (0, DreameMowerCleaningRoute.STANDARD, False),
        (0, DreameMowerCleaningRoute.DEEP, True),
        (0, DreameMowerCleaningRoute.INTENSIVE, True),
        (0, DreameMowerCleaningRoute.UNKNOWN, False),
        (0, DreameMowerCleaningRoute.NOT_SET, False),
        (0, None, False),
        (0, 17, False),
        (17, DreameMowerCleaningRoute.DEEP, False),
        (None, DreameMowerCleaningRoute.DEEP, False),
        (-1, DreameMowerCleaningRoute.DEEP, False),
    ],
)
def test_mowing_mode_only_resets_an_unsupported_route(
    monkeypatch, reported_mode, route, normalize,
) -> None:
    """Only a known incompatible route on a known mowing mode may be changed."""
    monkeypatch.setattr(
        device_module, "DreameMowerProtocol", Mock(return_value=Mock(cloud=None))
    )
    mower = DreameMowerDevice("Test mower", "192.0.2.1", "test-token")
    mower._ready = True
    mower.capability.cleaning_route = True
    mower.capability.auto_switch_settings = True
    mower.auto_switch_data = (
        {} if route is None
        else {DreameMowerAutoSwitchProperty.CLEANING_ROUTE.name: route}
    )
    mower.data[DreameMowerProperty.CLEANING_MODE] = reported_mode
    mower.status.cleaning_mode = (
        DreameMowerCleaningMode.UNKNOWN
        if reported_mode == 0 else DreameMowerCleaningMode.MOWING
    )
    mower.set_auto_switch_property = Mock()

    mower._cleaning_mode_changed()

    if normalize:
        mower.set_auto_switch_property.assert_called_once_with(
            DreameMowerAutoSwitchProperty.CLEANING_ROUTE,
            DreameMowerCleaningRoute.STANDARD.value,
        )
    else:
        mower.set_auto_switch_property.assert_not_called()


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
