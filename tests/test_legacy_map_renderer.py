"""Regression contracts for the inherited map renderer."""

from __future__ import annotations

import json
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMowerMapRenderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    MapData,
    MapImageDimensions,
    MapPixelType,
    MapRendererResources,
    Segment,
)


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
    assert renderer._calculate_calibration_points(data) is None


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
