"""Cross-surface capability evidence must not interfere with ordinary maps."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.camera import DreameLawnMowerMapCamera
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
    parse_pcd_metadata,
)
from custom_components.dreame_lawn_mower.feature_capabilities import (
    coordinator_feature_capabilities,
)
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache
from custom_components.dreame_lawn_mower.point_cloud_api import (
    DreameLawnMowerPointCloudAPI,
)
from custom_components.dreame_lawn_mower.reporting import build_coordinator_diagnostics


def _coordinator(download):
    coordinator = DreameLawnMowerCoordinator.__new__(DreameLawnMowerCoordinator)
    coordinator.data = SimpleNamespace(
        descriptor=SimpleNamespace(model="mova.mower.g2583"),
        capabilities=("map", "lidar_navigation"), raw_info={},
    )
    coordinator.client = SimpleNamespace(async_download_app_map_point_cloud=download)
    coordinator.entry = SimpleNamespace(entry_id="test-entry")
    coordinator.app_maps = {"current_map_index": 0, "maps": [{"idx": 0}]}
    coordinator.batch_device_data = {}
    coordinator.selected_map_index = 0
    # Command serialization is covered by its own tests; no live command runner.
    coordinator.async_run_command = None
    return coordinator


def _map_attributes(coordinator):
    camera = DreameLawnMowerMapCamera.__new__(DreameLawnMowerMapCamera)
    camera.coordinator = coordinator
    camera._map_cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    camera._preview_saved_at = None
    return camera.extra_state_attributes


def test_validated_download_promotes_unknown_mower_across_map_and_diagnostics():
    content = (
        b"VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
        b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n1 2 3\n"
    )

    async def download(**kwargs):
        return DreameLawnMowerPointCloudDownload(
            map_index=kwargs["map_index"], content=content,
            metadata=parse_pcd_metadata(content), source="generated",
        )

    coordinator = _coordinator(download)
    before = _map_attributes(coordinator)
    assert before["feature_capabilities"]["point_cloud"]["state"] == "unknown"
    api = DreameLawnMowerPointCloudAPI(
        SimpleNamespace(data={DOMAIN: {"test-entry": coordinator}})
    )
    asyncio.run(api.async_get("test-entry", 0))
    after = _map_attributes(coordinator)
    assert after["point_cloud_api_path"] == before["point_cloud_api_path"]
    assert after["mowing_map_api_path"] == before["mowing_map_api_path"]
    assert after["feature_capabilities"]["point_cloud"] == {
        "state": "supported", "source": "observed"
    }
    coordinator.data.capabilities = ()
    assert build_coordinator_diagnostics(coordinator)["feature_capabilities"] == (
        after["feature_capabilities"]
    )


def test_failure_never_proves_unsupported_or_erases_previous_export_evidence():
    async def download(**_kwargs):
        raise DreameLawnMowerPointCloudError(
            "No published object", code="point_cloud_not_published", retryable=False
        )

    coordinator = _coordinator(download)
    for observed in (False, True):
        if observed:
            coordinator.record_feature_capability_observed("point_cloud")
        api = DreameLawnMowerPointCloudAPI(
            SimpleNamespace(data={DOMAIN: {"test-entry": coordinator}})
        )
        with pytest.raises(DreameLawnMowerPointCloudError):
            asyncio.run(api.async_get("test-entry", 0))
        capability = coordinator_feature_capabilities(coordinator)["point_cloud"]
        assert capability["state"] == ("supported" if observed else "unknown")
        assert _map_attributes(coordinator)["mowing_map_api_path"]
