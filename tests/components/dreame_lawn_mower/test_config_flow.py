"""Config-flow smoke tests for Dreame lawn mower."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.dreame_lawn_mower.config_flow import DreameLawnMowerOptionsFlow
from custom_components.dreame_lawn_mower.const import (
    ACCOUNT_TYPE_DREAME,
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_MAP_LABEL_SCALE,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VIDEO_TRANSPORT,
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    CONF_XP2P_RUNNER_MODE,
    DEFAULT_VIDEO_TRANSPORT,
    DOMAIN,
    VIDEO_TRANSPORT_LAN,
    XP2P_RUNNER_MODE_PROCESS,
)


class _FakeDevice:
    def __init__(self) -> None:
        self.did = "device-1"
        self.name = "Garage Mower"
        self.model = "dreame.mower.g3255"
        self.display_model = "A3 AWD Pro"
        self.account_type = ACCOUNT_TYPE_DREAME
        self.country = "eu"
        self.host = "example.invalid"
        self.mac = "AA:BB:CC:DD:EE:FF"
        self.token = " "

    @property
    def title(self) -> str:
        return "Garage Mower (A3 AWD Pro)"

    @property
    def unique_id(self) -> str:
        return self.did


@pytest.mark.asyncio
async def test_user_flow_creates_entry(hass, monkeypatch) -> None:
    async def _fake_discover(**kwargs):
        return [_FakeDevice()]

    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.config_flow.async_discover_devices",
        _fake_discover,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_ACCOUNT_TYPE: ACCOUNT_TYPE_DREAME,
            CONF_COUNTRY: "eu",
            CONF_PASSWORD: "secret",
            CONF_USERNAME: "user@example.com",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage Mower (A3 AWD Pro)"
    assert result["data"][CONF_DID] == "device-1"
    assert result["data"][CONF_MODEL] == "dreame.mower.g3255"
    assert result["data"][CONF_NAME] == "Garage Mower"


def test_options_flow_accepts_map_label_scale() -> None:
    flow = DreameLawnMowerOptionsFlow(SimpleNamespace(options={}))

    result = asyncio.run(flow.async_step_init())

    assert result["type"] is FlowResultType.FORM
    validated = result["data_schema"](
        {
            CONF_SCAN_INTERVAL: "45",
            CONF_MAP_LABEL_SCALE: "2.5",
        }
    )
    assert validated[CONF_SCAN_INTERVAL] == 45
    assert validated[CONF_MAP_LABEL_SCALE] == 2.5

    result = asyncio.run(flow.async_step_init(validated))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_SCAN_INTERVAL: 45,
        CONF_MAP_LABEL_SCALE: 2.5,
        CONF_VIDEO_TRANSPORT: DEFAULT_VIDEO_TRANSPORT,
        CONF_XP2P_LIBRARY_PATH: "",
        CONF_XP2P_RUNNER_COMMAND: "",
        CONF_XP2P_RUNNER_MODE: XP2P_RUNNER_MODE_PROCESS,
    }


def test_options_flow_replaces_prerelease_lan_only_default() -> None:
    flow = DreameLawnMowerOptionsFlow(
        SimpleNamespace(options={CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_LAN})
    )

    result = asyncio.run(flow.async_step_init())
    validated = result["data_schema"]({})

    assert validated[CONF_VIDEO_TRANSPORT] == DEFAULT_VIDEO_TRANSPORT
