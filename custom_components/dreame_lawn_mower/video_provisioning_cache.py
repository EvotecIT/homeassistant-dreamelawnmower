"""Private persisted provisioning for cached XP2P restart."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.xp2p_config import (
    XP2P_PROTOCOL_AUTO,
    DreameLawnMowerXp2pDeviceConfig,
    async_resolve_xp2p_device_config,
)
from .runtime_data import DreameLawnMowerConfigEntry
from .video_cache_storage import async_save_video_cache

_STORAGE_VERSION = 1
PROVISIONING_CACHE_SOURCE = "video_provisioning_cache"


def _cache_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Use the same entry-scoped key for loading and removal."""
    return Store[dict[str, Any]](
        hass,
        _STORAGE_VERSION,
        f"{DOMAIN}.video_provisioning.{entry_id}",
        private=True,
    )


async def async_remove_video_provisioning_cache(
    hass: HomeAssistant, entry: DreameLawnMowerConfigEntry,
) -> None:
    """Remove persisted data even if this entry never loaded successfully."""
    owner = getattr(
        getattr(entry, "runtime_data", None), "video_provisioning_cache", None,
    )
    if isinstance(owner, DreameLawnMowerVideoProvisioningCache):
        await owner.async_remove()
    else:
        await _cache_store(hass, entry.entry_id).async_remove()


class DreameLawnMowerVideoProvisioningCache:
    """Persist the minimum private XP2P material for one exact mower."""

    def __init__(self, hass: HomeAssistant, *, entry_id: str, did: str) -> None:
        self._hass = hass
        self._store = _cache_store(hass, entry_id)
        self._write_lock = asyncio.Lock()
        self._removed = False
        self._did = did
        self.loaded = False
        self.inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None
        self.device_config: DreameLawnMowerXp2pDeviceConfig | None = None
        self._runtime_input_config: tuple[
            DreameLawnMowerCameraStreamRuntimeInputs,
            DreameLawnMowerXp2pDeviceConfig,
        ] | None = None

    async def async_close(self) -> None:
        """Fence late writes and drain disk work while preserving reload data."""
        self._removed = True
        async with self._write_lock:
            pass

    async def async_remove(self) -> None:
        """Fence late writes and remove data when the entry is deleted."""
        self._removed = True
        async with self._write_lock:
            await self._store.async_remove()
            self.inputs = None
            self.device_config = None
            self._runtime_input_config = None

    async def async_load(self) -> None:
        """Load complete provisioning only when it belongs to this mower."""
        payload = await self._store.async_load()
        self.inputs, self.device_config = _decode_provisioning_payload(
            payload,
            expected_did=self._did,
        )
        self.loaded = True

    async def async_save(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        device_config: DreameLawnMowerXp2pDeviceConfig,
    ) -> None:
        """Save only fields consumed by the native runtime, never cloud tokens."""
        async with self._write_lock:
            if self._removed:
                return
            if inputs.did != self._did or not inputs.ready:
                return
            cached = _cached_inputs(inputs)
            payload: dict[str, Any] = {
                "did": self._did,
                "inputs": {
                    "channel_id": cached.channel_id,
                    "product_id": cached.product_id,
                    "device_name": cached.device_name,
                    "p2p_info": cached.p2p_info,
                    "secret_id": cached.secret_id,
                    "secret_key": cached.secret_key,
                    "app_id": cached.app_id,
                    "app_secret": cached.app_secret,
                    "stream_channel": cached.stream_channel,
                    "live_command": cached.live_command,
                    "flv_path_template": cached.flv_path_template,
                },
                "device_config": {
                    "server": device_config.server,
                    "ip": device_config.ip,
                    "port": device_config.port,
                    "protocol_type": device_config.protocol_type,
                    "cross": device_config.cross,
                },
            }
            await async_save_video_cache(self._store, payload)
            self._runtime_input_config = (inputs, device_config)
            self.inputs = cached
            self.device_config = device_config

    async def async_clear(self) -> None:
        """Discard persisted and in-memory provisioning after failed playback."""
        await self._store.async_remove()
        self.inputs = None
        self.device_config = None
        self._runtime_input_config = None

    def resolve_device_config(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pDeviceConfig | None:
        """Return persisted or just-resolved configuration for these inputs."""
        if inputs.source == PROVISIONING_CACHE_SOURCE:
            return self.device_config
        if (
            self._runtime_input_config is not None
            and self._runtime_input_config[0] is inputs
        ):
            return self._runtime_input_config[1]
        return None

    async def async_stage_fresh_device_config(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pDeviceConfig:
        """Fetch configuration before starting the native worker."""
        config = await async_resolve_xp2p_device_config(
            inputs, session=async_get_clientsession(self._hass),
        )
        if not self._removed:
            self._runtime_input_config = (inputs, config)
        return config

    def resolve_for_transport(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        auto: bool,
    ) -> DreameLawnMowerXp2pDeviceConfig:
        """Return cached/fresh configuration with the requested route policy."""
        config = self.resolve_device_config(inputs)
        if config is None:
            raise RuntimeError("XP2P device configuration was not staged.")
        if not auto:
            return config
        return DreameLawnMowerXp2pDeviceConfig(
            server=config.server,
            ip=config.ip,
            port=config.port,
            protocol_type=XP2P_PROTOCOL_AUTO,
            cross=False,
        )


def _decode_provisioning_payload(
    payload: Mapping[str, Any] | None,
    *,
    expected_did: str,
) -> tuple[
    DreameLawnMowerCameraStreamRuntimeInputs | None,
    DreameLawnMowerXp2pDeviceConfig | None,
]:
    """Validate one private cache payload without accepting raw cloud data."""
    if not isinstance(payload, Mapping) or payload.get("did") != expected_did:
        return None, None
    raw_inputs = payload.get("inputs")
    raw_config = payload.get("device_config")
    if not isinstance(raw_inputs, Mapping) or not isinstance(raw_config, Mapping):
        return None, None
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source=PROVISIONING_CACHE_SOURCE,
        did=expected_did,
        channel_id=_text(raw_inputs.get("channel_id")),
        product_id=_text(raw_inputs.get("product_id")),
        device_name=_text(raw_inputs.get("device_name")),
        p2p_info=_text(raw_inputs.get("p2p_info")),
        secret_id=_text(raw_inputs.get("secret_id")),
        secret_key=_text(raw_inputs.get("secret_key")),
        app_id=_text(raw_inputs.get("app_id")),
        app_secret=_text(raw_inputs.get("app_secret")),
        stream_channel=raw_inputs.get("stream_channel", 0),
        live_command=_text(raw_inputs.get("live_command")) or "action=live",
        flv_path_template=(
            _text(raw_inputs.get("flv_path_template"))
            or "ipc.flv?action=live&channel={channel}&quality=high&_crypto=on"
        ),
    )
    port = raw_config.get("port")
    protocol_type = raw_config.get("protocol_type")
    if port is None or protocol_type is None:
        return None, None
    try:
        config = DreameLawnMowerXp2pDeviceConfig(
            server=_text(raw_config.get("server")) or "",
            ip=_text(raw_config.get("ip")) or "",
            port=int(port),
            protocol_type=int(protocol_type),
            cross=bool(raw_config.get("cross", False)),
        )
    except (TypeError, ValueError):
        return None, None
    if not inputs.ready or not 0 < config.port <= 65535:
        return None, None
    return inputs, config


def _cached_inputs(
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
) -> DreameLawnMowerCameraStreamRuntimeInputs:
    return DreameLawnMowerCameraStreamRuntimeInputs(
        source=PROVISIONING_CACHE_SOURCE,
        did=inputs.did,
        channel_id=inputs.channel_id,
        product_id=inputs.product_id,
        device_name=inputs.device_name,
        p2p_info=inputs.p2p_info,
        secret_id=inputs.secret_id,
        secret_key=inputs.secret_key,
        app_id=inputs.app_id,
        app_secret=inputs.app_secret,
        stream_channel=inputs.stream_channel,
        live_command=inputs.live_command,
        flv_path_template=inputs.flv_path_template,
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
