"""Action discovery and targeting through Home Assistant's service registry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.runtime_data import get_coordinator
from custom_components.dreame_lawn_mower.services import SERVICE_REMOTE_CONTROL_STOP


async def test_actions_are_discoverable_without_a_loaded_entry(hass):
    # Action registration does not need HTTP listeners or native video decoding.
    with patch("homeassistant.setup.async_process_deps_reqs", AsyncMock()):
        assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, SERVICE_REMOTE_CONTROL_STOP)
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN, SERVICE_REMOTE_CONTROL_STOP, {}, blocking=True
        )
    assert raised.value.translation_key == "no_loaded_entries"


@pytest.mark.parametrize(
    "state",
    [ConfigEntryState.NOT_LOADED, ConfigEntryState.SETUP_ERROR,
     ConfigEntryState.FAILED_UNLOAD],
)
async def test_actions_reject_retained_runtime_that_is_not_loaded(hass, state):
    # Failed unload retains runtime for cleanup, but it must not accept commands.
    from custom_components.dreame_lawn_mower import async_setup

    await async_setup(hass, {})
    entry = MockConfigEntry(domain=DOMAIN, state=state)
    entry.add_to_hass(hass)
    stop = AsyncMock()
    coordinator = SimpleNamespace(
        client=SimpleNamespace(async_remote_control_stop=stop),
    )
    entry.runtime_data = coordinator
    assert get_coordinator(hass, entry.entry_id) is coordinator
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN, SERVICE_REMOTE_CONTROL_STOP,
            {"entry_id": entry.entry_id}, blocking=True,
        )
    assert raised.value.translation_key == "entry_not_loaded"
    assert raised.value.translation_placeholders == {"entry_id": entry.entry_id}
    stop.assert_not_awaited()
    assert get_coordinator(hass, entry.entry_id) is coordinator


async def test_actions_require_a_current_unambiguous_mower_target(hass):
    from custom_components.dreame_lawn_mower import async_setup

    await async_setup(hass, {})
    entries = [MockConfigEntry(domain=DOMAIN, state=ConfigEntryState.LOADED)
               for _ in range(2)]
    coordinators = [SimpleNamespace(
        client=SimpleNamespace(async_remote_control_stop=AsyncMock()),
        async_request_refresh=AsyncMock(),
    ) for _ in entries]
    for entry, coordinator in zip(entries, coordinators, strict=True):
        entry.add_to_hass(hass)
        entry.runtime_data = coordinator
    foreign = MockConfigEntry(domain="other", state=ConfigEntryState.LOADED)
    foreign.add_to_hass(hass)
    foreign.runtime_data = coordinators[0]
    for data, key in [({}, "entry_id_required"),
                      ({"entry_id": foreign.entry_id}, "entry_not_found"),
                      ({"entry_id": "missing"}, "entry_not_found")]:
        with pytest.raises(ServiceValidationError) as raised:
            await hass.services.async_call(
                DOMAIN, SERVICE_REMOTE_CONTROL_STOP, data, blocking=True,
            )
        assert raised.value.translation_key == key
    for coordinator in coordinators:
        coordinator.client.async_remote_control_stop.assert_not_awaited()
    await hass.services.async_call(
        DOMAIN, SERVICE_REMOTE_CONTROL_STOP,
        {"entry_id": entries[1].entry_id}, blocking=True,
    )
    coordinators[1].client.async_remote_control_stop.assert_awaited_once_with()
    coordinators[1].async_request_refresh.assert_awaited_once_with()
    del entries[1].runtime_data
    await hass.services.async_call(
        DOMAIN, SERVICE_REMOTE_CONTROL_STOP, {}, blocking=True,
    )
    coordinators[0].client.async_remote_control_stop.assert_awaited_once_with()
    coordinators[0].async_request_refresh.assert_awaited_once_with()
