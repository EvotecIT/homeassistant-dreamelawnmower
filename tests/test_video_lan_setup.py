from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady

import custom_components.dreame_lawn_mower as integration_module
from custom_components.dreame_lawn_mower.const import (
    CONF_VIDEO_TRANSPORT,
    DOMAIN,
    VIDEO_TRANSPORT_LAN,
)


def test_proven_cached_lan_only_mode_can_set_up_while_cloud_is_unavailable() -> None:
    async def _run() -> tuple[bool, tuple[Platform, ...], tuple[Platform, ...], bool]:
        class _Coordinator:
            def __init__(self, _hass: object, _entry: object) -> None:
                self.client = SimpleNamespace(descriptor=SimpleNamespace(did="did-1"))
                self.video_lan_cache = None

            async def async_config_entry_first_refresh(self) -> None:
                raise ConfigEntryNotReady("offline")

            async def async_shutdown(self) -> None:
                return None

        class _Cache:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.inputs = None
                self.endpoint = None

            async def async_load(self) -> None:
                self.inputs = object()
                self.endpoint = object()

        class _ProvisioningCache:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.inputs = None
                self.device_config = None

            async def async_load(self) -> None:
                return None

        forwarded_platforms: tuple[Platform, ...] = ()
        unloaded_platforms: tuple[Platform, ...] = ()

        class _ConfigEntries:
            async def async_forward_entry_setups(
                self,
                _entry: object,
                _platforms: object,
            ) -> None:
                nonlocal forwarded_platforms
                forwarded_platforms = tuple(_platforms)

            async def async_unload_platforms(
                self,
                _entry: object,
                _platforms: object,
            ) -> bool:
                nonlocal unloaded_platforms
                unloaded_platforms = tuple(_platforms)
                return True

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

        async def _unload_services(_hass: object) -> None:
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
                "DreameLawnMowerVideoProvisioningCache",
                _ProvisioningCache,
            ),
            patch.object(
                integration_module,
                "async_setup_services",
                _setup_services,
            ),
            patch.object(
                integration_module,
                "async_unload_services",
                _unload_services,
            ),
        ):
            result = await integration_module.async_setup_entry(hass, _Entry())
            coordinator = hass.data[DOMAIN]["entry-1"]
            cached = coordinator.video_lan_cache.inputs is not None
            assert await integration_module.async_unload_entry(hass, _Entry()) is True

        return (
            result,
            forwarded_platforms,
            unloaded_platforms,
            cached,
        )

    assert asyncio.run(_run()) == (
        True,
        (Platform.CAMERA,),
        (Platform.CAMERA,),
        True,
    )
