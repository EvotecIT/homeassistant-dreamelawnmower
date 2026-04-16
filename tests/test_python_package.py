"""Regression checks for the standalone mower Python package."""

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerMapSummary,
    map_summary_from_map_data,
)
from dreame_lawn_mower_client.client import (
    DreameLawnMowerClient as ClientFromModule,
)
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary as MapSummaryFromModule,
)


def test_public_package_exports_client() -> None:
    assert DreameLawnMowerClient is ClientFromModule


def test_public_package_exports_map_helpers() -> None:
    assert DreameLawnMowerMapSummary is MapSummaryFromModule
    assert callable(map_summary_from_map_data)


def test_public_package_client_has_cloud_probe_helpers() -> None:
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_info")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_properties")
