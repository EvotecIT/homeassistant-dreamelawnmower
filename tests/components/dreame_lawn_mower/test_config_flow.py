"""Config-flow smoke tests for Dreame lawn mower."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.dreame_lawn_mower.const import (
    ACCOUNT_TYPE_DREAME,
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)


class _FakeDevice:
    def __init__(self) -> None:
        self.did = "device-1"
        self.name = "Garage Mower"
        self.model = "dreame.mower.g3255"
        self.display_model = "A2 Pro"
        self.account_type = ACCOUNT_TYPE_DREAME
        self.country = "eu"
        self.host = "example.invalid"
        self.mac = "AA:BB:CC:DD:EE:FF"
        self.token = " "

    @property
    def title(self) -> str:
        return "Garage Mower (A2 Pro)"

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
    assert result["title"] == "Garage Mower (A2 Pro)"
    assert result["data"][CONF_DID] == "device-1"
    assert result["data"][CONF_MODEL] == "dreame.mower.g3255"
    assert result["data"][CONF_NAME] == "Garage Mower"
