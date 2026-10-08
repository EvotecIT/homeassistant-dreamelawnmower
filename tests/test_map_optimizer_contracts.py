"""Supported optimizer paths preserve map ownership, pixels and optional geometry."""

import logging

import numpy as np
import pytest

from dreame_lawn_mower_client._loader import load_internal_module

_optimizer = load_internal_module("map_optimizer")
_types = load_internal_module("map_types")


def _frame():
    data = _types.MapData()
    data.dimensions = _types.MapImageDimensions(200, 100, 60, 70, 50)
    data.pixel_type = np.zeros((70, 60), dtype=np.uint8)
    data.pixel_type[5:65, 5:55] = 253
    data.pixel_type[5:65, 5] = data.pixel_type[5:65, 54] = 255
    data.pixel_type[5, 5:55] = data.pixel_type[64, 5:55] = 255
    return data


def test_python_optimizer_completes_valid_room_with_outside_border(caplog):
    data = _frame()
    source = data.pixel_type.copy()
    dimensions = data.dimensions
    result = _optimizer.DreameMowerMapOptimizer().optimize(data, js_optimizer=False)
    assert result is data
    assert "Optimize map failed" not in caplog.text
    assert result.optimized_pixel_type is not None
    assert result.optimized_pixel_type.shape == source.shape
    assert result.optimized_dimensions is dimensions
    assert (
        np.count_nonzero(result.optimized_pixel_type) >= np.count_nonzero(source) // 2
    )
    assert set(np.unique(result.optimized_pixel_type)) <= {0, 250, 253, 255}
    assert result.dimensions is dimensions
    np.testing.assert_array_equal(result.pixel_type, source)


@pytest.mark.parametrize("grid", [0, -1])
def test_nonpositive_current_grid_preserves_existing_frame_without_optimization(
    grid, caplog
):
    data = _frame()
    data.dimensions.grid_size = grid
    before = data.pixel_type.copy()
    assert _optimizer.DreameMowerMapOptimizer().optimize(data) is data
    assert data.optimized_pixel_type is None
    np.testing.assert_array_equal(data.pixel_type, before)
    assert "Optimize map failed" not in caplog.text


@pytest.mark.parametrize("missing", ["dimensions", "pixels", "shape"])
def test_partial_frame_with_saved_map_preserves_unoptimized_ownership(
    missing, caplog
):
    data, saved = _frame(), _frame()
    if missing == "dimensions":
        data.dimensions = None
    elif missing == "pixels":
        data.pixel_type = None
    else:
        data.pixel_type = np.zeros((1, 1), dtype=np.uint8)
    assert _optimizer.DreameMowerMapOptimizer().optimize(data, saved) is data
    assert data.optimized_pixel_type is None
    assert data.optimized_dimensions is None
    assert "Optimize map failed" not in caplog.text


@pytest.mark.parametrize("js_optimizer", [True, False])
def test_partial_saved_map_is_ignored_while_valid_current_map_can_complete(
    js_optimizer, caplog
):
    data, saved = _frame(), _types.MapData()
    assert (
        _optimizer.DreameMowerMapOptimizer().optimize(data, saved, js_optimizer) is data
    )
    assert data.optimized_pixel_type is not None
    assert data.optimized_pixel_type.shape == data.pixel_type.shape
    assert "Optimize map failed" not in caplog.text


def test_unknown_charger_heading_preserves_position_and_complete_python_map(caplog):
    data = _frame()
    data.charger_position = _types.Point(850, 950)
    original = (
        data.charger_position.x,
        data.charger_position.y,
        data.charger_position.a,
    )
    _optimizer.DreameMowerMapOptimizer().optimize(data, js_optimizer=False)
    assert data.optimized_pixel_type is not None
    assert data.optimized_charger_position is None
    assert (
        data.charger_position.x,
        data.charger_position.y,
        data.charger_position.a,
    ) == original
    assert "Optimize map failed" not in caplog.text


def test_empty_python_frame_completes_without_claiming_new_optimized_pixels(caplog):
    data = _frame()
    data.pixel_type[:] = 0
    assert (
        _optimizer.DreameMowerMapOptimizer().optimize(data, js_optimizer=False) is data
    )
    assert data.optimized_pixel_type is None
    assert "Optimize map failed" not in caplog.text


def test_wifi_optimizer_keeps_unreached_cells_and_does_not_mutate_source_pixels(caplog):
    data = _frame()
    data.wifi_map = True
    data.pixel_type[:] = _types.MapPixelType.WIFI_UNREACHED
    data.pixel_type[20:30, 20:30] = _types.MapPixelType.WIFI_LOW
    before = data.pixel_type.copy()
    assert _optimizer.DreameMowerMapOptimizer().optimize(data) is data
    assert data.optimized_dimensions is data.dimensions
    assert data.optimized_pixel_type is not None
    assert data.optimized_pixel_type[0, 0] == _types.MapPixelType.WIFI_UNREACHED
    assert data.optimized_pixel_type[25, 25] == _types.MapPixelType.WIFI_LOW
    np.testing.assert_array_equal(data.pixel_type, before)
    assert "Optimize map failed" not in caplog.text


def test_failed_js_engine_uses_valid_saved_raster_without_modifying_either_source(
    monkeypatch, caplog
):
    data, saved = _frame(), _frame()
    data_source, saved_source = data.pixel_type.copy(), saved.pixel_type.copy()

    def fail(*_args):
        raise RuntimeError("synthetic JS engine failure")

    monkeypatch.setattr(_optimizer, "optimize_map", fail)
    caplog.set_level(logging.WARNING)
    assert _optimizer.DreameMowerMapOptimizer().optimize(data, saved) is data
    assert data.optimized_pixel_type is not None
    assert data.optimized_dimensions is not None
    np.testing.assert_array_equal(data.pixel_type, data_source)
    np.testing.assert_array_equal(saved.pixel_type, saved_source)
    assert "Optimize map failed" in caplog.text


def test_python_saved_map_merge_preserves_origins_and_both_source_rasters(caplog):
    data, saved = _frame(), _frame()
    saved.dimensions.left += 150
    saved.dimensions.top += 100
    data_source, saved_source = data.pixel_type.copy(), saved.pixel_type.copy()
    assert (
        _optimizer.DreameMowerMapOptimizer().optimize(data, saved, js_optimizer=False)
        is data
    )
    assert "Optimize map failed" not in caplog.text
    dimensions = data.optimized_dimensions
    pixels = data.optimized_pixel_type
    assert dimensions is not None and pixels is not None
    assert (dimensions.left, dimensions.top, dimensions.grid_size) == (100, 200, 50)
    assert (dimensions.width, dimensions.height) == (73, 62)
    assert pixels.shape == (73, 62)
    assert pixels[23, 22] == saved_source[20, 20] == 253
    assert saved_source[3, 4] == 0
    assert pixels[6, 6] != 0
    assert pixels[0, 0] == 0
    np.testing.assert_array_equal(data.pixel_type, data_source)
    np.testing.assert_array_equal(saved.pixel_type, saved_source)
