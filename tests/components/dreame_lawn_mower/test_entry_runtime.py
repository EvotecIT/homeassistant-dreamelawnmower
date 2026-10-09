"""Real HA config-entry registry and last-entry runtime retirement."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import async_setup, async_unload_entry
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.runtime_data import get_coordinator
from custom_components.dreame_lawn_mower.services import SERVICE_REMOTE_CONTROL_STOP


async def test_real_entries_keep_independent_runtime_and_last_service_lifetime(hass):
    await async_setup(hass, {})
    entries = [MockConfigEntry(domain=DOMAIN, unique_id=f"mower-{i}") for i in range(2)]
    coordinators = [
        SimpleNamespace(loaded_platforms=(), async_shutdown=AsyncMock())
        for _ in entries
    ]
    for entry, coordinator in zip(entries, coordinators, strict=True):
        entry.add_to_hass(hass)
        entry.runtime_data = coordinator
        assert get_coordinator(hass, entry.entry_id) is coordinator
    assert await async_unload_entry(hass, entries[0])
    assert get_coordinator(hass, entries[0].entry_id) is None
    assert get_coordinator(hass, entries[1].entry_id) is coordinators[1]
    assert hass.services.has_service(DOMAIN, SERVICE_REMOTE_CONTROL_STOP)
    assert await async_unload_entry(hass, entries[1])
    assert hass.services.has_service(DOMAIN, SERVICE_REMOTE_CONTROL_STOP)
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN, SERVICE_REMOTE_CONTROL_STOP, {}, blocking=True,
        )
    assert raised.value.translation_key == "no_loaded_entries"
    for coordinator in coordinators:
        coordinator.async_shutdown.assert_awaited_once_with()
