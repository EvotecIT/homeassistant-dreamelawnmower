"""Map reset preserves the supported restored and incomplete frame states."""

from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMapMowerMapManager,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapImageDimensions,
)


class _DummyProtocol:
    """Protocol boundary unused by a local map reset."""


@pytest.mark.parametrize("frame_id", [None, 0, 9])
@pytest.mark.parametrize("has_dimensions", [False, True])
def test_reset_map_handles_restored_and_incomplete_frames(frame_id, has_dimensions):
    manager = DreameMapMowerMapManager(_DummyProtocol())
    data = MapData()
    data.frame_id = frame_id
    data.dimensions = MapImageDimensions(0, 0, 4, 3, 50) if has_dimensions else None
    data.path = []
    data.obstacles = {}
    data.floor_material = {1: 2}
    data.hidden_segments = [1]
    manager._map_data = data
    manager._map_data_updated = Mock()

    manager.editor.reset_map()

    assert data.empty_map is True and data.saved_map_status == 0
    assert data.segments == {}
    assert data.path is data.obstacles is None
    assert data.floor_material is data.hidden_segments is None
    if has_dimensions:
        assert (data.dimensions.width, data.dimensions.height) == (0, 0)
    else:
        assert data.dimensions is None
    assert manager._updated_frame_id == (frame_id + 1 if frame_id is not None else None)
    manager._map_data_updated.assert_called_once_with()
