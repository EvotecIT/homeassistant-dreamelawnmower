"""Regression checks for tolerated legacy map-manager payload shapes."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    MAP_PARAMETER_CODE,
    MAP_PARAMETER_OUT,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMapMowerMapManager,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map import (
    DreameMowerMapOptimizer as LegacyDreameMowerMapOptimizer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_optimizer import (
    DreameMowerMapOptimizer,
)


class _DummyProtocol:
    """Minimal protocol stand-in for map-manager unit checks."""


def test_map_optimizer_keeps_historical_import_and_manager_contract() -> None:
    assert LegacyDreameMowerMapOptimizer is DreameMowerMapOptimizer

    manager = DreameMapMowerMapManager(_DummyProtocol())

    assert type(manager.optimizer) is DreameMowerMapOptimizer


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


def test_map_download_logs_do_not_disclose_signed_urls(caplog):
    import logging
    from types import SimpleNamespace
    from unittest.mock import Mock

    signed_url = "https://example.invalid/map?token=private-map-token"
    cloud = SimpleNamespace(logged_in=True, get_file=Mock(return_value=b"map"))
    protocol = SimpleNamespace(cloud=cloud, dreame_cloud=True)
    manager = DreameMapMowerMapManager(protocol)
    manager._get_file_url = Mock(return_value=signed_url)
    caplog.set_level(logging.INFO)

    assert manager._get_interim_file_data("map-object") == b"map"
    cloud.get_file.assert_called_with(signed_url)
    cloud.get_file.return_value = None
    assert manager._get_interim_file_data("map-object") is None

    manager._map_list = [1]
    manager._saved_map_data[1] = SimpleNamespace(
        recovery_map_list=[SimpleNamespace(object_name="recovery-object")],
    )
    cloud.get_file.return_value = b"recovery"
    assert manager.get_recovery_map_file(1, 1) == (
        b"recovery", signed_url, "recovery-object",
    )
    assert "Request map data" in caplog.text
    assert "private-map-token" not in caplog.text
    assert signed_url not in caplog.text


def test_map_download_failure_logs_only_exception_type(caplog):
    from types import SimpleNamespace
    from unittest.mock import Mock

    secret = "https://example.invalid/map?token=private-map-token"
    manager = DreameMapMowerMapManager(
        SimpleNamespace(cloud=SimpleNamespace(logged_in=True)),
    )
    manager._map_list_object_name = "map-object"
    manager._get_interim_file_data = Mock(side_effect=RuntimeError(secret))
    manager.request_map_list()
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert manager._need_map_list_request is None


def test_partial_frame_waits_for_pending_initial_map():
    """A delta arriving before the initial frame stays queued until its base exists."""
    from unittest.mock import Mock

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import map_types

    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._latest_map_id = 8
    manager._map_request_time = 12000
    manager._request_i_map = Mock()
    partial = map_types.MapDataPartial()
    partial.map_id = 8
    partial.frame_id = 2
    partial.frame_type = map_types.MapFrameType.P.value
    partial.timestamp_ms = 13000

    assert manager._add_map_data(partial) is True
    assert manager._map_data_queue[8][2] is partial
    assert manager._map_data is None
    assert manager._current_frame_id is None
    assert manager._map_request_time == 12000
    manager._request_i_map.assert_not_called()


def test_next_partial_frame_can_retry_after_transport_failure() -> None:
    from unittest.mock import Mock

    protocol = Mock()
    protocol.action.side_effect = [OSError("connection lost"), {"code": 0, "out": []}]
    manager = DreameMapMowerMapManager(protocol)

    assert manager._request_next_p_map(1, 2) is False
    assert manager._request_next_p_map(1, 2) is True
    assert protocol.action.call_count == 2


def test_next_partial_frame_can_retry_after_device_rejection() -> None:
    from unittest.mock import Mock

    protocol = Mock()
    protocol.action.side_effect = [{"code": 1}, {"code": 0, "out": []}]
    manager = DreameMapMowerMapManager(protocol)

    assert manager._request_next_p_map(1, 2) is False
    assert manager._request_next_p_map(1, 2) is True
    assert protocol.action.call_count == 2


def test_next_partial_frame_suppresses_duplicate_only_while_inflight() -> None:
    from unittest.mock import Mock

    protocol = Mock()
    manager = DreameMapMowerMapManager(protocol)

    def respond(*args):
        assert manager._request_next_p_map(1, 2) is None
        return {"code": 0, "out": []}

    protocol.action.side_effect = respond
    assert manager._request_next_p_map(1, 2) is True
    assert manager._request_next_p_map(1, 2) is True
    assert protocol.action.call_count == 2
