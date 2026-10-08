"""Entry runtime publication, targeting and retirement contracts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower import (
    _async_cleanup_failed_setup,
)
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.mowing_map_api import (
    MOWING_MAP_API_KEY,
    MowingMapAPI,
)
from custom_components.dreame_lawn_mower.point_cloud_api import (
    POINT_CLOUD_API_DATA_KEY,
    DreameLawnMowerPointCloudAPI,
)
from custom_components.dreame_lawn_mower.runtime_data import (
    get_coordinator,
    iter_coordinators,
)
from custom_components.dreame_lawn_mower.services import _coordinator_from_call
from tests.runtime_fixtures import runtime_hass


def owner():
    return Mock(
        spec=DreameLawnMowerCoordinator,
        loaded_platforms=(), async_shutdown=AsyncMock(),
    )


def test_lookup_tracks_current_runtime_and_rejects_other_domains():
    first, replacement = owner(), owner()
    hass = runtime_hass({"mower": first, "foreign": owner()})
    hass.config_entries.entries["foreign"].domain = "other_integration"
    assert get_coordinator(hass, "missing") is None
    assert get_coordinator(hass, "foreign") is None
    assert list(iter_coordinators(hass)) == [first]
    entry = hass.config_entries.entries["mower"]
    entry.runtime_data = replacement
    assert get_coordinator(hass, "mower") is replacement
    del entry.runtime_data
    assert get_coordinator(hass, "mower") is None
    assert list(iter_coordinators(hass)) == []


def test_services_target_entries_and_require_disambiguation():
    first, second = owner(), owner()
    hass = runtime_hass({"first": first, "second": second})
    call = SimpleNamespace(data={"entry_id": "second"})
    assert _coordinator_from_call(hass, call) is second
    with pytest.raises(HomeAssistantError, match="Multiple"):
        _coordinator_from_call(hass, SimpleNamespace(data={}))
    del hass.config_entries.entries["second"].runtime_data
    assert _coordinator_from_call(hass, SimpleNamespace(data={})) is first
    with pytest.raises(HomeAssistantError, match="No Dreame"):
        _coordinator_from_call(hass, call)
    del hass.config_entries.entries["first"].runtime_data
    with pytest.raises(HomeAssistantError, match="No Dreame"):
        _coordinator_from_call(hass, SimpleNamespace(data={}))


def test_failed_setup_withdraws_runtime_and_purges_both_api_owners():
    async def run():
        coordinator = owner()
        hass = runtime_hass({"mower": coordinator})
        entry = hass.config_entries.entries["mower"]
        points = Mock(spec=DreameLawnMowerPointCloudAPI)
        maps = Mock(spec=MowingMapAPI)
        hass.data[DOMAIN] = {
            POINT_CLOUD_API_DATA_KEY: points, MOWING_MAP_API_KEY: maps,
        }
        services = AsyncMock()
        with patch(
            "custom_components.dreame_lawn_mower.async_unload_services", services,
        ):
            await _async_cleanup_failed_setup(hass, entry, coordinator)
        assert get_coordinator(hass, "mower") is None
        points.purge_entry.assert_called_once_with("mower")
        maps.purge_entry.assert_awaited_once_with("mower")
        coordinator.async_shutdown.assert_awaited_once_with()
        services.assert_awaited_once_with(hass)

    asyncio.run(run())


def test_failed_old_setup_cannot_withdraw_replacement_runtime():
    async def run():
        old, replacement = owner(), owner()
        hass = runtime_hass({"mower": replacement})
        services = AsyncMock()
        with patch(
            "custom_components.dreame_lawn_mower.async_unload_services", services,
        ):
            await _async_cleanup_failed_setup(
                hass, hass.config_entries.entries["mower"], old,
            )
        assert get_coordinator(hass, "mower") is replacement
        old.async_shutdown.assert_awaited_once_with()
        replacement.async_shutdown.assert_not_awaited()
        services.assert_not_awaited()

    asyncio.run(run())
