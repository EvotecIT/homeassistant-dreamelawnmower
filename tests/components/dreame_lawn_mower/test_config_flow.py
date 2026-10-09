"""Config-flow smoke tests for Dreame lawn mower."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

try:
    # Follow the serializer used by each supported Home Assistant generation.
    from probatio import to_field_list as serialize_schema
except ImportError:
    from voluptuous_serialize import convert as serialize_schema

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
    CONF_HOST,
    CONF_MAC,
    CONF_MAP_LABEL_SCALE,
    CONF_MAP_MARKER_IMAGE,
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_MOWING_PATH_STYLE,
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    CONF_MAP_SPOT_AREA_STYLE,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
    CONF_MODEL,
    CONF_NAME,
    CONF_NOTIFICATION_MODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
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
    DEFAULT_NOTIFICATION_MODE,
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
    # Config-flow tests do not exercise Home Assistant's external stream stack.
    hass.config.components.add("stream")
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
        for item in serialize_schema(
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
    serialized_schema = serialize_schema(
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

    serialized_schema = serialize_schema(
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
        "map_restart_preview": False,
        CONF_MAP_ROTATION: 90,
        CONF_MAP_THEME: DEFAULT_MAP_THEME,
        CONF_MAP_STROKE_SCALE: DEFAULT_MAP_STROKE_SCALE,
        CONF_MAP_MARKER_SCALE: DEFAULT_MAP_MARKER_SCALE,
        CONF_MAP_MARKER_IMAGE: "",
        CONF_MAP_SPOT_AREA_STYLE: "outline",
        CONF_MAP_MOWING_PATH_STYLE: "hidden",
        CONF_NOTIFICATION_MODE: DEFAULT_NOTIFICATION_MODE,
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
        CONF_NOTIFICATION_MODE,
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


def test_options_flow_replaces_unknown_notification_mode_default() -> None:
    flow = DreameLawnMowerOptionsFlow(
        SimpleNamespace(options={CONF_NOTIFICATION_MODE: "unknown"})
    )

    result = asyncio.run(flow.async_step_init())
    validated = result["data_schema"]({})

    assert validated[CONF_NOTIFICATION_MODE] == DEFAULT_NOTIFICATION_MODE


@pytest.mark.parametrize("loaded", [False, True], ids=["unloaded", "loaded"])
async def test_opt_out_removes_saved_preview(hass, loaded):
    from custom_components.dreame_lawn_mower.map_preview import (
        RestartMapPreview,
        preview_store,
    )

    rotations = {"7": 180}
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={
        "map_restart_preview": True, CONF_MAP_ROTATIONS: rotations,
    }, state=(
        config_entries.ConfigEntryState.LOADED if loaded
        else config_entries.ConfigEntryState.NOT_LOADED
    ))
    entry.add_to_hass(hass)
    if loaded:
        preview = RestartMapPreview(hass, entry.entry_id)
        entry.runtime_data = SimpleNamespace(map_restart_preview=preview)
    hass.config.components.add("stream")
    await preview_store(hass, entry.entry_id).async_save({"jpeg": "private-map"})
    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        initial["flow_id"],
        initial["data_schema"]({
            "map_restart_preview": False, CONF_MAP_ROTATION: "90",
        }),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["map_restart_preview"] is False
    assert entry.options["map_restart_preview"] is False
    assert entry.options[CONF_MAP_ROTATION] == 90
    assert entry.options[CONF_MAP_ROTATIONS] == rotations
    assert await preview_store(hass, entry.entry_id).async_load() is None
    if loaded:
        await preview.async_save(b"\xff\xd8\xfflate-preview", "test-scope")
        assert await preview_store(hass, entry.entry_id).async_load() is None


async def test_discovery_receives_home_assistant_shared_session(hass, monkeypatch):
    from unittest.mock import AsyncMock

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.dreame_lawn_mower.api import DreameLawnMowerClient

    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}
    shared = async_get_clientsession(hass)
    assert discover.await_args.kwargs["session"] is shared
    assert not shared.closed


@pytest.mark.parametrize("source", ["user", "reauth"])
@pytest.mark.parametrize("failure_kind", ["connection", "authentication", "two-factor"])
async def test_native_discovery_failure_preserves_saved_entry(
    hass, monkeypatch, source, failure_kind,
):
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.api import (
        DreameLawnMowerAuthError,
        DreameLawnMowerClient,
        DreameLawnMowerConnectionError,
        DreameLawnMowerTwoFactorRequiredError,
    )

    hass.config.components.add("stream")

    failures = {
        "authentication": DreameLawnMowerAuthError(
            "Cloud authentication failed: HTTP 403"
        ),
        "connection": DreameLawnMowerConnectionError("Cloud timed out"),
        "two-factor": DreameLawnMowerTwoFactorRequiredError("https://example.invalid/2fa"),
    }
    failure = failures[failure_kind]
    discover = AsyncMock(side_effect=failure)
    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    original = {
        CONF_ACCOUNT_TYPE: ACCOUNT_TYPE_DREAME, CONF_COUNTRY: "eu",
        CONF_PASSWORD: "saved", CONF_USERNAME: "saved@example.invalid",
        CONF_DID: "device-1",
    }
    entry = MockConfigEntry(domain=DOMAIN, data=original, unique_id="device-1")
    if source == "user":
        result = await _start_user_flow(hass)
    else:
        entry.add_to_hass(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id},
            data=original,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_COUNTRY: "eu", CONF_PASSWORD: "unverified",
             CONF_USERNAME: "replacement@example.invalid"},
        )
    assert result["type"] is FlowResultType.FORM
    expected = {
        "authentication": "cannot_auth", "connection": "cannot_connect",
        "two-factor": "2fa_required",
    }[failure_kind]
    assert result["errors"] == {"base": expected}
    assert entry.data == original
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    discover.side_effect = None
    discover.return_value = [_FakeDevice()]
    verified = {
        CONF_USERNAME: "verified@example.invalid", CONF_PASSWORD: "verified",
        CONF_COUNTRY: "eu",
    }
    if source == "user":
        verified[CONF_ACCOUNT_TYPE] = ACCOUNT_TYPE_DREAME
    recovered = await hass.config_entries.flow.async_configure(
        result["flow_id"], verified,
    )
    await hass.async_block_till_done()
    retry_call = discover.await_args
    assert retry_call is not None
    assert retry_call.kwargs["username"] == verified[CONF_USERNAME]
    assert retry_call.kwargs["password"] == verified[CONF_PASSWORD]
    if source == "user":
        assert recovered["type"] is FlowResultType.CREATE_ENTRY
        assert recovered["data"][CONF_PASSWORD] == "verified"
        reload_entry.assert_not_awaited()
    else:
        assert recovered["type"] is FlowResultType.ABORT
        assert recovered["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "verified"
        reload_entry.assert_awaited_once_with(entry.entry_id)


async def test_reauth_recovers_when_original_mower_returns_and_refreshes_only_its_entry(
    hass, monkeypatch,
):
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.api import DreameLawnMowerClient

    hass.config.components.add("stream")
    original = {
        CONF_ACCOUNT_TYPE: ACCOUNT_TYPE_DREAME, CONF_COUNTRY: "eu",
        CONF_PASSWORD: "saved", CONF_USERNAME: "saved@example.invalid",
        CONF_DID: "device-1", CONF_HOST: "old.example.invalid",
    }
    entry = MockConfigEntry(domain=DOMAIN, data=original, unique_id="device-1")
    entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN, data={**original, CONF_DID: "device-2"}, unique_id="device-2",
    )
    other.add_to_hass(hass)
    other_original = dict(other.data)
    selected = _FakeDevice(name="Updated mower")
    selected.host, selected.token = "updated.example.invalid", "updated-token"
    selected.country = "us"
    discover = AsyncMock(side_effect=[
        [_FakeDevice(did="device-2")],
        [_FakeDevice(did="device-2"), selected],
    ])
    monkeypatch.setattr(DreameLawnMowerClient, "async_discover_devices", discover)
    reload_entry = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    initial = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_REAUTH,
                         "entry_id": entry.entry_id}, data=original,
    )
    credentials = {
        CONF_USERNAME: "updated@example.invalid", CONF_PASSWORD: "updated",
        CONF_COUNTRY: "us",
    }
    missing = initial
    assert missing["type"] is FlowResultType.FORM
    assert missing["errors"] == {"base": "no_devices"}
    assert entry.data == original
    reload_entry.assert_not_awaited()
    result = await hass.config_entries.flow.async_configure(
        missing["flow_id"], credentials,
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert dict(entry.data) == {
        **original, **credentials, CONF_HOST: selected.host, CONF_MAC: selected.mac,
        CONF_MODEL: selected.model, CONF_NAME: selected.name,
        CONF_TOKEN: selected.token,
    }
    assert entry.unique_id == "device-1"
    assert other.data == other_original
    reload_entry.assert_awaited_once_with(entry.entry_id)
