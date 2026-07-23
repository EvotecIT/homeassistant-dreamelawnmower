"""Regression contracts for the inherited map renderer."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMowerMapRenderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    MapData,
    Segment,
)


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
