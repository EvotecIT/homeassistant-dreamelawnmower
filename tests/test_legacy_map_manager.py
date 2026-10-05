"""Regression checks for tolerated legacy map-manager payload shapes."""

from __future__ import annotations

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
