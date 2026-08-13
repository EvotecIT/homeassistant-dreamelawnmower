"""Config-flow smoke tests for Dreame lawn mower."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import async_migrate_entry
from custom_components.dreame_lawn_mower.config_flow import (
    DreameLawnMowerConfigFlow,
    DreameLawnMowerOptionsFlow,
)
from custom_components.dreame_lawn_mower.const import (
    ACCOUNT_TYPE_DREAME,
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_MAP_LABEL_SCALE,
    CONF_MAP_MARKER_IMAGE,
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_MOWING_PATH_STYLE,
    CONF_MAP_ROTATION,
    CONF_MAP_SPOT_AREA_STYLE,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VIDEO_RETENTION,
    CONF_VIDEO_TRANSPORT,
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    CONF_XP2P_RUNNER_MODE,
    DEFAULT_MAP_MARKER_SCALE,
    DEFAULT_MAP_MOWING_PATH_STYLE,
    DEFAULT_MAP_SPOT_AREA_STYLE,
    DEFAULT_MAP_STROKE_SCALE,
    DEFAULT_MAP_THEME,
    DEFAULT_VIDEO_RETENTION,
    DEFAULT_VIDEO_TRANSPORT,
    DOMAIN,
    VIDEO_TRANSPORT_LAN,
    XP2P_RUNNER_MODE_PROCESS,
)


class _FakeDevice:
    def __init__(
        self,
        *,
        did: str = "device-1",
        name: str = "Garage Mower",
    ) -> None:
        self.did = did
        self.name = name
        self.model = "dreame.mower.g3255"
        self.display_model = "A3 AWD Pro"
        self.account_type = ACCOUNT_TYPE_DREAME
        self.country = "eu"
        self.host = "example.invalid"
        self.mac = "AA:BB:CC:DD:EE:FF"
        self.token = " "

    @property
    def title(self) -> str:
        return f"{self.name} ({self.display_model})"

    @property
    def unique_id(self) -> str:
        return self.did


async def _start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_ACCOUNT_TYPE: ACCOUNT_TYPE_DREAME,
            CONF_COUNTRY: "eu",
            CONF_PASSWORD: "secret",
            CONF_USERNAME: "user@example.com",
        },
    )


def _serialized_schema(result) -> dict[str, dict]:
    return {
        item["name"]: item
        for item in voluptuous_serialize.convert(
            result["data_schema"],
            custom_serializer=cv.custom_serializer,
        )
    }


def _assert_localized_selector(schema: dict[str, dict], key: str) -> None:
    assert schema[key]["selector"]["select"]["translation_key"] == key


@pytest.mark.asyncio
async def test_migration_enables_only_integration_disabled_primary_map(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-1",
        data={CONF_DID: "device-1"},
        version=1,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    primary_map = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "device-1_map",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    live_path_map = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "device-1_live_path_map",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 1
    assert entry.minor_version == 2
    assert registry.async_get(primary_map.entity_id).disabled_by is None
    assert (
        registry.async_get(live_path_map.entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )


@pytest.mark.asyncio
async def test_migration_preserves_user_disabled_primary_map(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-1",
        data={CONF_DID: "device-1"},
        version=1,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    primary_map = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "device-1_map",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 1
    assert entry.minor_version == 2
    assert (
        registry.async_get(primary_map.entity_id).disabled_by
        is er.RegistryEntryDisabler.USER
    )


@pytest.mark.asyncio
async def test_migration_preserves_disable_new_entities_preference(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-1",
        data={CONF_DID: "device-1"},
        version=1,
        minor_version=1,
        pref_disable_new_entities=True,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    primary_map = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "device-1_map",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 1
    assert entry.minor_version == 2
    assert (
        registry.async_get(primary_map.entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )


@pytest.mark.asyncio
async def test_migration_accepts_future_minor_version_without_changes(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-1",
        data={CONF_DID: "device-1"},
        version=1,
        minor_version=3,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 1
    assert entry.minor_version == 3


@pytest.mark.asyncio
async def test_user_flow_creates_entry(hass, monkeypatch) -> None:
    async def _fake_discover(**kwargs):
        return [_FakeDevice()]

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_discover_devices",
        _fake_discover,
    )

    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Mower (A3 AWD Pro)"
    assert result["data"][CONF_DID] == "device-1"
    assert result["data"][CONF_MODEL] == "dreame.mower.g3255"
    assert result["data"][CONF_NAME] == "Garage Mower"


@pytest.mark.asyncio
async def test_user_flow_lists_multiple_mowers_by_name(hass, monkeypatch) -> None:
    async def _fake_discover(**kwargs):
        return [
            _FakeDevice(did="device-1", name="Garden Mower"),
            _FakeDevice(did="device-2", name="Parents' Mower"),
        ]

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_discover_devices",
        _fake_discover,
    )

    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "device"
    serialized_schema = voluptuous_serialize.convert(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
    assert serialized_schema[0]["selector"]["select"]["options"] == [
        {"value": "device-1", "label": "Garden Mower (A3 AWD Pro)"},
        {"value": "device-2", "label": "Parents' Mower (A3 AWD Pro)"},
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "device-2"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Parents' Mower (A3 AWD Pro)"
    assert result["data"][CONF_DID] == "device-2"


@pytest.mark.asyncio
async def test_user_flow_keeps_mowers_with_the_same_name_distinct(
    hass, monkeypatch
) -> None:
    async def _fake_discover(**kwargs):
        return [
            _FakeDevice(did="device-1", name="Garden Mower"),
            _FakeDevice(did="device-2", name="Garden Mower"),
        ]

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_discover_devices",
        _fake_discover,
    )

    result = await _start_user_flow(hass)

    serialized_schema = voluptuous_serialize.convert(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
    assert serialized_schema[0]["selector"]["select"]["options"] == [
        {
            "value": "device-1",
            "label": "Garden Mower (A3 AWD Pro) - device-1",
        },
        {
            "value": "device-2",
            "label": "Garden Mower (A3 AWD Pro) - device-2",
        },
    ]


@pytest.mark.asyncio
async def test_user_flow_only_offers_unconfigured_mowers(hass, monkeypatch) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-1",
        data={CONF_DID: "device-1"},
    ).add_to_hass(hass)

    async def _fake_discover(**kwargs):
        return [
            _FakeDevice(did="device-1", name="Garden Mower"),
            _FakeDevice(did="device-2", name="Parents' Mower"),
        ]

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_discover_devices",
        _fake_discover,
    )

    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Parents' Mower (A3 AWD Pro)"
    assert result["data"][CONF_DID] == "device-2"


@pytest.mark.asyncio
async def test_user_flow_aborts_when_all_mowers_are_configured(
    hass, monkeypatch
) -> None:
    for did in ("device-1", "device-2"):
        MockConfigEntry(
            domain=DOMAIN,
            unique_id=did,
            data={CONF_DID: did},
        ).add_to_hass(hass)

    async def _fake_discover(**kwargs):
        return [
            _FakeDevice(did="device-1", name="Garden Mower"),
            _FakeDevice(did="device-2", name="Parents' Mower"),
        ]

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_discover_devices",
        _fake_discover,
    )

    result = await _start_user_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def test_options_flow_accepts_map_label_scale() -> None:
    flow = DreameLawnMowerOptionsFlow(SimpleNamespace(options={}))

    result = asyncio.run(flow.async_step_init())

    assert result["type"] is FlowResultType.FORM
    validated = result["data_schema"](
        {
            CONF_SCAN_INTERVAL: "45",
            CONF_MAP_LABEL_SCALE: "2.5",
            CONF_MAP_ROTATION: "90",
            CONF_MAP_SPOT_AREA_STYLE: "outline",
            CONF_MAP_MOWING_PATH_STYLE: "hidden",
        }
    )
    assert validated[CONF_SCAN_INTERVAL] == 45
    assert validated[CONF_MAP_LABEL_SCALE] == 2.5
    assert validated[CONF_MAP_ROTATION] == "90"
    assert validated[CONF_MAP_SPOT_AREA_STYLE] == "outline"
    assert validated[CONF_MAP_MOWING_PATH_STYLE] == "hidden"

    result = asyncio.run(flow.async_step_init(validated))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_SCAN_INTERVAL: 45,
        CONF_MAP_LABEL_SCALE: 2.5,
        CONF_MAP_ROTATION: 90,
        CONF_MAP_THEME: DEFAULT_MAP_THEME,
        CONF_MAP_STROKE_SCALE: DEFAULT_MAP_STROKE_SCALE,
        CONF_MAP_MARKER_SCALE: DEFAULT_MAP_MARKER_SCALE,
        CONF_MAP_MARKER_IMAGE: "",
        CONF_MAP_SPOT_AREA_STYLE: "outline",
        CONF_MAP_MOWING_PATH_STYLE: "hidden",
        CONF_VIDEO_RETENTION: DEFAULT_VIDEO_RETENTION,
        CONF_VIDEO_TRANSPORT: DEFAULT_VIDEO_TRANSPORT,
        CONF_XP2P_LIBRARY_PATH: "",
        CONF_XP2P_RUNNER_COMMAND: "",
        CONF_XP2P_RUNNER_MODE: XP2P_RUNNER_MODE_PROCESS,
    }


@pytest.mark.asyncio
async def test_every_localized_selector_is_wired_to_its_form(hass) -> None:
    user_flow = DreameLawnMowerConfigFlow()
    user_flow.hass = hass
    user_schema = _serialized_schema(await user_flow.async_step_user())
    _assert_localized_selector(user_schema, CONF_COUNTRY)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-1",
        data={
            CONF_ACCOUNT_TYPE: ACCOUNT_TYPE_DREAME,
            CONF_COUNTRY: "eu",
            CONF_DID: "device-1",
            CONF_PASSWORD: "secret",
            CONF_USERNAME: "user@example.com",
        },
    )
    entry.add_to_hass(hass)
    reauth_flow = DreameLawnMowerConfigFlow()
    reauth_flow.hass = hass
    reauth_flow.context = {
        "source": config_entries.SOURCE_REAUTH,
        "entry_id": entry.entry_id,
    }
    reauth_schema = _serialized_schema(await reauth_flow.async_step_reauth())
    _assert_localized_selector(reauth_schema, CONF_COUNTRY)

    options_flow = DreameLawnMowerOptionsFlow(SimpleNamespace(options={}))
    options_schema = _serialized_schema(await options_flow.async_step_init())
    for key in (
        CONF_MAP_ROTATION,
        CONF_MAP_THEME,
        CONF_MAP_SPOT_AREA_STYLE,
        CONF_MAP_MOWING_PATH_STYLE,
        CONF_VIDEO_RETENTION,
        CONF_VIDEO_TRANSPORT,
        CONF_XP2P_RUNNER_MODE,
    ):
        _assert_localized_selector(options_schema, key)


def test_options_flow_defaults_to_clean_vector_map_layers() -> None:
    flow = DreameLawnMowerOptionsFlow(SimpleNamespace(options={}))

    result = asyncio.run(flow.async_step_init())
    validated = result["data_schema"]({})

    assert validated[CONF_MAP_SPOT_AREA_STYLE] == DEFAULT_MAP_SPOT_AREA_STYLE
    assert validated[CONF_MAP_MOWING_PATH_STYLE] == DEFAULT_MAP_MOWING_PATH_STYLE


def test_options_flow_preserves_integer_map_rotation_storage() -> None:
    flow = DreameLawnMowerOptionsFlow(SimpleNamespace(options={CONF_MAP_ROTATION: 270}))

    result = asyncio.run(flow.async_step_init())
    validated = result["data_schema"]({})

    assert validated[CONF_MAP_ROTATION] == "270"

    result = asyncio.run(flow.async_step_init(validated))

    assert result["data"][CONF_MAP_ROTATION] == 270


def test_options_flow_replaces_prerelease_lan_only_default() -> None:
    flow = DreameLawnMowerOptionsFlow(
        SimpleNamespace(options={CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_LAN})
    )

    result = asyncio.run(flow.async_step_init())
    validated = result["data_schema"]({})

    assert validated[CONF_VIDEO_TRANSPORT] == DEFAULT_VIDEO_TRANSPORT


def test_options_flow_replaces_unknown_video_retention_default() -> None:
    flow = DreameLawnMowerOptionsFlow(
        SimpleNamespace(options={CONF_VIDEO_RETENTION: "unknown"})
    )

    result = asyncio.run(flow.async_step_init())
    validated = result["data_schema"]({})

    assert validated[CONF_VIDEO_RETENTION] == DEFAULT_VIDEO_RETENTION
