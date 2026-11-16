"""An offline first snapshot still loads HA platforms and connectivity entities."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower.api import DreameLawnMowerClient
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)


async def test_offline_entry_loads_status_without_fast_retries_or_optional_reads(
    hass, hass_client,
):
    assert await async_setup_component(hass, "http", {})
    await hass_client()
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, minor_version=2, unique_id="stored-mower",
        data={
            "did": "stored-mower", "name": "Garden", "model": "mova.mower.g2529c",
            "account_type": "mova", "country": "eu",
            "username": "garden@example.invalid", "password": "secret",
        },
    )
    entry.add_to_hass(hass)
    snapshot = DreameLawnMowerSnapshot(
        descriptor=DreameLawnMowerDescriptor(
            did="stored-mower", name="Garden", model="mova.mower.g2529c",
            display_model="LiDAX", account_type="mova", country="eu",
        ),
        available=False, online=False, device_connected=False,
        state="unknown", state_name="Unknown", activity="unknown",
    )
    # Native HTTP admission/recovery is proved in tests/test_offline_startup.py.
    # Here the real coordinator, setup manager and all entity platforms consume
    # that client boundary. Any optional cloud/device read is unexpected.
    with (
        patch("homeassistant.setup.async_process_deps_reqs", AsyncMock()),
        patch.object(
            DreameLawnMowerClient, "async_refresh", AsyncMock(return_value=snapshot),
        ),
        patch.object(DreameLawnMowerClient, "_async_cloud_read", AsyncMock(
            side_effect=AssertionError("Offline setup must not hydrate mower metadata"),
        )) as cloud_read,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        coordinator = entry.runtime_data
        assert coordinator.last_update_success is True
        assert coordinator.connection_degraded is True
        assert coordinator._connectivity_retry_task is None
        assert coordinator._metadata_refresh_task is None
        registry = er.async_get(hass)
        online_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, "stored-mower_online",
        )
        mower_id = registry.async_get_entity_id(
            "lawn_mower", DOMAIN, "stored-mower_mower",
        )
        assert online_id is not None
        assert mower_id is not None
        assert hass.states.get(online_id).state == STATE_OFF
        assert hass.states.get(mower_id).state == STATE_UNAVAILABLE
        cloud_read.assert_not_awaited()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert coordinator._shutting_down is True
        assert coordinator._connectivity_retry_task is None
