"""Legacy map projection and edits tolerate partial state without partial writes."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    legacy_map_visuals,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    MAP_DATA_JSON_CLASS,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMapRecoveryStatus,
    DreameMowerChargingStatus,
    DreameMowerProperty,
    DreameMowerTaskStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager import (
    DreameMapMowerMapManager,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapImageDimensions,
    MapPixelType,
    Point,
    RecoveryMapInfo,
    Segment,
)


@pytest.fixture
def mower(monkeypatch):
    device = DreameMowerDevice("Map contracts", None, None)
    device.schedule_update = Mock()
    device._protocol.set_property = Mock()
    device._protocol.action_async = Mock()
    device._protocol.action = Mock()
    device._protocol.get_properties = Mock(return_value=[])
    device._map_manager = None
    device.capability.map_object_offset = False
    device.data[DreameMowerProperty.TASK_STATUS.value] = DreameMowerTaskStatus.COMPLETED
    device.data[DreameMowerProperty.CHARGING_STATUS.value] = (
        DreameMowerChargingStatus.CHARGING
    )
    device.data[DreameMowerProperty.MAP_RECOVERY_STATUS.value] = (
        DreameMapRecoveryStatus.SUCCESS.value
    )
    device.property_mapping[DreameMowerProperty.MAP_RECOVERY] = {"siid": 4, "piid": 5}
    try:
        yield device
    finally:
        device.disconnect()


def _map():
    data = MapData()
    data.map_id = 7
    data.frame_id = 1
    data.empty_map = False
    data.rotation = 0
    data.saved_map_status = 2
    data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
    data.pixel_type = np.full((4, 4), MapPixelType.FLOOR.value)
    data.data = bytes([MapPixelType.FLOOR.value] * 16)
    data.segments = {}
    return data


def _attach_map(mower, data):
    manager = DreameMapMowerMapManager(mower._protocol)
    manager.schedule_update = Mock()
    manager._map_data = data
    manager._selected_map_id = data.map_id
    manager._saved_map_data = {data.map_id: data}
    manager._map_list = [data.map_id]
    mower._map_manager = manager
    return manager


def test_render_projection_preserves_source_and_applies_grid_offset(mower):
    source = _map()
    projected = mower.get_map_for_render(source)
    assert projected is not source
    assert projected.dimensions.left == -25
    assert projected.dimensions.top == 25
    assert source.dimensions.left == source.dimensions.top == 0
    np.testing.assert_array_equal(projected.pixel_type, source.pixel_type)


def test_render_projection_waits_for_dimensions(mower):
    assert mower.get_map_for_render(MapData()) is None


def test_map_point_check_accepts_world_coordinates_with_fractional_offset():
    assert _map().check_point(75.5, 75.5)


def test_map_point_check_rejects_fractional_absolute_cell_index():
    assert not _map().check_point(1.5, 1, absolute=True)


def test_vslam_projection_keeps_current_geometry_without_selected_map(mower):
    source = _map()
    source.saved_map_status = 1
    manager = _attach_map(mower, source)
    manager._selected_map_id = None
    manager._saved_map_data = {}
    mower.capability.lidar_navigation = False
    projected = mower.get_map_for_render(source)
    np.testing.assert_array_equal(projected.pixel_type, source.pixel_type)
    assert projected.dimensions.width == 4


def test_docked_projection_preserves_unknown_heading(mower):
    source = _map()
    source.robot_position = Point(75, 75)
    projected = mower.get_map_for_render(source)
    assert projected.charger_position == source.robot_position
    assert projected.charger_position.a is None


def test_unknown_heading_projection_renders_positions_without_inventing_angle(
    mower, caplog
):
    source = _map()
    source.robot_position = Point(75, 75)
    projected = mower.get_map_for_render(source)
    png = legacy_map_visuals.render_legacy_map_png(projected)
    with Image.open(BytesIO(png)) as image:
        image.load()
        assert image.width > 1 and image.height > 1
        entities = json.loads(image.text[MAP_DATA_JSON_CLASS])["entities"]
    positions = {
        entity["type"]: entity
        for entity in entities
        if entity["type"] in {"robot_position", "charger_location"}
    }
    assert set(positions) == {"robot_position", "charger_location"}
    assert (
        positions["robot_position"]["points"]
        == positions["charger_location"]["points"]
    )
    assert all(entity["metaData"] == {} for entity in positions.values())
    assert projected.robot_position.a is projected.charger_position.a is None
    assert not [record for record in caplog.records if record.levelno >= 40]


@pytest.mark.parametrize("reply", [[{"code": 0}], "unexpected", 1])
def test_async_map_callback_rejects_opaque_reply_and_keeps_refresh(mower, reply):
    manager = _attach_map(mower, _map())
    manager.request_next_map = Mock()
    mower.update_map_data_async({"test": 1})
    callback = mower._protocol.action_async.call_args.args[0]
    callback(reply)
    mower.schedule_update.assert_called_once_with(5)
    manager.schedule_update.assert_called_with(3)


def test_history_projection_accepts_existing_cruise_marker(mower):
    source = _map()
    source.history_map = True
    source.task_cruise_points = True
    assert mower.get_map_for_render(source).task_cruise_points is True


def test_camera_projection_uses_coordinate_mapping_for_empty_points(mower):
    mower.capability.camera_streaming = True
    projected = mower.get_map_for_render(_map())
    assert projected.predefined_points == {}


def test_restricted_zone_edit_preserves_wire_fields_and_updates_local_geometry(mower):
    data = _map()
    _attach_map(mower, data)
    mower.update_map_data_async = Mock()
    walls = [[0, 0, 100, 100]]
    zones = [[0, 0, 100, 100]]
    no_mops = [[200, 200, 300, 300]]
    mower.set_restricted_zone(walls, zones, no_mops)
    assert len(data.virtual_walls) == len(data.no_go_areas) == 1
    mower.update_map_data_async.assert_called_once_with(
        {
            "vw": {"line": walls, "rect": zones, "mop": no_mops},
        }
    )


def test_rotation_without_selected_map_rejects_before_dispatch(mower):
    manager = _attach_map(mower, _map())
    manager._selected_map_id = None
    manager._saved_map_data = {}
    mower.update_map_data_async = Mock()
    with pytest.raises(InvalidActionException, match="Map ID"):
        mower.set_map_rotation(90)
    mower.update_map_data_async.assert_not_called()


@pytest.mark.parametrize("response", [None, [], {}, [{}], [{"code": 9}]])
def test_restore_rejected_acknowledgement_rolls_back_status(mower, response):
    _attach_map(mower, _map())
    mower._protocol.set_property.return_value = response
    with pytest.raises(InvalidActionException, match="Map recovery failed"):
        mower.restore_map_from_file("https://example.invalid/map", 7)
    assert mower.status.map_recovery_status == DreameMapRecoveryStatus.SUCCESS.value
    mower._protocol.get_properties.assert_not_called()


def test_restore_transport_exception_rolls_back_status(mower):
    _attach_map(mower, _map())
    mower._protocol.set_property.side_effect = RuntimeError("transport failed")
    with pytest.raises(RuntimeError, match="transport failed"):
        mower.restore_map_from_file("https://example.invalid/map", 7)
    assert mower.status.map_recovery_status == DreameMapRecoveryStatus.SUCCESS.value
    mower._protocol.get_properties.assert_not_called()


def test_restore_from_file_without_map_manager_keeps_successful_acknowledgement(mower):
    response = [{"code": 0}]
    mower._protocol.set_property.return_value = response
    assert mower.restore_map_from_file("https://example.invalid/map", 7) is response
    assert mower.status.map_recovery_status == DreameMapRecoveryStatus.RUNNING.value


def test_restore_missing_recovery_list_rejects_before_download(mower):
    _attach_map(mower, _map())
    mower.recovery_map_file = Mock()
    with pytest.raises(InvalidActionException, match="recovery map index"):
        mower.restore_map(1, 7)
    mower.recovery_map_file.assert_not_called()
    mower._protocol.set_property.assert_not_called()


@pytest.mark.parametrize("times,segments", [([1], [1, 2]), ([1, 1], [1, 99])])
def test_custom_cleaning_validates_complete_request_before_local_edits(
    mower, times, segments
):
    data = _map()
    data.segments = {1: Segment(1), 2: Segment(2)}
    data.cleanset = {"1": [1, 3, 2, 0], "2": [1, 3, 2, 0]}
    manager = _attach_map(mower, data)
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()
    with pytest.raises(InvalidActionException):
        mower.set_custom_cleaning(segments, times)
    assert data.cleanset["1"][2] == 2
    assert data.segments[1].cleaning_times is None
    mower.update_map_data_async.assert_not_called()
    manager.schedule_update.assert_not_called()


def test_custom_cleaning_without_map_uses_existing_payload(mower, monkeypatch):
    mower.capability.customized_cleaning = True
    monkeypatch.setattr(type(mower.capability), "map", property(lambda self: False))
    mower.update_map_data_async = Mock()
    mower.set_custom_cleaning([1], [2])
    mower.update_map_data_async.assert_called_once_with({"customeClean": [[1, 2]]})


def test_custom_cleaning_updates_selected_segments_and_retains_payload(mower):
    data = _map()
    data.segments = {1: Segment(1), 2: Segment(2)}
    data.cleanset = {"1": [1, 3, 1, 0], "2": [1, 3, 1, 0]}
    _attach_map(mower, data)
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()
    mower.set_custom_cleaning([1, 2], [2, 1])
    assert data.segments[1].cleaning_times == 2
    assert data.segments[2].cleaning_times == 1
    mower.update_map_data_async.assert_called_once_with(
        {"customeClean": [[1, 2], [2, 1]]}
    )


@pytest.mark.parametrize("method,args", [
    ("set_custom_cleaning", ([1], [2])),
    ("set_cleaning_sequence", ([1],)),
    ("set_segment_order", (1, 1)),
])
def test_missing_cleaning_settings_reject_request_without_default_or_null_command(
    mower, method, args
):
    data = _map()
    data.segments = {1: Segment(1)}
    manager = _attach_map(mower, data)
    manager._map_data_updated = Mock()
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()

    with pytest.raises(
        InvalidActionException, match="Cleaning settings are unavailable"
    ):
        getattr(mower, method)(*args)

    assert data.segments[1].order is data.segments[1].cleaning_times is None
    assert data.cleanset is None
    mower.update_map_data_async.assert_not_called()
    manager._map_data_updated.assert_not_called()


def test_custom_cleaning_checks_all_raw_rows_before_editing_first_segment(mower):
    data = _map()
    data.segments = {1: Segment(1), 2: Segment(2)}
    data.cleanset = {"1": [1, 3, 1, 0]}
    manager = _attach_map(mower, data)
    manager._map_data_updated = Mock()
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()

    with pytest.raises(
        InvalidActionException, match="Cleaning settings are unavailable"
    ):
        mower.set_custom_cleaning([1, 2], [2, 2])

    assert data.cleanset == {"1": [1, 3, 1, 0]}
    assert data.segments[1].cleaning_times is data.segments[2].cleaning_times is None
    mower.update_map_data_async.assert_not_called()
    manager._map_data_updated.assert_not_called()


def test_custom_cleaning_checks_mode_availability_before_any_times_edit(mower):
    data = _map()
    data.segments = {1: Segment(1, cleaning_mode=2), 2: Segment(2)}
    data.cleanset = {"1": [1, 3, 1, 0, 2], "2": [1, 3, 1, 0]}
    _attach_map(mower, data)
    mower.capability.customized_cleaning = True
    assert mower.capability.custom_cleaning_mode
    mower.update_map_data_async = Mock()

    with pytest.raises(
        InvalidActionException, match="Cleaning settings are unavailable"
    ):
        mower.set_custom_cleaning([1, 2], [2, 2], [1, 1])

    assert data.cleanset == {"1": [1, 3, 1, 0, 2], "2": [1, 3, 1, 0]}
    assert data.segments[1].cleaning_times is data.segments[2].cleaning_times is None
    assert data.segments[1].cleaning_mode == 2
    mower.update_map_data_async.assert_not_called()


@pytest.mark.parametrize("operation", ["sequence", "times", "mode"])
def test_grouped_cleaning_requires_all_saved_segments_in_current_map(mower, operation):
    data = _map()
    with_modes = operation == "mode"
    mode = 2 if with_modes else None
    data.segments = {1: Segment(1, cleaning_mode=mode)}
    row = [1, 3, 1, 0, 2] if with_modes else [1, 3, 1, 0]
    data.cleanset = {"1": row.copy(), "2": row.copy()}
    manager = _attach_map(mower, data)
    manager._map_data_updated = Mock()
    saved = _map()
    saved.segments = {1: Segment(1, cleaning_mode=mode), 2: Segment(2)}
    manager._saved_map_data = {7: saved}
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()

    with pytest.raises(
        InvalidActionException, match="Cleaning settings are unavailable"
    ):
        if operation == "sequence":
            mower.set_cleaning_sequence([1, 2])
        else:
            mower.set_custom_cleaning([1, 2], [2, 2], [1, 1] if with_modes else None)

    assert data.segments[1].cleaning_times is None
    assert data.segments[1].cleaning_mode == mode
    assert data.segments[1].order is None
    assert data.cleanset == {"1": row, "2": row}
    mower.update_map_data_async.assert_not_called()
    manager._map_data_updated.assert_not_called()


@pytest.mark.parametrize("method,args", [
    ("set_cleaning_sequence", ([],)),
    ("set_segment_order", (1, None)),
])
def test_clearing_known_cleaning_order_still_sends_an_empty_sequence(
    mower, method, args
):
    data = _map()
    data.segments = {1: Segment(1, order=1)}
    data.cleanset = {"1": [1, 3, 1, 1]}
    _attach_map(mower, data)
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()

    getattr(mower, method)(*args)

    assert data.cleanset["1"][3] == data.segments[1].order == 0
    mower.update_map_data_async.assert_called_once_with({"cleanOrder": []})


@pytest.mark.parametrize("method,args", [
    ("set_cleaning_sequence", ([],)),
    ("set_segment_order", (1, 0)),
])
def test_unavailable_editor_order_result_is_not_sent_as_null(mower, method, args):
    data = _map()
    _attach_map(mower, data)
    mower.capability.customized_cleaning = True
    mower.update_map_data_async = Mock()

    getattr(mower, method)(*args)

    mower.update_map_data_async.assert_not_called()


def test_split_segments_retains_caller_line_for_retry(mower):
    mower.update_map_data = Mock(return_value={"code": 0})
    line = [0, 0, 100, 100]
    mower.split_segments(7, 1, line)
    assert line == [0, 0, 100, 100]
    mower.update_map_data.assert_called_once_with(
        {"dsrid": [0, 0, 100, 100, 1], "mapid": 7}
    )


def test_merge_rejects_incomplete_pair_before_map_edit(mower):
    manager = _attach_map(mower, _map())
    manager.editor.merge_segments = Mock()
    mower.update_map_data = Mock()
    with pytest.raises(InvalidActionException, match="Two segments"):
        mower.merge_segments(7, [1])
    manager.editor.merge_segments.assert_not_called()
    mower.update_map_data.assert_not_called()


@pytest.mark.parametrize("index", [0, "0", -1])
def test_recovery_readers_reject_nonpositive_indices(mower, index):
    data = _map()
    data.recovery_map_list = [RecoveryMapInfo(7, {"info": "map-object"})]
    data.recovery_map_list[0].map_data = _map()
    manager = _attach_map(mower, data)
    manager._get_file_url = Mock()
    assert manager.get_recovery_map(7, index) is None
    assert manager.get_recovery_map_file(7, index) == (None, None, None)
    manager._get_file_url.assert_not_called()


def test_restore_keeps_numeric_string_index_and_selected_map_fallback(mower):
    data = _map()
    recovery = RecoveryMapInfo(7, {})
    recovery.object_name = "map-object"
    data.recovery_map_list = [recovery]
    manager = _attach_map(mower, data)
    manager.editor.restore_map = Mock()
    mower.recovery_map_file = Mock(
        return_value=(b"map", "https://example.invalid/map", "map-object")
    )
    response = [{"code": 0}]
    mower.restore_map_from_file = Mock(return_value=response)
    assert mower.restore_map("1") is response
    mower.recovery_map_file.assert_called_once()
    arguments = mower.recovery_map_file.call_args.args
    assert arguments[0] == 7 and int(arguments[1]) == 1
    manager.editor.restore_map.assert_called_once_with(recovery)
