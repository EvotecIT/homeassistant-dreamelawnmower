"""Regression checks for tolerated legacy map-manager payload shapes."""

from __future__ import annotations

import base64
import json
import zlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_manager as map_manager_module,
)
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
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapDataPartial,
    MapFrameType,
)


class _DummyProtocol:
    """Minimal protocol stand-in for map-manager unit checks."""


def _encoded_map_frame(timestamp: int | None, frame_id: int = 10) -> str:
    header = bytearray(map_manager_module.DreameMowerMapDecoder.HEADER_SIZE)
    header[0:2] = (7).to_bytes(2, "little", signed=True)
    header[2:4] = frame_id.to_bytes(2, "little", signed=True)
    header[4] = MapFrameType.I.value
    metadata = {} if timestamp is None else {"timestamp_ms": timestamp}
    return base64.b64encode(
        zlib.compress(bytes(header) + json.dumps(metadata).encode())
    ).decode()


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


@pytest.mark.parametrize("interim", [True, False])
def test_map_url_reuses_signature_until_refresh_margin(
    monkeypatch: pytest.MonkeyPatch, interim: bool
) -> None:
    cloud = Mock()
    signer = cloud.get_interim_file_url if interim else cloud.get_file_url
    signer.side_effect = [
        "https://maps.example/map?signature=first",
        "https://maps.example/map?signature=second",
    ]
    manager = DreameMapMowerMapManager(SimpleNamespace(cloud=cloud))
    now = 1000
    monkeypatch.setattr(map_manager_module.time, "time", lambda: now)

    assert manager._get_file_url("map.bin", interim) == (
        "https://maps.example/map?signature=first"
    )
    now = 2739  # 61 seconds remain in the 30-minute cache lifetime.
    assert manager._get_file_url("map.bin", interim) == (
        "https://maps.example/map?signature=first&current=2739"
    )
    signer.assert_called_once_with("map.bin")

    now = 2740  # Refresh at the 60-second margin, before the URL expires.
    assert manager._get_file_url("map.bin", interim) == (
        "https://maps.example/map?signature=second"
    )
    assert signer.call_count == 2
    other_signer = cloud.get_file_url if interim else cloud.get_interim_file_url
    other_signer.assert_not_called()


def test_map_url_failed_signing_can_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = Mock()
    cloud.get_interim_file_url.side_effect = [
        None,
        "https://maps.example/map?signature=recovered",
    ]
    manager = DreameMapMowerMapManager(SimpleNamespace(cloud=cloud))
    monkeypatch.setattr(map_manager_module.time, "time", lambda: 1000)

    assert manager._get_file_url("map.bin") is None
    assert manager._get_file_url("map.bin") == (
        "https://maps.example/map?signature=recovered"
    )
    assert cloud.get_interim_file_url.call_count == 2


@pytest.mark.parametrize("request_pending", [False, True])
@pytest.mark.parametrize("restored_map", [False, True])
def test_partial_frame_waits_for_base_map(
    monkeypatch: pytest.MonkeyPatch, request_pending: bool, restored_map: bool
) -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._latest_map_id = 7
    manager._map_request_time = 1000 if request_pending else None
    if restored_map:
        manager._map_data = MapData()
        manager._map_data.restored_map = True
        manager._current_map_id = 7
        manager._current_frame_id = 1
    partial = MapDataPartial()
    partial.map_id = 7
    partial.frame_id = 2
    partial.frame_type = MapFrameType.P.value
    request_base = Mock(return_value=True)
    monkeypatch.setattr(manager, "_request_i_map", request_base)

    assert manager._add_map_data(partial) is True
    assert manager._unqueue_partial_map(7, 2) is partial
    assert manager._map_data is None
    assert manager._current_frame_id is None
    assert request_base.call_count == (0 if request_pending else 1)


@pytest.mark.parametrize("latest_timestamp", [None, 1700000000000])
def test_map_without_timestamp_preserves_known_ordering(
    latest_timestamp: int | None,
) -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    manager._latest_map_timestamp_ms = latest_timestamp
    manager._latest_map_id = 8 if latest_timestamp is not None else None

    partial = manager._decode_map_partial(_encoded_map_frame(None))

    assert partial is not None
    assert partial.timestamp_ms is None
    assert partial.map_id == 7
    assert manager._latest_map_timestamp_ms == latest_timestamp
    assert manager._latest_map_id == (8 if latest_timestamp is not None else 7)


@pytest.mark.parametrize("wire_timestamp", [None, 1000])
def test_map_timestamp_uses_request_time_when_missing_or_uptime(
    wire_timestamp: int | None,
) -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    partial = manager._decode_map_partial(
        _encoded_map_frame(wire_timestamp), 1700000000000
    )

    assert partial is not None
    assert partial.timestamp_ms == 1700000000000
    assert manager._latest_map_timestamp_ms == 1700000000000
    assert manager._latest_map_id == 7


@pytest.mark.parametrize(
    ("current_timestamp", "incoming_timestamp"),
    [(1700000000000, None), (None, 1700000000000), (None, None)],
)
def test_lower_frame_without_comparable_timestamps_preserves_current_map(
    current_timestamp: int | None, incoming_timestamp: int | None
) -> None:
    manager = DreameMapMowerMapManager(_DummyProtocol())
    current_map = MapData()
    manager._map_data = current_map
    manager._current_map_id = manager._latest_map_id = 7
    manager._current_frame_id = 11
    manager._current_timestamp_ms = current_timestamp
    partial = map_manager_module.DreameMowerMapDecoder.decode_map_partial(
        _encoded_map_frame(incoming_timestamp)
    )

    assert manager._add_map_data(partial) is True
    assert manager._map_data is current_map
    assert manager._current_frame_id == 11


@pytest.mark.parametrize("empty_map", [True, False])
def test_timestamp_free_map_refreshes_while_running(
    monkeypatch: pytest.MonkeyPatch, empty_map: bool,
) -> None:
    protocol = SimpleNamespace(
        dreame_cloud=False, cloud=SimpleNamespace(logged_in=True)
    )
    manager = DreameMapMowerMapManager(protocol)
    assert manager._add_raw_map_data(_encoded_map_frame(None)) is True
    assert manager._map_data is not None
    assert manager._current_timestamp_ms is None
    manager._map_data.empty_map = empty_map
    manager._device_running = True
    manager._available = True
    request_map = Mock(return_value=True)
    monkeypatch.setattr(manager, "_request_current_map", request_map)

    manager.update()

    request_map.assert_called_once_with()
    assert manager._available is True
