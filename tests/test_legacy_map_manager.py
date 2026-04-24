"""Regression checks for tolerated legacy map-manager payload shapes."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    MAP_PARAMETER_CODE,
    MAP_PARAMETER_OUT,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMapMowerMapManager,
)


class _DummyProtocol:
    """Minimal protocol stand-in for map-manager unit checks."""


def test_handle_properties_skips_map_property_without_value() -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._ready = True

    manager.handle_properties([{"piid": 1}])

    assert manager._map_request_time is None


def test_request_next_p_map_skips_map_property_without_value() -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._request_map = lambda payload: {  # noqa: ARG005
        MAP_PARAMETER_CODE: 0,
        MAP_PARAMETER_OUT: [{"piid": 1}],
    }

    assert manager._request_next_p_map(map_id=1, frame_id=2) is True


def test_request_i_map_skips_map_property_without_value() -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._request_map = lambda payload: {  # noqa: ARG005
        MAP_PARAMETER_CODE: 0,
        MAP_PARAMETER_OUT: [{"piid": 1}],
    }
    manager._request_map_from_cloud = lambda: False

    assert manager._request_i_map() is False


def test_request_i_map_ignores_non_mapping_response() -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._request_map = lambda payload: []  # noqa: ARG005
    manager._request_map_from_cloud = lambda: False

    assert manager._request_i_map() is False
