"""Regression contracts for the inherited map renderer."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_map,
    map_renderer_types,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMowerMapRenderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    Coordinate,
    Furniture,
    FurnitureType,
    MapData,
    MapImageDimensions,
    MapPixelType,
    MapRendererResources,
    Obstacle,
    ObstacleType,
    Point,
    RecoveryMapType,
    Segment,
)


@pytest.mark.parametrize("new_furnitures", [False, True])
def test_default_renderer_resources_serialize_optional_assets(
    new_furnitures: bool,
) -> None:
    capability = SimpleNamespace(
        customized_cleaning=True, custom_cleaning_mode=True, cleaning_route=True,
        wifi_map=True, camera_streaming=True, pet_furniture=True,
        extended_furnitures=True, new_furnitures=new_furnitures,
    )
    resources = DreameMowerMapRenderer().get_resources(capability)
    output = json.loads(json.dumps(asdict(resources)))
    assert output["robot_type"] == 0
    assert output["segment"]
    assert output["obstacle"]
    assert output["furniture"]
    assert all(
        item["dimensions"] is None or isinstance(item["dimensions"], list)
        for item in output["furniture"].values()
    )


@pytest.mark.parametrize("icon_set", [0, 1, 2, 3])
def test_mower_unknown_heading_preserves_marker_without_direction(
    icon_set: int,
) -> None:
    renderer = DreameMowerMapRenderer()
    renderer.icon_set = icon_set
    renderer.config.cleaning_direction = True
    point = Point(2500, 2500)
    dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    actual = renderer.render_mower(point, 1, (100, 100), dimensions, 20, 0, 1)
    reference = DreameMowerMapRenderer()
    reference.icon_set = icon_set
    reference.config.cleaning_direction = False
    expected = reference.render_mower(
        Point(2500, 2500, 0), 1, (100, 100), dimensions, 20, 0, 1,
    )
    assert actual.getbbox() is not None
    assert actual.tobytes() == expected.tobytes()
    assert point.a is None


def test_router_icon_resizes_when_reusing_renderer() -> None:
    renderer = DreameMowerMapRenderer()
    position = Point(2500, 2500)
    dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    renderer.render_router(position, (100, 100), dimensions, 10, 0, 1)
    resized = renderer.render_router(position, (100, 100), dimensions, 30, 0, 1)
    fresh = DreameMowerMapRenderer().render_router(
        position, (100, 100), dimensions, 30, 0, 1,
    )
    assert resized.tobytes() == fresh.tobytes()


def test_segment_without_palette_assignment_preserves_label_and_order() -> None:
    segment = Segment(1)
    segment.x = segment.y = 2500
    segment.name = "Garden"
    segment.order = 1
    renderer = DreameMowerMapRenderer()
    actual = renderer.render_segment(
        segment, False, (200, 200), MapImageDimensions(0, 0, 100, 100, 50),
        10, 0, 1, True, False,
    )
    assert actual.getbbox() is not None
    assert segment.color_index is None
    # Supplying the same neutral background as a palette color must preserve
    # the complete label and order marker, not merely avoid an exception.
    reference = copy.deepcopy(segment)
    reference.color_index = 0
    neutral_renderer = DreameMowerMapRenderer()
    neutral_renderer.color_scheme = copy.deepcopy(neutral_renderer.color_scheme)
    neutral_renderer.color_scheme.segment[0][1] = renderer.color_scheme.icon_background
    expected = neutral_renderer.render_segment(
        reference, False, (200, 200), MapImageDimensions(0, 0, 100, 100, 50),
        10, 0, 1, True, False,
    )
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("ignore_status", [0, 1, 2])
def test_obstacle_background_tracks_marker_size_and_rotation(
    ignore_status: int,
) -> None:
    obstacle = Obstacle(2500, 2500, ObstacleType.WIRE, 100, ignore_status=ignore_status)
    dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    renderer = DreameMowerMapRenderer()
    renderer.render_obstacle(obstacle, (100, 100), dimensions, 10, 0, 1)
    actual = renderer.render_obstacle(obstacle, (100, 100), dimensions, 20, 90, 1)
    expected = DreameMowerMapRenderer().render_obstacle(
        obstacle, (100, 100), dimensions, 20, 90, 1,
    )
    assert actual is not None and expected is not None
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("point_type", [0, 1])
def test_cruise_background_tracks_marker_size_and_rotation(point_type: int) -> None:
    point = Coordinate(2500, 2500, False, point_type)
    dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    renderer = DreameMowerMapRenderer()
    renderer.render_cruise_point(1, point, (100, 100), dimensions, 10, 0, 1)
    actual = renderer.render_cruise_point(1, point, (100, 100), dimensions, 20, 90, 1)
    expected = DreameMowerMapRenderer().render_cruise_point(
        1, point, (100, 100), dimensions, 20, 90, 1,
    )
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("version", [1, 2])
def test_furniture_background_tracks_marker_size_and_rotation(version: int) -> None:
    furniture = Furniture(2500, 2500, 0, 0, 0, 0, FurnitureType.COFFEE_TABLE, 0)
    dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    renderer = DreameMowerMapRenderer()
    renderer.render_furniture(furniture, version, (100, 100), dimensions, 10, 0, 1)
    actual = renderer.render_furniture(
        furniture, version, (100, 100), dimensions, 20, 90, 1,
    )
    expected = DreameMowerMapRenderer().render_furniture(
        furniture, version, (100, 100), dimensions, 20, 90, 1,
    )
    assert actual is not None and expected is not None
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize("icon_set", [0, 1, 2, 3])
def test_charger_without_heading_keeps_reported_location(
    icon_set: int, caplog: pytest.LogCaptureFixture,
) -> None:
    data = MapData()
    data.map_id = data.frame_id = 1
    data.empty_map = False
    data.rotation = 0
    data.charger_position = Point(100, 100)
    data.dimensions = MapImageDimensions(0, 0, 8, 8, 50)
    data.pixel_type = np.full((8, 8), MapPixelType.FLOOR.value)
    data.data = bytes([MapPixelType.FLOOR.value] * 64)
    data.segments = {}
    renderer = DreameMowerMapRenderer(cache=False, map_objects=["charger"])
    renderer.icon_set = icon_set
    actual = renderer.render_map(data)
    assert actual != renderer.default_map_image
    assert "Map render Failed" not in caplog.text
    assert data.charger_position == Point(100, 100)


@pytest.mark.parametrize("charger", [None, Point(50, 50)])
def test_docked_marker_preserves_reported_position_without_charger_heading(
    charger: Point | None, caplog: pytest.LogCaptureFixture,
) -> None:
    data = MapData()
    data.map_id = data.frame_id = 1
    data.empty_map = False
    data.rotation = 0
    data.robot_position = Point(100, 100, 45)
    data.charger_position = charger
    data.docked = True
    data.dimensions = MapImageDimensions(0, 0, 8, 8, 50)
    data.pixel_type = np.full((8, 8), MapPixelType.FLOOR.value)
    data.data = bytes([MapPixelType.FLOOR.value] * 64)
    data.segments = {}
    reported = copy.deepcopy(data)
    reported.docked = False
    expected = DreameMowerMapRenderer(
        cache=False, map_objects=["robot"],
    ).render_map(reported)
    renderer = DreameMowerMapRenderer(cache=False, map_objects=["robot"])
    actual = renderer.render_map(data)
    assert actual != renderer.default_map_image
    assert actual == expected
    assert "Map render Failed" not in caplog.text


@pytest.mark.parametrize(
    "recovery_type", [None, RecoveryMapType.UNKNOWN, RecoveryMapType.BACKUP],
)
def test_recovery_map_header_accepts_optional_type(
    recovery_type: RecoveryMapType | None, caplog: pytest.LogCaptureFixture,
) -> None:
    data = MapData()
    data.map_id = 1
    data.frame_id = 1
    data.empty_map = False
    data.rotation = 0
    data.recovery_map = True
    data.recovery_map_type = recovery_type
    data.last_updated = 1700000000
    data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
    data.pixel_type = np.full((4, 4), MapPixelType.FLOOR.value)
    data.data = bytes([MapPixelType.FLOOR.value] * 16)
    data.segments = {}
    renderer = DreameMowerMapRenderer(cache=False, map_objects=[])
    rendered = renderer.render_map(data, info_text=True)
    assert rendered != renderer.default_map_image
    assert "Map render Failed" not in caplog.text
    with Image.open(BytesIO(rendered)) as image:
        image.load()
        assert image.width >= 490


def test_obstacle_box_bottom_clip_preserves_horizontal_extent() -> None:
    source = BytesIO()
    Image.new("RGB", (400, 200), "white").save(source, format="PNG")
    obstacle = Obstacle(0, 0, 0, 100, pos_x=10, pos_y=90, width=10, height=30)
    rendered = DreameMowerMapRenderer().render_obstacle_image(
        source.getvalue(), obstacle, False,
    )
    with Image.open(BytesIO(rendered)) as image:
        assert image.size == (400, 200)
        # Outside the true right edge must remain white, despite bottom clipping.
        assert min(image.getpixel((160, 180))) > 245
        red, _, blue = image.getpixel((60, 199))
        assert blue - red > 50


def test_obstacle_corner_loading_recovers_after_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = BytesIO()
    Image.new("RGB", (400, 200), "white").save(source, format="PNG")
    obstacle = Obstacle(0, 0, 0, 100, pos_x=20, pos_y=20, width=20, height=20)
    renderer = DreameMowerMapRenderer()
    open_image = Image.open
    calls = 0

    def interrupted_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("Interrupted corner image load")
        return open_image(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Image, "open", interrupted_open)
        with pytest.raises(OSError, match="Interrupted corner image load"):
            renderer.render_obstacle_image(source.getvalue(), obstacle, False)

    recovered = renderer.render_obstacle_image(source.getvalue(), obstacle, False)
    fresh = DreameMowerMapRenderer().render_obstacle_image(
        source.getvalue(), obstacle, False,
    )
    assert recovered == fresh


def test_layer_composition_uses_key_order_and_preserves_source_images() -> None:
    red = Image.new("RGBA", (2, 2), (255, 0, 0, 128))
    blue = Image.new("RGBA", (2, 2), (0, 0, 255, 128))
    layer = map_renderer_types.MapRendererLayer
    layers = {layer.OBSTACLE: {2: blue, 1: red, 3: None}}
    DreameMowerMapRenderer()._combine_layers(
        layers, (2, 2), layer.OBSTACLES, layer.OBSTACLE,
    )
    combined = layers[layer.OBSTACLES]
    assert combined.getpixel((0, 0)) == (85, 0, 170, 192)
    assert red.getpixel((0, 0)) == (255, 0, 0, 128)
    assert blue.getpixel((0, 0)) == (0, 0, 255, 128)


@pytest.mark.parametrize("segments", [None, {1: Segment(1)}])
def test_neglected_segment_mask_survives_missing_center(segments) -> None:
    mask = Image.new("RGBA", (100, 100), (200, 100, 10, 110))
    rendered = DreameMowerMapRenderer().render_neglected_segments(
        {1: 1}, segments, mask.size, mask,
        MapImageDimensions(0, 0, 100, 100, 50), 0, False,
    )
    assert rendered.size == mask.size
    assert rendered.tobytes() == mask.tobytes()


@pytest.mark.parametrize("point_kind", ["active", "predefined"])
def test_map_json_exports_fractional_point_coordinates(point_kind: str) -> None:
    data = MapData()
    data.dimensions = MapImageDimensions(0, 0, 2, 2, 50)
    data.pixel_type = np.full((2, 2), MapPixelType.FLOOR.value)
    if point_kind == "active":
        data.active_points = [Point(12.5, -3.25)]
        field = "active_points"
    else:
        data.predefined_points = {7: Coordinate(12.5, -3.25, False, 0)}
        field = "predefined_points"
    output = json.loads(DreameMowerMapRenderer().get_data_string(data))
    assert output[field] == [[12.5, -3.25]]


def test_camera_map_without_predefined_points_exports_empty_array() -> None:
    data = MapData()
    data.saved_map = True
    data.dimensions = MapImageDimensions(0, 0, 2, 2, 50)
    data.pixel_type = np.full((2, 2), MapPixelType.FLOOR.value)
    state = SimpleNamespace(
        _map_manager=None,
        capability=SimpleNamespace(
            lidar_navigation=True, map_object_offset=False, camera_streaming=True,
            new_furnitures=False,
        ),
        status=SimpleNamespace(
            started=False, segment_cleaning=False, cruising=False,
            customized_cleaning=False, docked=False,
        ),
    )
    prepared = device_map._DreameMowerDeviceMapMixin.get_map_for_render(state, data)
    output = json.loads(DreameMowerMapRenderer().get_data_string(prepared))
    assert output["predefined_points"] == []
    assert data.predefined_points is None


@pytest.mark.parametrize("has_dimensions", [False, True])
def test_incomplete_map_returns_empty_output_and_preserves_resources(
    has_dimensions: bool,
) -> None:
    renderer = DreameMowerMapRenderer()
    data = MapData()
    if has_dimensions:
        data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
    assert renderer.render_map(data) == renderer.default_map_image
    assert renderer.get_data_string(data) == "{}"
    resources = MapRendererResources(renderer="test", robot="robot-image")
    result = json.loads(renderer.get_data_string(data, resources=resources))
    assert result["resources"]["renderer"] == "test"
    assert result["resources"]["robot"] == "robot-image"
    assert "charger" not in result["resources"]
    assert renderer.calibration_points is None


def test_empty_map_json_serializes_optional_resources() -> None:
    data = MapData()
    data.empty_map = True
    resources = MapRendererResources(renderer="test")
    result = json.loads(DreameMowerMapRenderer().get_data_string(data, resources))
    assert result["resources"]["renderer"] == "test"


@pytest.mark.parametrize(
    "cache,previous_frame", [(False, False), (True, False), (True, True)]
)
def test_raster_failure_returns_complete_png_and_next_frame_recovers(
    cache: bool, previous_frame: bool, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    renderer = DreameMowerMapRenderer(cache=cache, map_objects=[])

    def frame(frame_id: int) -> MapData:
        data = MapData()
        data.map_id = 1
        data.frame_id = frame_id
        data.empty_map = False
        data.rotation = 0
        data.saved_map = False
        data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
        data.pixel_type = np.full((4, 4), MapPixelType.FLOOR.value)
        data.data = bytes([MapPixelType.FLOOR.value] * 16)
        data.segments = {}
        return data

    expected = (
        renderer.render_map(frame(1)) if previous_frame else renderer.default_map_image
    )

    def fail_raster(*args: object, **kwargs: object) -> Image.Image:
        raise OSError("Raster allocation failed")

    with monkeypatch.context() as patch:
        patch.setattr(Image, "fromarray", fail_raster)
        failed_frame = frame(2)
        # Changed pixel data requires a fresh raster even with a completed cache.
        failed_frame.data = bytes([MapPixelType.WALL.value] * 16)
        result = renderer.render_map(failed_frame)

    assert result == expected
    assert "Raster allocation failed" in caplog.text
    assert renderer.render_complete is True
    with Image.open(BytesIO(result)) as image:
        image.load()
        assert image.format == "PNG"
    recovery_frame = frame(3)
    recovery_frame.pixel_type = np.full((4, 4), MapPixelType.WALL.value)
    recovery_frame.data = bytes([MapPixelType.WALL.value] * 16)
    recovered = renderer.render_map(recovery_frame)
    assert recovered != expected
    assert recovered != renderer.default_map_image
    assert renderer.render_complete is True


def _map_with_segment() -> MapData:
    map_data = MapData()
    map_data.segments = {1: Segment(1)}
    map_data.rotation = 0
    map_data.saved_map = False
    map_data.recovery_map = False
    map_data.cleanset = {}
    map_data.active_segments = None
    map_data.hidden_segments = None
    map_data.cleaning_map = False
    map_data.neglected_segments = None
    return map_data


def test_unchanged_cached_segment_does_not_render_again() -> None:
    previous_map = _map_with_segment()
    current_map = _map_with_segment()

    assert (
        DreameMowerMapRenderer._segment_needs_render(
            cache_enabled=True,
            previous_map=previous_map,
            cached_segments={1: object()},
            map_data=current_map,
            segment_id=1,
            segment=current_map.segments[1],
        )
        is False
    )


def test_changed_cached_segment_renders_again() -> None:
    previous_map = _map_with_segment()
    current_map = _map_with_segment()
    current_map.segments[1].order = 2

    assert (
        DreameMowerMapRenderer._segment_needs_render(
            cache_enabled=True,
            previous_map=previous_map,
            cached_segments={1: object()},
            map_data=current_map,
            segment_id=1,
            segment=current_map.segments[1],
        )
        is True
    )


def test_route_change_renders_cached_segment_again() -> None:
    previous_map = _map_with_segment()
    current_map = _map_with_segment()
    current_map.segments[1].cleaning_route = 2

    assert (
        DreameMowerMapRenderer._segment_needs_render(
            cache_enabled=True,
            previous_map=previous_map,
            cached_segments={1: object()},
            map_data=current_map,
            segment_id=1,
            segment=current_map.segments[1],
        )
        is True
    )


def test_cleaning_map_transition_invalidates_segment_layer() -> None:
    previous_map = _map_with_segment()
    current_map = _map_with_segment()
    current_map.cleaning_map = True

    assert (
        DreameMowerMapRenderer._segments_layer_needs_update(
            cache_enabled=True,
            previous_map=previous_map,
            map_data=current_map,
            has_cached_layer=True,
        )
        is True
    )


def test_segments_do_not_share_default_neighbors() -> None:
    first = Segment(1)
    second = Segment(2)

    first.neighbors.append(2)

    assert second.neighbors == []


@pytest.mark.parametrize("status,rotation", [(1, 0), (2, 0), (10, 0), (3, 0), (3, 90)])
def test_mower_status_cache_tracks_size_and_rotation(
    status: int, rotation: int
) -> None:
    dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    position = Point(2500, 2500, 45)
    renderer = DreameMowerMapRenderer()
    renderer.config.cleaning_direction = True
    renderer.render_mower(position, status, (200, 200), dimensions, 10, 0, 1)
    actual = renderer.render_mower(
        position, status, (200, 200), dimensions, 30, rotation, 1
    )
    fresh = DreameMowerMapRenderer()
    fresh.config.cleaning_direction = True
    expected = fresh.render_mower(
        position, status, (200, 200), dimensions, 30, rotation, 1
    )
    assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize(
    "collection",
    [
        "furnitures",
        "segments",
        "obstacles",
        "active_cruise_points",
        "invisible_obstacle",
    ],
)
def test_partial_object_removal_recomposes_cached_layer(collection: str) -> None:
    invisible_obstacle = collection == "invisible_obstacle"
    if invisible_obstacle:
        collection = "obstacles"
    data = MapData()
    data.rotation = 0
    data.saved_map = False
    data.dimensions = MapImageDimensions(0, 0, 100, 100, 50)
    data.furniture_version = 1
    objects = {}
    for key, position in [(1, 1500), (2, 3500)]:
        if collection == "furnitures":
            value = Furniture(
                position, position, 0, 0, 0, 0, FurnitureType.COFFEE_TABLE, 0
            )
        elif collection == "segments":
            value = Segment(key)
            value.x = value.y = position
            value.name = f"Garden {key}"
        elif collection == "obstacles":
            value = Obstacle(
                position, position, ObstacleType.WIRE, 100, ignore_status=0
            )
        else:
            value = Coordinate(position, position, False, 0)
        objects[key] = value
    setattr(data, collection, objects)
    renderer = DreameMowerMapRenderer(cache=True)
    image = Image.new("RGBA", (100, 100))
    layers = {}
    before = renderer.render_objects(layers, data, 0, 0, image, 1)
    renderer._map_data = copy.deepcopy(data)
    if invisible_obstacle:
        objects[2].type = ObstacleType.UNKNOWN
    else:
        del objects[2]
    actual = renderer.render_objects(layers, data, 0, 0, image, 1)
    expected = DreameMowerMapRenderer(cache=True).render_objects(
        {}, data, 0, 0, image, 1
    )
    assert before.tobytes() != expected.tobytes()
    assert actual.tobytes() == expected.tobytes()
