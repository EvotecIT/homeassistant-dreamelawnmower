from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.exceptions import ConfigEntryNotReady

import custom_components.dreame_lawn_mower as integration_module
from custom_components.dreame_lawn_mower.const import (
    CONF_VIDEO_TRANSPORT,
    DOMAIN,
    VIDEO_TRANSPORT_LAN,
)


def test_proven_cached_lan_only_mode_can_set_up_while_cloud_is_unavailable() -> None:
    async def _run() -> tuple[bool, bool, bool]:
        class _Coordinator:
            def __init__(self, _hass: object, _entry: object) -> None:
                self.client = SimpleNamespace(descriptor=SimpleNamespace(did="did-1"))
                self.video_lan_cache = None

            async def async_config_entry_first_refresh(self) -> None:
                raise ConfigEntryNotReady("offline")

        class _Cache:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.inputs = None
                self.endpoint = None

            async def async_load(self) -> None:
                self.inputs = object()
                self.endpoint = object()

        forwarded = False

        class _ConfigEntries:
            async def async_forward_entry_setups(
                self,
                _entry: object,
                _platforms: object,
            ) -> None:
                nonlocal forwarded
                forwarded = True

        class _Entry:
            entry_id = "entry-1"
            options = {CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_LAN}

            @staticmethod
            def add_update_listener(_listener: object) -> object:
                return object()

            @staticmethod
            def async_on_unload(_callback: object) -> None:
                return None

        hass = SimpleNamespace(data={}, config_entries=_ConfigEntries())

        async def _setup_services(_hass: object) -> None:
            return None

        with (
            patch.object(
                integration_module,
                "DreameLawnMowerCoordinator",
                _Coordinator,
            ),
            patch.object(integration_module, "DreameLawnMowerVideoLanCache", _Cache),
            patch.object(
                integration_module,
                "async_setup_services",
                _setup_services,
            ),
        ):
            result = await integration_module.async_setup_entry(hass, _Entry())

        coordinator = hass.data[DOMAIN]["entry-1"]
        return result, forwarded, coordinator.video_lan_cache.inputs is not None

    assert asyncio.run(_run()) == (True, True, True)
