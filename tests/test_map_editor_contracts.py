"""Map edits preserve valid selection and saved/local state boundaries."""

from unittest.mock import Mock

import numpy as np
import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_decoder import (
    DreameMowerMapDecoder,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_manager import (
    DreameMapMowerMapManager,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapImageDimensions,
    MapPixelType,
    Segment,
)


@pytest.fixture
def manager():
    manager = DreameMapMowerMapManager(None)
    manager._map_data_updated = Mock()
    manager.request_next_map_list = Mock()
    manager.schedule_update = Mock()
    return manager


def _saved_map(map_id):
    data = MapData()
    data.map_id = map_id
    data.rotation = 0
    data.saved_map = True
    data.segments = {}
    return data


def test_delete_last_selected_map_clears_manager_selection(manager):
    saved = _saved_map(7)
    manager._saved_map_data = {7: saved}
    manager._map_list = [7]
    manager._map_data = saved
    manager._selected_map_id = 7
    manager._updated_frame_id = 2

    manager.editor.delete_map(7)

    assert manager._map_data is None
    assert manager._selected_map_id is manager._updated_frame_id is None
    assert manager._saved_map_data == {}
    assert manager._map_list == []
    manager.request_next_map_list.assert_called_once()


@pytest.mark.parametrize("deleted_id", [7, 8])
def test_delete_selected_map_switches_to_a_remaining_map(manager, deleted_id):
    manager._saved_map_data = {key: _saved_map(key) for key in (7, 8)}
    manager._map_list = [7, 8]
    manager._map_data = manager._saved_map_data[deleted_id]
    manager._selected_map_id = deleted_id
    remaining_id = 8 if deleted_id == 7 else 7

    manager.editor.delete_map(deleted_id)

    assert set(manager._saved_map_data) == {remaining_id}
    assert manager._map_list == [remaining_id]
    assert manager._selected_map_id == remaining_id
    assert manager._map_data.map_id == remaining_id
    manager.request_next_map_list.assert_called_once()


def test_delete_missing_map_waits_for_metadata_instead_of_mutating(manager):
    manager.editor.delete_map(7)

    assert manager._saved_map_data == {}
    assert manager._map_data is manager._selected_map_id is None
    manager.schedule_update.assert_called_once_with(2)
    manager.request_next_map_list.assert_not_called()


@pytest.mark.parametrize("replacement_id", [7, 9])
def test_temporary_map_replacement_retains_saved_map_with_same_or_new_id(
    manager, replacement_id
):
    manager._saved_map_data = {7: _saved_map(7)}
    manager._map_list = [7]
    manager._selected_map_id = 7
    current = _saved_map(12)
    current.temporary_map = True
    current.saved_map = False
    current.saved_map_id = replacement_id
    manager._map_data = current

    manager.editor.replace_temporary_map(7)

    assert set(manager._saved_map_data) == {replacement_id}
    assert manager._selected_map_id == replacement_id
    assert manager._map_list == [replacement_id]
    assert manager._saved_map_data[replacement_id].saved_map is True
    assert current.saved_map_id == replacement_id
    assert not current.temporary_map


def test_temporary_map_without_saved_id_waits_without_losing_existing_map(manager):
    saved = _saved_map(7)
    manager._saved_map_data = {7: saved}
    manager._map_list = [7]
    manager._selected_map_id = 7
    current = _saved_map(12)
    current.temporary_map = True
    manager._map_data = current

    manager.editor.replace_temporary_map(7)

    assert manager._saved_map_data == {7: saved}
    assert manager._selected_map_id == 7
    assert current.temporary_map
    manager.request_next_map_list.assert_not_called()


@pytest.mark.parametrize("method,args", [
    ("set_segment_visibility", (1, 0)),
    ("set_segment_name", (1, 0, "Garden")),
    ("set_segment_floor_material", (1, 2)),
])
def test_segment_edit_keeps_current_map_when_saved_segments_are_pending(
    manager, method, args
):
    current = _saved_map(7)
    current.saved_map = False
    current.segments = {1: Segment(1, 0, 0, 100, 100)}
    saved = _saved_map(7)
    saved.segments = None
    manager._map_data = current
    manager._selected_map_id = 7
    manager._saved_map_data = {7: saved}

    result = getattr(manager.editor, method)(*args)

    assert result is not None
    assert saved.segments is None
    if method == "set_segment_visibility":
        assert result == [1] and current.segments[1].visibility == 0
    elif method == "set_segment_name":
        assert current.segments[1].custom_name == "Garden"
        assert 1 in result
    else:
        assert result == {"1": {"material": 2}}
        assert current.segments[1].floor_material == 2


@pytest.mark.parametrize("method,args", [
    ("set_cleaning_sequence", ([1],)),
    ("set_segment_order", (1, 1)),
    ("set_segment_cleaning_times", (1, 2)),
])
def test_segment_settings_wait_for_raw_cleanset_before_mutation(manager, method, args):
    current = _saved_map(7)
    current.segments = {1: Segment(1)}
    manager._map_data = current

    with pytest.raises(
        InvalidActionException, match="Cleaning settings are unavailable"
    ):
        getattr(manager.editor, method)(*args)
    assert current.segments[1].order is current.segments[1].cleaning_times is None
    manager._map_data_updated.assert_not_called()


@pytest.mark.parametrize("mode_first", [False, True])
def test_cleaning_payload_uses_uniform_mode_columns_independent_of_segment_order(
    manager, mode_first
):
    plain = Segment(1, cleaning_times=2)
    mode = Segment(2, cleaning_times=1, cleaning_mode=3)
    data = _saved_map(7)
    data.segments = {2: mode, 1: plain} if mode_first else {1: plain, 2: mode}

    settings = manager.editor.cleanset(data)

    assert sorted(settings) == [[1, 2, 2], [2, 1, 3]]


def _segmented_map():
    data = _saved_map(7)
    data.dimensions = MapImageDimensions(0, 0, 2, 2, 50)
    data.data = bytes([1, 1, 2, 2])
    data.pixel_type = np.array([[1, 2], [1, 2]], dtype=np.uint8)
    data.segments = {
        1: Segment(1, 0, 0, 100, 100, neighbors=[2]),
        2: Segment(2, 0, 0, 100, 100, neighbors=[1]),
    }
    return data


@pytest.mark.parametrize("missing", ["dimensions", "data", "pixel_type"])
def test_merge_waits_for_complete_geometry_without_removing_segments(manager, missing):
    data = _segmented_map()
    setattr(data, missing, None)
    manager._saved_map_data = {7: data}

    manager.editor.merge_segments(7, [1, 2])

    assert set(data.segments) == {1, 2}
    manager._map_data_updated.assert_not_called()


def test_merge_updates_pixel_ids_and_removes_neighbor_and_hidden_references(manager):
    data = _segmented_map()
    data.hidden_segments = [2]
    manager._saved_map_data = {7: data}

    manager.editor.merge_segments(7, [1, 2])

    assert data.data == bytes([1, 1, 1, 1])
    np.testing.assert_array_equal(data.pixel_type, np.ones((2, 2), dtype=np.uint8))
    assert set(data.segments) == {1}
    assert data.segments[1].neighbors == data.hidden_segments == []
    manager._map_data_updated.assert_called_once()


def test_merge_retains_neighbor_chain_for_a_following_merge(manager):
    data = _saved_map(7)
    data.dimensions = MapImageDimensions(0, 0, 2, 3, 50)
    data.pixel_type = np.array([[1, 1], [2, 2], [3, 3]], dtype=np.uint8)
    data.data = bytes([1, 2, 3, 1, 2, 3])
    data.segments = DreameMowerMapDecoder.get_segments(data, False)
    for key, neighbors in {1: [2], 2: [1, 3], 3: [2]}.items():
        data.segments[key].neighbors = neighbors
    manager._saved_map_data = {7: data}

    manager.editor.merge_segments(7, [1, 2])

    assert data.segments[1].neighbors == [3]
    assert data.segments[3].neighbors == [1]
    manager.editor.merge_segments(7, [1, 3])
    assert set(data.segments) == {1}
    assert data.segments[1].neighbors == []
    assert data.data == bytes([1] * 6)


def test_merge_updates_distinct_segment_bounds_and_preserves_named_metadata(manager):
    data = _segmented_map()
    data.segments = DreameMowerMapDecoder.get_segments(data, False)
    data.segments[1].neighbors = [2]
    data.segments[2].neighbors = [1]
    data.segments[1].custom_name = "Garden"
    original_bounds = data.segments[1].outline
    manager._saved_map_data = {7: data}

    manager.editor.merge_segments(7, [1, 2])

    expected = DreameMowerMapDecoder.get_segments(data, False)[1]
    assert data.segments[1].outline == expected.outline
    assert data.segments[1].outline != original_bounds
    assert data.segments[1].custom_name == "Garden"
    assert data.segments[1].center == expected.center


def test_merge_preserves_wall_flags_in_raw_pixels(manager):
    data = _segmented_map()
    data.data = bytes([1, 1, 0x82, 2])
    data.pixel_type[0, 1] = MapPixelType.WALL.value
    manager._saved_map_data = {7: data}

    manager.editor.merge_segments(7, [1, 2])

    assert data.data == bytes([1, 1, 0x81, 1])
    assert data.pixel_type[0, 1] == MapPixelType.WALL.value


def test_cleared_segment_order_survives_saved_map_reselection(manager):
    data = _saved_map(7)
    data.segments = {1: Segment(1, order=1), 2: Segment(2, order=2)}
    data.cleanset = {"1": [1, 3, 1, 1], "2": [1, 3, 1, 2]}
    manager._saved_map_data = {7: data}
    manager.editor.set_current_map(7)

    assert manager.editor.set_segment_order(1, 0) == [2]
    assert manager._map_data.cleanset["1"][3] == 0
    assert manager._saved_map_data[7].cleanset["1"][3] == 0
    manager.editor.set_current_map(7)
    assert manager._map_data.segments[1].order == 0
    assert manager.cleaning_sequence == [2]


def test_predefined_points_preserve_fractional_coordinates_and_integer_kind(manager):
    data = _saved_map(7)
    data.predefined_points = {}
    manager._map_data = data
    manager._saved_map_data = {7: _saved_map(7)}
    manager._selected_map_id = 7

    manager.editor.set_predefined_points([[25.5, 30.5, 0, 1]])

    point = data.predefined_points[1]
    assert (point.x, point.y, point.completed, point.type) == (25.5, 30.5, False, 1)
    assert manager._saved_map_data[7].predefined_points[1] is point
