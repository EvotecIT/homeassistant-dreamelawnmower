"""Connection repair through Home Assistant's actual config-flow manager."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import _async_update_listener
from custom_components.dreame_lawn_mower.api import (
    DreameLawnMowerAuthError,
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    DreameLawnMowerTwoFactorRequiredError,
)
from custom_components.dreame_lawn_mower.const import (
    ACCOUNT_TYPE_DREAME,
    ACCOUNT_TYPE_MOVA,
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_HOST,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.dreame_lawn_mower.option_updates import EntryUpdateSnapshot

try:
    from probatio import to_field_list as serialize_schema
except ImportError:
    from voluptuous_serialize import convert as serialize_schema


def _serialized_schema(result):
    return {
        item["name"]: item
        for item in serialize_schema(
            result["data_schema"],
            custom_serializer=cv.custom_serializer,
        )
    }


REPLACEMENT = {
    CONF_USERNAME: "repaired@example.invalid",
    CONF_PASSWORD: "replacement-secret",
    CONF_COUNTRY: "us",
}


def _entry(hass, account_type=ACCOUNT_TYPE_DREAME, unique_id="mower-1"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        title="My chosen garden name",
        data={
            CONF_ACCOUNT_TYPE: account_type,
            CONF_COUNTRY: "eu",
            CONF_DID: "mower-1",
            CONF_USERNAME: "saved@example.invalid",
            CONF_PASSWORD: "saved-secret",
            CONF_HOST: "old.example.invalid",
            "preserved_setting": "existing-value",
        },
        options={CONF_SCAN_INTERVAL: 45},
    )
    entry.add_to_hass(hass)
    return entry


def _device(account_type=ACCOUNT_TYPE_DREAME, did="mower-1"):
    return DreameLawnMowerDescriptor(
        did=did,
        name="Renamed in vendor app",
        model="dreame.mower.g3255",
        display_model="A3 AWD Pro",
        account_type=account_type,
        country="us",
        host="new.example.invalid",
        mac="AA:BB:CC:DD:EE:FF",
        token="replacement-token",
    )


async def _start(hass, entry):
    # Discovery is exercised; stream dependency initialization belongs to setup tests.
    hass.config.components.add("stream")
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )


def _assert_saved(entry, original):
    assert dict(entry.data) == original
    assert entry.title == "My chosen garden name"
    assert dict(entry.options) == {CONF_SCAN_INTERVAL: 45}


@pytest.mark.parametrize("account_type", [ACCOUNT_TYPE_DREAME, ACCOUNT_TYPE_MOVA])
async def test_reconfiguration_preserves_identity_and_registry(
    hass, monkeypatch, account_type
):
    entry = _entry(hass, account_type)
    original = dict(entry.data)
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    registered_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "mower-1")},
    )
    registered_entity = registry.async_get_or_create(
        "lawn_mower",
        DOMAIN,
        "mower-1",
        config_entry=entry,
        device_id=registered_device.id,
        suggested_object_id="my_garden",
    )
    selected = _device(account_type)
    discover = AsyncMock(return_value=[_device(account_type, "mower-2"), selected])
    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)

    initial = await _start(hass, entry)
    assert initial["type"] is FlowResultType.FORM
    assert initial["step_id"] == "reconfigure"
    assert initial["errors"] == {}
    schema = _serialized_schema(initial)
    assert schema[CONF_USERNAME]["default"] == original[CONF_USERNAME]
    assert schema[CONF_COUNTRY]["default"] == "eu"
    assert schema[CONF_COUNTRY]["selector"]["select"]["translation_key"] == CONF_COUNTRY
    assert "default" not in schema[CONF_PASSWORD]
    assert CONF_ACCOUNT_TYPE not in schema
    discover.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], REPLACEMENT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(entry.data) == {
        **original,
        **REPLACEMENT,
        CONF_HOST: selected.host,
        CONF_MAC: selected.mac,
        CONF_MODEL: selected.model,
        CONF_NAME: selected.name,
        CONF_TOKEN: selected.token,
    }
    assert entry.unique_id == "mower-1"
    assert entry.title == "My chosen garden name"
    assert dict(entry.options) == {CONF_SCAN_INTERVAL: 45}
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert registry.async_get(registered_entity.entity_id) == registered_entity
    assert device_registry.async_get(registered_device.id) == registered_device
    reload_entry.assert_awaited_once_with(entry.entry_id)
    shared = async_get_clientsession(hass)
    discover.assert_awaited_once_with(
        username=REPLACEMENT[CONF_USERNAME],
        password=REPLACEMENT[CONF_PASSWORD],
        country="us",
        account_type=account_type,
        session=shared,
    )
    assert not shared.closed


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
@pytest.mark.parametrize("credentials_changed", [True, False])
async def test_connection_repair_reloads_once_with_the_registered_listener(
    hass,
    monkeypatch,
    source,
    credentials_changed,
):
    entry = _entry(hass)
    selected = _device()
    if not credentials_changed:
        # A recovered account can validate the same credentials and metadata.
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                **REPLACEMENT,
                CONF_HOST: selected.host,
                CONF_MAC: selected.mac,
                CONF_MODEL: selected.model,
                CONF_NAME: selected.name,
                CONF_TOKEN: selected.token,
            },
        )
    entry.runtime_data = SimpleNamespace(
        applied_entry_update=EntryUpdateSnapshot.capture(entry),
        async_update_listeners=Mock(),
    )
    # Successful integration setup registers this actual listener with HA.
    remove_listener = entry.add_update_listener(_async_update_listener)
    entry.async_on_unload(remove_listener)
    monkeypatch.setattr(
        DreameLawnMowerClient,
        "async_discover_devices",
        AsyncMock(return_value=[selected]),
    )
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    hass.config.components.add("stream")
    initial = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": source, "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"],
        REPLACEMENT,
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == f"{source}_successful"
    assert entry.data[CONF_PASSWORD] == REPLACEMENT[CONF_PASSWORD]
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (DreameLawnMowerAuthError("Cloud login failed: HTTP 403"), "cannot_auth"),
        (DreameLawnMowerAuthError("Unsupported region"), "invalid_region"),
        (DreameLawnMowerConnectionError("Cloud timed out"), "cannot_connect"),
        (
            DreameLawnMowerTwoFactorRequiredError("https://example.invalid/2fa"),
            "2fa_required",
        ),
    ],
)
async def test_reconfiguration_failure_retains_configuration_and_can_retry(
    hass,
    monkeypatch,
    failure,
    error,
):
    entry = _entry(hass)
    original = dict(entry.data)
    discover = AsyncMock(side_effect=failure)
    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    initial = await _start(hass, entry)
    failed = await hass.config_entries.flow.async_configure(
        initial["flow_id"], REPLACEMENT
    )
    assert failed["type"] is FlowResultType.FORM
    assert failed["step_id"] == "reconfigure"
    assert failed["errors"] == {"base": error}
    assert "default" not in _serialized_schema(failed)[CONF_PASSWORD]
    _assert_saved(entry, original)
    reload_entry.assert_not_awaited()

    discover.side_effect = None
    discover.return_value = [_device()]
    retried = {**REPLACEMENT, CONF_PASSWORD: "verified-retry-secret"}
    result = await hass.config_entries.flow.async_configure(failed["flow_id"], retried)
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == retried[CONF_PASSWORD]
    assert discover.await_args.kwargs["password"] == retried[CONF_PASSWORD]
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("devices", [[], [_device(did="mower-2")]])
async def test_reconfiguration_requires_the_saved_mower(hass, monkeypatch, devices):
    entry = _entry(hass)
    original = dict(entry.data)
    discover = AsyncMock(return_value=devices)
    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    initial = await _start(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], REPLACEMENT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}
    _assert_saved(entry, original)
    reload_entry.assert_not_awaited()


async def test_reconfiguration_aborts_an_entry_identity_mismatch(hass, monkeypatch):
    entry = _entry(hass, unique_id="other-mower")
    original = dict(entry.data)
    monkeypatch.setattr(
        DreameLawnMowerClient,
        "async_discover_devices",
        AsyncMock(return_value=[_device()]),
    )
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    initial = await _start(hass, entry)
    result = await hass.config_entries.flow.async_configure(
        initial["flow_id"], REPLACEMENT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    _assert_saved(entry, original)
    reload_entry.assert_not_awaited()


async def test_cancelled_reconfiguration_does_not_save_or_reload(hass, monkeypatch):
    entry = _entry(hass)
    original = dict(entry.data)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def discover(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    initial = await _start(hass, entry)
    task = asyncio.create_task(
        hass.config_entries.flow.async_configure(initial["flow_id"], REPLACEMENT)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    _assert_saved(entry, original)
    reload_entry.assert_not_awaited()
    assert not async_get_clientsession(hass).closed
