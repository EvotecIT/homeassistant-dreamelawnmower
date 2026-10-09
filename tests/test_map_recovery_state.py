"""Recovery preserves saved maps when cloud data is unavailable."""

import base64
import json
import zlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_manager as map_manager_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_map import (
    _DreameMowerDeviceMapMixin,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMapMowerMapManager,
    DreameMowerMapDecoder,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    CleaningHistory,
    MapData,
    RecoveryMapInfo,
)


class _DummyProtocol:
    """No network calls are needed for local recovery state."""


def _map_payload(metadata=None):
    header = bytearray(DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, byteorder="little", signed=True)
    header[4] = 73
    header[17:19] = (50).to_bytes(2, byteorder="little", signed=True)
    header[19:21] = (1).to_bytes(2, byteorder="little", signed=True)
    header[21:23] = (1).to_bytes(2, byteorder="little", signed=True)
    return base64.b64encode(zlib.compress(
        bytes(header) + b"\x01" + json.dumps(metadata or {}).encode()
    )).decode()


@pytest.mark.parametrize("timestamp", [None, 0, 1700000000])
def test_recovery_map_getter_initializes_metadata_before_caching(timestamp):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    saved = MapData()
    saved.rotation = 90
    metadata = {"thb": _map_payload(), "first": 0}
    if timestamp is not None:
        metadata["time"] = timestamp
    recovery = RecoveryMapInfo(7, metadata)
    saved.recovery_map_list = [recovery]
    manager._map_list = [7]
    manager._saved_map_data = {7: saved}

    recovered = manager.get_recovery_map(7, 1)

    assert recovered is not None
    assert recovered.map_id == 7
    assert recovered.rotation == 90
    assert recovered.last_updated == timestamp
    assert recovered.recovery_map is True
    assert recovered.recovery_map_type == recovery.map_type
    assert recovery.map_data is recovered
    assert manager.get_recovery_map(7, 1) is recovered


@pytest.mark.parametrize("raw_map", [None, "invalid-map", "x", 37, [], {}])
def test_recovery_map_getter_keeps_failed_decode_retryable(raw_map):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    saved = MapData()
    recovery = RecoveryMapInfo(7, {"thb": raw_map, "time": 1700000000})
    if not isinstance(raw_map, str):
        assert recovery.raw_map is None
    saved.recovery_map_list = [recovery]
    manager._map_list = [7]
    manager._saved_map_data = {7: saved}

    assert manager.get_recovery_map(7, 1) is None
    assert recovery.map_data is None
    assert manager._saved_map_data[7] is saved
    recovery.raw_map = _map_payload()
    recovered = manager.get_recovery_map(7, 1)
    assert recovered is recovery.map_data
    assert recovered.recovery_map is True
    assert recovered.last_updated == 1700000000


@pytest.mark.parametrize("timestamp", [None, 0, 1700000000])
@pytest.mark.parametrize("cruising", [False, True])
def test_history_map_preserves_optional_date_in_main_and_cleaning_map(
    timestamp, cruising,
):
    cloud = SimpleNamespace(
        dreame_cloud=True,
        get_file=Mock(return_value=_map_payload({
            "iscleanlog": 1, "decmap": "",
        }).encode()),
    )
    manager = DreameMapMowerMapManager(SimpleNamespace(cloud=cloud))
    manager._get_file_url = Mock(return_value="https://example.invalid/map")
    mapping = {DreameMowerProperty.CLEAN_LOG_FILE_NAME: {"piid": 7}}
    properties = [{"piid": 7, "value": "map-object"}]
    if timestamp is not None:
        mapping[DreameMowerProperty.CLEANING_START_TIME] = {"piid": 8}
        properties.append({"piid": 8, "value": timestamp})
    history = CleaningHistory(properties, mapping)
    device = SimpleNamespace(
        capability=SimpleNamespace(map=True),
        status=SimpleNamespace(
            _cruising_history=[history] if cruising else [],
            _cleaning_history=[] if cruising else [history],
            _history_map_data={},
        ),
        _map_manager=manager,
    )

    recovered = _DreameMowerDeviceMapMixin.history_map(device, 1, cruising)

    assert recovered is not None
    assert recovered.history_map is True
    assert recovered.last_updated == timestamp
    assert recovered.cleaning_map_data is not None
    assert recovered.cleaning_map_data.last_updated == timestamp
    assert _DreameMowerDeviceMapMixin.history_map(device, 1, cruising) is recovered
    cloud.get_file.assert_called_once()


@pytest.mark.parametrize("raw_map", [None, "invalid-map", "x", 37, [], {}])
def test_restore_map_keeps_saved_state_when_recovery_cannot_decode(raw_map):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    saved = MapData()
    saved.rotation = 0
    manager._map_list = [7]
    manager._saved_map_data = {7: saved}
    manager.schedule_update = Mock()
    manager._map_data_updated = Mock()
    recovery = RecoveryMapInfo(7, {"thb": raw_map})

    manager.editor.restore_map(recovery)

    assert manager._saved_map_data[7] is saved
    manager.schedule_update.assert_called_once_with(15)
    manager._map_data_updated.assert_not_called()


@pytest.mark.parametrize("wifi_flag", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_restore_map_updates_embedded_wifi_data_and_preserves_saved_metadata(
    monkeypatch, wifi_flag, selected,
):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    saved = MapData()
    saved.rotation = 90
    saved.custom_name = "Garden"
    saved.map_name = "Garden map"
    saved.map_index = 2
    saved.timestamp_ms = 123
    manager._map_list = [7]
    manager._saved_map_data = {7: saved}
    if selected:
        manager._selected_map_id = 7
    manager.schedule_update = Mock()
    manager._map_data_updated = Mock()
    recovery = RecoveryMapInfo(7, {})
    restored = MapData()
    restored.wifi_map = wifi_flag
    restored.wifi_map_data = MapData()
    recovery.map_data = restored
    monkeypatch.setattr(map_manager_module.time, "time", lambda: 1000.0)

    manager.editor.restore_map(recovery)

    assert manager._saved_map_data[7] is restored
    assert restored.saved_map is True and restored.recovery_map is False
    assert (restored.rotation, restored.custom_name, restored.map_name) == (
        90, "Garden", "Garden map"
    )
    assert (restored.map_index, restored.timestamp_ms) == (2, 123)
    assert restored.last_updated == restored.wifi_map_data.last_updated == 1000.0
    assert manager._need_map_request and manager._need_map_list_request
    if selected:
        assert manager._map_data.restored_map is True
