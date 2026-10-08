"""Entry lifetime and API isolation through Home Assistant config entries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import (
    _async_cleanup_failed_setup,
    async_unload_entry,
)
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.runtime_data import (
    get_coordinator,
    iter_coordinators,
)
from custom_components.dreame_lawn_mower.services import _coordinator_from_call

OWNER = "custom_components.dreame_lawn_mower"


@pytest.mark.parametrize("unload_ok", [False, True])
@pytest.mark.parametrize("another_entry", [False, True])
async def test_unload_retains_owner_until_platforms_release(
    hass, unload_ok, another_entry
):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(loaded_platforms=(), async_shutdown=AsyncMock())
    entry.runtime_data = coordinator
    hass.data[DOMAIN] = {}
    if another_entry:
        other = MockConfigEntry(domain=DOMAIN)
        other.add_to_hass(hass)
        other.runtime_data = SimpleNamespace()
    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=unload_ok),
        ),
        patch(f"{OWNER}.async_unload_services", AsyncMock()) as unload_services,
    ):
        assert await async_unload_entry(hass, entry) is unload_ok
        assert (get_coordinator(hass, entry.entry_id) is coordinator) is not unload_ok
        assert coordinator.async_shutdown.await_count == int(unload_ok)
        assert unload_services.await_count == int(unload_ok and not another_entry)


@pytest.mark.parametrize("assigned", [False, True])
async def test_failed_setup_releases_only_its_own_runtime(hass, assigned):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(async_shutdown=AsyncMock())
    if assigned:
        entry.runtime_data = coordinator
    await _async_cleanup_failed_setup(hass, entry, coordinator)
    assert get_coordinator(hass, entry.entry_id) is None
    coordinator.async_shutdown.assert_awaited_once_with()


async def test_service_targets_use_only_current_integration_entries(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = Mock(spec=DreameLawnMowerCoordinator)
    entry.runtime_data = coordinator
    foreign = MockConfigEntry(domain="other")
    foreign.add_to_hass(hass)
    foreign.runtime_data = Mock(spec=DreameLawnMowerCoordinator)
    assert list(iter_coordinators(hass)) == [coordinator]
    assert (
        _coordinator_from_call(hass, ServiceCall(hass, DOMAIN, "test", {}))
        is coordinator
    )
    for entry_id in (foreign.entry_id, "missing"):
        with pytest.raises(HomeAssistantError):
            _coordinator_from_call(
                hass, ServiceCall(hass, DOMAIN, "test", {"entry_id": entry_id})
            )
    del entry.runtime_data
    with pytest.raises(HomeAssistantError):
        _coordinator_from_call(hass, ServiceCall(hass, DOMAIN, "test", {}))


async def test_remove_after_failed_unload_drains_retained_storage_owners(hass):
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.dreame_lawn_mower.map_preview import RestartMapPreview
    from custom_components.dreame_lawn_mower.observation_checkpoint import (
        ObservationCheckpoint,
    )

    entry = MockConfigEntry(domain=DOMAIN, state=ConfigEntryState.FAILED_UNLOAD)
    entry.add_to_hass(hass)
    preview = Mock(spec=RestartMapPreview)
    checkpoint = Mock(spec=ObservationCheckpoint)
    entry.runtime_data = SimpleNamespace(
        map_restart_preview=preview,
        observation_checkpoint=checkpoint,
    )
    result = await hass.config_entries.async_remove(entry.entry_id)
    assert result["require_restart"]
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    preview.async_remove.assert_awaited_once_with()
    checkpoint.async_remove.assert_awaited_once_with()
