"""Wire decoding either produces complete map state or preserves the last frame."""

import base64
import json
import zlib

import numpy as np
import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    ObstacleType,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_decoder import (
    DreameMowerMapDecoder as Decoder,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapDataPartial,
    MapFrameType,
    MapImageDimensions,
    MapPixelType,
    Point,
    Segment,
)


def _wire(
    *, width=2, height=2, grid=50, left=0, pixels=None, metadata=None,
    frame=MapFrameType.I.value, frame_id=2,
):
    header = bytearray(Decoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, "little", signed=True)
    header[2:4] = frame_id.to_bytes(2, "little", signed=True)
    header[4] = frame
    for offset, value in ((17, grid), (19, width), (21, height), (23, left)):
        header[offset:offset + 2] = value.to_bytes(2, "little", signed=True)
    if pixels is None:
        pixels = bytes([1] * max(0, width * height))
    suffix = json.dumps(metadata).encode() if metadata is not None else b""
    return base64.b64encode(zlib.compress(bytes(header) + pixels + suffix)).decode()


@pytest.mark.parametrize("payload", ["", "invalid-base64", "eA=="])
def test_failed_wire_decode_keeps_pair_result_contract(payload):
    assert Decoder.decode_map(payload, False) == (None, None)
    assert Decoder.decode_saved_map(payload, False) is None


@pytest.mark.parametrize("raw", [None, b"\x01"])
def test_partial_without_a_complete_header_returns_failed_pair(raw):
    partial = MapDataPartial()
    partial.raw = raw
    assert Decoder.decode_map_data_from_partial(partial, False) == (None, None)


@pytest.mark.parametrize("width,height,grid,pixels", [
    (2, 2, 50, b"\x01"),
    (-1, 1, 50, b""),
    (1, 1, 0, b"\x01"),
])
def test_invalid_raster_header_or_truncated_pixels_cannot_publish_partial_map(
    width, height, grid, pixels
):
    payload = _wire(width=width, height=height, grid=grid, pixels=pixels)
    assert Decoder.decode_map(payload, False) == (None, None)


def test_parse_failure_does_not_return_partially_populated_map():
    payload = _wire(metadata={"cs": "invalid-number", "ris": 2})
    assert Decoder.decode_map(payload, False) == (None, None)


def test_valid_empty_frame_with_history_metadata_has_no_cleaning_overlay(caplog):
    data, saved = Decoder.decode_map(
        _wire(width=0, height=0, grid=0, metadata={"ris": 2, "multime": 2}),
        False,
    )
    assert data is not None and data.empty_map
    assert data.cleaning_map_data is None
    assert saved is None
    assert "Map Parse Failed" not in caplog.text


def test_four_column_obstacle_retains_map_and_location_without_optional_photo():
    data, _ = Decoder.decode_map(_wire(metadata={
        "ai_obstacle": [[25, 30, ObstacleType.OBSTACLE.value, 0.75]],
    }), False)
    assert data is not None
    obstacle = data.obstacles["1"]
    assert (obstacle.x, obstacle.y, obstacle.possibility) == (25, 30, 75)
    assert obstacle.object_id is None and obstacle.file_name is None


def test_decoder_debug_logs_omit_inline_keys_and_raw_photo_metadata(caplog):
    caplog.set_level("DEBUG", logger=Decoder.__module__)
    photo_key, inline_key = "synthetic-photo-secret", "synthetic-inline-map-secret"
    payload = _wire(metadata={"ai_obstacle": [[
        25, 30, ObstacleType.OBSTACLE.value, 0.75, 1001, photo_key, "photo.jpg",
    ]]})

    data, _ = Decoder.decode_map(payload, False)
    assert data is not None and data.obstacles["1"].key == photo_key
    Decoder.decode_map_partial("eA==," + inline_key, iv="0123456789abcdef")

    assert photo_key not in caplog.text
    assert inline_key not in caplog.text
    assert payload not in caplog.text


def test_parser_failure_logs_do_not_echo_private_metadata(caplog):
    secret = "synthetic-private-metadata"
    assert Decoder.decode_map(_wire(metadata={"cs": secret}), False) == (None, None)
    assert "ValueError" in caplog.text and secret not in caplog.text


@pytest.mark.parametrize("length", [8, 9, 10])
def test_incomplete_expanded_photo_record_does_not_publish_partial_map(length):
    photo = [25, 30, ObstacleType.OBSTACLE.value, 0.75, 1001,
             "photo.jpg", "synthetic-key", 0, 0, 0.1]
    payload = _wire(metadata={"ai_obstacle": [photo[:length]]})
    assert Decoder.decode_map(payload, False) == (None, None)


def _current_map():
    data = MapData()
    data.map_id, data.frame_id = 7, 1
    data.saved_map_status = 2
    data.saved_map, data.empty_map = True, False
    data.dimensions = MapImageDimensions(0, 0, 2, 2, 50)
    data.data = bytes([1, 1, 1, 1])
    data.pixel_type = np.ones((2, 2), dtype=np.uint8)
    data.robot_position = Point(25, 25, 0)
    data.segments = {1: Segment(1, 0, -50, 100, 50)}
    return data


@pytest.mark.parametrize("missing", ["dimensions", "data", "pixel_type"])
def test_delta_with_incomplete_base_preserves_previous_frame(missing):
    data = _current_map()
    setattr(data, missing, None)
    position, raw, pixels, dimensions = (
        data.robot_position, data.data, data.pixel_type, data.dimensions
    )
    partial = Decoder.decode_map_partial(_wire(frame=MapFrameType.P.value))

    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is None
    assert data.frame_id == 1 and data.saved_map
    assert data.robot_position is position
    assert data.data is raw and data.pixel_type is pixels
    assert data.dimensions is dimensions


def test_delta_with_changed_grid_requires_full_frame_without_mutation():
    data = _current_map()
    position, raw = data.robot_position, data.data
    partial = Decoder.decode_map_partial(_wire(frame=MapFrameType.P.value, grid=25))

    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is None
    assert data.frame_id == 1 and data.saved_map
    assert data.robot_position is position and data.data is raw


def test_valid_delta_preserves_modulo_byte_addition_and_frame_metadata():
    data = _current_map()
    data.data = bytes([255, 1, 1, 1])
    partial = Decoder.decode_map_partial(_wire(
        frame=MapFrameType.P.value, pixels=bytes([2, 0, 0, 0]),
    ))

    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is data
    assert data.data == bytes([1, 1, 1, 1])
    assert data.frame_id == 2 and not data.saved_map
    assert data.pixel_type[0, 0] == 1


@pytest.mark.parametrize("x,expected", [(-50, 1), (1000, 0)])
def test_robot_outside_raster_uses_segment_bounds_instead_of_wrapped_index(x, expected):
    data = _current_map()
    data.pixel_type[1, :] = 2
    data.segments[2] = Segment(2, 50, -50, 100, 50)
    data.robot_position = Point(x, 0, 0)
    position = data.robot_position

    Decoder.set_robot_segment(data)

    assert data.robot_segment == expected
    assert data.robot_position is position


@pytest.mark.parametrize("missing", ["dimensions", "pixel_type"])
def test_missing_geometry_does_not_invent_segments_or_cleaning_pixels(missing):
    data = _current_map()
    setattr(data, missing, None)

    assert Decoder.get_segments(data, False) == {}
    assert Decoder.decode_cleaning_map_data(data, None) is None


def test_cleaning_overlay_outside_left_edge_does_not_color_first_column():
    data = _current_map()
    overlay = Decoder.decode_cleaning_map_data(
        data, _wire(width=1, height=1, left=-1, pixels=b"\x03"),
    )
    assert overlay is not None
    assert np.array_equal(overlay.pixel_type, data.pixel_type)
    assert not overlay.has_dirty_area and not overlay.has_cleaned_area


def test_partial_floor_metadata_waits_for_geometry_instead_of_guessing_direction():
    data = _current_map()
    data.segments[1] = Segment(1)
    data.segments[1].floor_material = 1
    Decoder.set_floor_material(data)
    assert data.floor_material is None
    data.segments[1].floor_material_direction = 90
    Decoder.set_floor_material(data)
    assert data.floor_material == {1: 2}


@pytest.mark.parametrize("suffix", [b"{", b"\xff"])
def test_present_unparseable_suffix_cannot_become_a_map_without_metadata(suffix):
    raster = zlib.decompress(base64.b64decode(_wire()))
    payload = base64.b64encode(zlib.compress(raster + suffix)).decode()
    assert Decoder.decode_map_partial(payload) is None
    assert Decoder.decode_map(payload, False) == (None, None)


@pytest.mark.parametrize("frame", [MapFrameType.I.value, MapFrameType.P.value])
def test_invalid_timestamp_cannot_discard_other_metadata_and_publish_frame(frame):
    payload = _wire(frame=frame, metadata={
        "timestamp_ms": "invalid", "ris": 2, "cs": "invalid",
    })
    assert Decoder.decode_map_partial(payload) is None
    assert Decoder.decode_map(payload, False) == (None, None)


@pytest.mark.parametrize(
    "missing", ["dimensions", "data", "pixel_type", "empty0", "empty2"]
)
def test_metadata_only_delta_requires_complete_base_without_state_changes(missing):
    data = _current_map()
    if missing == "empty0":
        data, _ = Decoder.decode_map(_wire(width=0, height=0, grid=0), False)
    elif missing == "empty2":
        data, _ = Decoder.decode_map(_wire(pixels=bytes(4)), False)
    else:
        setattr(data, missing, None)
    before = vars(data).copy()
    partial = Decoder.decode_map_partial(_wire(
        width=0, height=0, grid=0, frame=MapFrameType.P.value,
        metadata={"tr": "S10,20"},
    ))

    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is None
    assert vars(data).keys() == before.keys()
    assert all(vars(data)[key] is value for key, value in before.items())


def test_metadata_only_delta_preserves_complete_raster_and_applies_trace():
    data = _current_map()
    raw, pixels, dimensions = data.data, data.pixel_type, data.dimensions
    partial = Decoder.decode_map_partial(_wire(
        width=0, height=0, grid=0, frame=MapFrameType.P.value,
        metadata={"tr": "S10,20"},
    ))
    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is data
    assert data.data is raw and data.pixel_type is pixels
    assert data.dimensions is dimensions and data.frame_id == 2
    assert [(point.x, point.y) for point in data.path] == [(10, 20)]


@pytest.mark.parametrize("middle,expected", [
    (1, MapPixelType.OUTSIDE.value),
    (MapPixelType.WIFI_UNREACHED.value, MapPixelType.WIFI_UNREACHED.value),
])
def test_wifi_projection_handles_each_cell_without_abandoning_known_remainder(
    middle, expected
):
    data, saved = Decoder.decode_map(_wire(
        width=3, height=1, pixels=bytes([2, middle, 14]), frame=MapFrameType.W.value,
    ), False)
    assert data is not None and data.wifi_map and not data.empty_map and saved is None
    assert list(data.pixel_type[:, 0]) == [2, expected, 14]


@pytest.mark.parametrize("origin", [["invalid", 0], [None, 0], [float("nan"), 0],
                                    [0, float("inf")]])
def test_invalid_origin_cannot_escape_delta_failure_or_change_base(origin):
    data = _current_map()
    before = vars(data).copy()
    partial = Decoder.decode_map_partial(_wire(
        frame=MapFrameType.P.value, metadata={"origin": origin},
    ))
    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is None
    assert all(vars(data)[key] is value for key, value in before.items())


def test_numeric_metadata_origin_preserves_fractional_coordinates():
    data, _ = Decoder.decode_map(_wire(metadata={"origin": [12.5, -3]}), False)
    assert data is not None
    assert (data.dimensions.left, data.dimensions.top) == (12.5, -3)


@pytest.mark.parametrize(
    "pixels,expected_empty", [(bytes(4), True), (bytes([1]*4), False)]
)
def test_pixel_delta_marks_empty_base_nonempty_only_when_it_projects_content(
    pixels, expected_empty
):
    data, _ = Decoder.decode_map(_wire(pixels=bytes(4)), False)
    assert data is not None and data.empty_map
    partial = Decoder.decode_map_partial(_wire(
        frame=MapFrameType.P.value, pixels=pixels,
    ))
    assert Decoder.decode_p_map_data_from_partial(partial, data, False) is data
    assert data.empty_map is expected_empty
    assert data.data == pixels and data.frame_id == 2
