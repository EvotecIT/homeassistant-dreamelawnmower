"""Map state survives incomplete frames and malformed vendor snapshots."""

import base64
import json
import logging
import zlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_manager as manager_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager import (
    DreameMapMowerMapManager,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapDataPartial,
    MapFrameType,
    Point,
    RecoveryMapInfo,
    RecoveryMapType,
)


def _saved_frame(map_id=7, metadata=None, pixel=1):
    header = bytearray(27)
    header[0:2] = map_id.to_bytes(2, "little", signed=True)
    header[2:4] = (1).to_bytes(2, "little", signed=True)
    header[4] = MapFrameType.I.value
    header[17:19] = (50).to_bytes(2, "little", signed=True)
    header[19:21] = header[21:23] = (1).to_bytes(2, "little", signed=True)
    return base64.b64encode(zlib.compress(
        bytes(header) + bytes([pixel]) + json.dumps(metadata or {}).encode()
    )).decode()


def _manager_with_saved_map():
    manager = DreameMapMowerMapManager(Mock())
    saved = MapData()
    saved.map_id, saved.custom_name = 7, "Garden"
    saved.rotation = 90
    saved.recovery_map_list = [RecoveryMapInfo(7, {"first": 2, "time": 1700000000})]
    manager._saved_map_data[7] = saved
    manager._map_list = [7]
    manager._selected_map_id = 7
    manager._map_data = MapData()
    manager._need_map_list_request = manager._need_recovery_map_list_request = True
    manager._change_callback = Mock()
    return manager, saved


@pytest.mark.parametrize("payload", [
    b"\xff", b"[]", b"{}", b'{"mapstr":{},"curr_id":7}',
    b'{"mapstr":[{}],"curr_id":7}',
    json.dumps({"mapstr": [{"map": "bad-frame"}], "curr_id": 7}).encode(),
    json.dumps({
        "mapstr": [{"map": _saved_frame(), "angle": "bad"}], "curr_id": 7,
    }).encode(),
    json.dumps({
        "mapstr": [{"map": _saved_frame()}, {"map": "bad"}], "curr_id": 7,
    }).encode(),
    json.dumps({"mapstr": [{"map": _saved_frame()}]}).encode(),
])
def test_saved_map_snapshot_failure_preserves_cache_and_retry(payload):
    manager, saved = _manager_with_saved_map()
    manager._apply_map_list(payload)
    assert manager._saved_map_data == {7: saved}
    assert manager._map_list == [7] and manager._selected_map_id == 7
    assert manager._need_map_list_request is True
    manager._change_callback.assert_not_called()


def test_saved_map_snapshot_can_replace_or_explicitly_clear_cache():
    manager, _saved = _manager_with_saved_map()
    manager._apply_map_list(json.dumps({
        "mapstr": [{"map": _saved_frame(8), "name": "Front garden", "angle": 180}],
        "curr_id": 8,
    }).encode())
    assert manager.map_list == [8]
    assert manager.selected_map.map_id == 8
    assert manager.selected_map.custom_name == "Front garden"
    assert manager.selected_map.rotation == 180
    assert manager._need_map_list_request is False
    manager._change_callback.assert_called_once()
    manager._apply_map_list(b'{"mapstr":[],"curr_id":0}')
    assert manager.map_list == [] and manager.selected_map is None
    assert manager._selected_map_id is None


@pytest.mark.parametrize("payload", [
    b"\xff", b"{}", b'[{}]', b'[{"id":7,"info":null}]',
    b'[{"id":7,"info":[null]}]',
    b'[{"id":7,"info":[{"time":"bad"}]}]',
    b'[{"id":7,"info":[{"objname":17}]}]',
    b'[{"id":7,"info":[{"objname":["bad"]}]}]',
    b'[{"id":7,"info":[]},{"id":8,"info":[{"time":"bad"}]}]',
])
def test_recovery_snapshot_failure_preserves_all_maps_and_retry(payload):
    manager, saved = _manager_with_saved_map()
    other = MapData()
    other.recovery_map_list = [RecoveryMapInfo(8, {"first": 1})]
    manager._saved_map_data[8] = other
    manager._map_list.append(8)
    original = saved.recovery_map_list
    manager._apply_recovery_map_list(payload)
    assert saved.recovery_map_list is original
    assert other.recovery_map_list[0].map_type == RecoveryMapType.ORIGINAL
    assert manager._need_recovery_map_list_request is True
    manager._change_callback.assert_not_called()


def test_recovery_snapshot_keeps_vendor_order_and_labels_edited_map_first():
    manager, saved = _manager_with_saved_map()
    manager._apply_recovery_map_list(json.dumps([{"id": 7, "info": [
        {"first": 2}, {"first": 0}, {"first": 1},
    ]}]).encode())
    assert [info.map_type for info in saved.recovery_map_list] == [
        RecoveryMapType.EDITED, RecoveryMapType.BACKUP, RecoveryMapType.ORIGINAL,
    ]
    assert [info.map_index for info in saved.recovery_map_list] == [1, 2, 3]
    assert saved.recovery_map_list[0].map_name == "Garden Recovery Map 1 (Edited)"
    assert manager._need_recovery_map_list_request is False


@pytest.mark.parametrize("with_saved_map", [False, True])
def test_vslam_docking_updates_only_available_selected_map(with_saved_map):
    manager, saved = _manager_with_saved_map()
    manager._vslam_map = True
    manager._device_docked = False
    manager._map_data.saved_map_status = 1
    saved.data = b"saved-raster"
    saved.charger_position = Point(50, 100, 90)
    if not with_saved_map:
        manager._saved_map_data.clear()
        manager._map_list.clear()
    manager.schedule_update = Mock()
    manager.set_device_running(False, True)
    assert manager._device_docked is True
    manager.schedule_update.assert_called_once_with(2)
    if with_saved_map:
        assert manager._map_data.data == b"saved-raster"
        assert manager._map_data.robot_position == saved.charger_position
        assert manager._map_data.saved_map_status == 2 and manager._map_data.docked
        manager._change_callback.assert_called_once()
    else:
        assert manager._map_data.saved_map_status == 1
        manager._change_callback.assert_not_called()


@pytest.mark.parametrize("robot_time", [None, 1700000000000])
def test_initial_response_with_optional_robot_time_is_usable_and_private(
    caplog, robot_time,
):
    manager = DreameMapMowerMapManager(Mock())
    manager._map_request_time = 123
    manager._last_robot_time = robot_time
    caplog.set_level(logging.DEBUG)
    secret_name = "map-object,private-map-aes-key"
    name, raw = manager._read_i_map_response(
        {"code": 0, "out": [{"piid": 3, "value": secret_name}]}, None,
    )
    assert name == secret_name and raw is None
    assert manager._map_request_time is None and manager._last_robot_time == robot_time
    manager._get_object_file_data = Mock(return_value=(None, None))
    manager._add_cloud_map_data(None, secret_name, None)
    assert "private-map-aes-key" not in caplog.text


def test_timestampless_frame_does_not_replace_known_newer_map():
    manager = DreameMapMowerMapManager(Mock())
    manager._latest_map_timestamp_ms = 1700000000000
    manager._latest_map_id = 9
    partial = manager._decode_map_partial(_saved_frame(7))
    assert partial is not None and partial.timestamp_ms is None
    assert manager._latest_map_id == 9
    assert manager._latest_map_timestamp_ms == 1700000000000


def test_older_frame_without_time_does_not_crash_or_replace_current_map():
    manager = DreameMapMowerMapManager(Mock())
    manager._latest_map_id = manager._current_map_id = 7
    manager._current_frame_id = 10
    manager._current_timestamp_ms = 1700000000000
    current = manager._map_data = MapData()
    partial = MapDataPartial()
    partial.map_id, partial.frame_id, partial.frame_type = 7, 1, MapFrameType.I.value
    assert manager._add_map_data(partial) is True
    assert manager._map_data is current and manager._current_frame_id == 10


def test_cached_file_url_uses_expiry_and_failed_download_is_retryable(monkeypatch):
    cloud = SimpleNamespace(
        logged_in=True,
        get_interim_file_url=Mock(return_value="https://example.invalid/map?sig=fake"),
        get_file=Mock(side_effect=[None, b"map"]),
    )
    manager = DreameMapMowerMapManager(SimpleNamespace(cloud=cloud))
    monkeypatch.setattr(manager_module.time, "time", lambda: 1000)
    assert manager._get_file_url("map") == "https://example.invalid/map?sig=fake"
    assert manager._get_file_url("map").endswith("&current=1000")
    cloud.get_interim_file_url.assert_called_once()
    assert manager._get_interim_file_data("map") is None
    assert manager._get_interim_file_data("map") == b"map"
    assert cloud.get_interim_file_url.call_count == 2


def test_empty_frame_reset_preserves_device_decoder_configuration():
    manager = DreameMapMowerMapManager(Mock())
    capability = SimpleNamespace(lidar_navigation=False)
    manager.set_aes_iv("fixture-iv")
    manager.set_capability(capability)
    assert manager._add_raw_map_data(_saved_frame(metadata={"ris": 0}, pixel=0))
    assert manager._map_data.empty_map is True
    assert manager._aes_iv == "fixture-iv"
    assert manager._capability is capability
    assert manager._vslam_map is True
