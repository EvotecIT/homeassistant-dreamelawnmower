"""Exercise the embedded map algorithm with its real JavaScript runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import numpy as np
import pytest

from dreame_lawn_mower_client._loader import load_internal_module

_optimizer = load_internal_module("map_optimizer")
_types = load_internal_module("map_types")


def _map_pair(case: str):
    current = _types.MapData()
    current.dimensions = _types.MapImageDimensions(200, 100, 50, 60, 50)
    current.pixel_type = np.zeros((60, 50), dtype=np.uint8)
    current.charger_position = _types.Point(850, 950, 90)
    if case != "empty":
        current.pixel_type[5:55, 5:45] = 253
        current.pixel_type[5:55, 5] = 255
        current.pixel_type[5:55, 44] = 255
        current.pixel_type[5, 5:45] = 255
        current.pixel_type[54, 5:45] = 255
        current.pixel_type[20:23, 20:23] = 250

    saved = None
    if case == "saved":
        saved = _types.MapData()
        saved.dimensions = _types.MapImageDimensions(100, 0, 54, 64, 50)
        saved.pixel_type = np.full((64, 54), 254, dtype=np.uint8)
    return current, saved


# Full pixel digests captured with the original V8 optimizer. These protect
# algorithm output, including room borders and merging maps with different origins.
@pytest.mark.parametrize(
    ("case", "digest", "dimensions"),
    [
        (
            "empty",
            "c81ca5eda5947c7826ad046fdbdc2a25a846b835a6c34c237cc8b3afbe9ec6cc",
            (200, 100, 50, 60, 50),
        ),
        (
            "room",
            "96d32b818551678f74e95179e7a21d5099cc63d090ca97d35b7fa2adc6e64b01",
            (200, 100, 50, 60, 50),
        ),
        (
            "saved",
            "e2d994924bb0355d65193af0bf1c0cfee328f3f73b2bb1ac83ce1b3dc5d74c96",
            (100, 0, 54, 64, 50),
        ),
    ],
)
def test_optimizer_preserves_pixels_and_coordinates(case, digest, dimensions):
    optimizer = _optimizer.DreameMowerMapOptimizer()
    for _ in range(2):
        current, saved = _map_pair(case)
        source = current.pixel_type.copy()
        result = optimizer.optimize(current, saved)

        assert result is current
        assert sha256(result.optimized_pixel_type.tobytes()).hexdigest() == digest
        assert result.optimized_dimensions == _types.MapImageDimensions(*dimensions)
        np.testing.assert_array_equal(current.pixel_type, source)


def test_cached_optimizer_accepts_calls_from_different_workers():
    optimizer = _optimizer.DreameMowerMapOptimizer()
    # Initialize from one thread, then reuse from the camera/map worker threads.
    optimizer.optimize(*_map_pair("room"))

    def optimize_from_worker(_):
        result = optimizer.optimize(*_map_pair("room"))
        return sha256(result.optimized_pixel_type.tobytes()).hexdigest()

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(optimize_from_worker, range(12)))

    assert results == [
        "96d32b818551678f74e95179e7a21d5099cc63d090ca97d35b7fa2adc6e64b01"
    ] * 12
