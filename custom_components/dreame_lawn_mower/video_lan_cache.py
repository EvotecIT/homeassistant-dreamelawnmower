"""Durable non-secret identity and endpoint cache for same-LAN video."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .dreame_lawn_mower_client.lan_video import DreameLawnMowerLanVideoEndpoint
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)
from .video_cache_storage import async_save_video_cache

_STORAGE_VERSION = 1


def _cache_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Use the same entry-scoped key for loading and removal."""
    return Store[dict[str, Any]](
        hass,
        _STORAGE_VERSION,
        f"{DOMAIN}.video_lan.{entry_id}",
    )


async def async_remove_video_lan_cache(
    hass: HomeAssistant, entry: ConfigEntry,
) -> None:
    """Remove persisted data even if this entry never loaded successfully."""
    owner = getattr(
        getattr(entry, "runtime_data", None), "video_lan_cache", None,
    )
    if isinstance(owner, DreameLawnMowerVideoLanCache):
        await owner.async_remove()
    else:
        await _cache_store(hass, entry.entry_id).async_remove()


class DreameLawnMowerVideoLanCache:
    """Persist only stable identity and the last direct endpoint for one mower."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry_id: str,
        did: str,
    ) -> None:
        self._store = _cache_store(hass, entry_id)
        self._write_lock = asyncio.Lock()
        self._removed = False
        self._did = did
        self.loaded = False
        self.inputs: DreameLawnMowerCameraStreamRuntimeInputs | None = None
        self.endpoint: DreameLawnMowerLanVideoEndpoint | None = None

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
            self.endpoint = None

    async def async_load(self) -> None:
        """Load a cache only when it belongs to this exact mower."""
        payload = await self._store.async_load()
        inputs, endpoint = _decode_cache_payload(payload, expected_did=self._did)
        self.inputs = inputs
        self.endpoint = endpoint
        self.loaded = True

    async def async_save_identity(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> None:
        """Persist the cloud-provisioned identity without credentials/p2pInfo."""
        if not inputs.lan_identity_ready:
            return
        self.inputs = _lan_only_inputs(inputs)
        if self.endpoint is not None and (
            self.endpoint.product_id != self.inputs.product_id
            or self.endpoint.device_name != self.inputs.device_name
        ):
            self.endpoint = None
        await self._async_save()

    async def async_save_session(
        self,
        session: DreameLawnMowerXp2pLiveStreamSession,
    ) -> None:
        """Persist the endpoint proven by a successful direct-LAN session."""
        inputs = self.inputs
        if (
            inputs is None
            or not session.lan_endpoint_address
            or not session.lan_endpoint_port
        ):
            return
        self.endpoint = DreameLawnMowerLanVideoEndpoint(
            product_id=str(inputs.product_id),
            device_name=str(inputs.device_name),
            address=session.lan_endpoint_address,
            port=session.lan_endpoint_port,
            response_version="cached",
            source="proven_session",
        )
        await self._async_save()

    async def async_clear_endpoint(self) -> None:
        """Forget a direct endpoint that failed a health-checked restart."""
        if self.endpoint is None:
            return
        self.endpoint = None
        await self._async_save()

    async def _async_save(self) -> None:
        async with self._write_lock:
            if self._removed:
                return
            if self.inputs is None:
                return
            payload: dict[str, Any] = {
                "did": self._did,
                "product_id": self.inputs.product_id,
                "device_name": self.inputs.device_name,
                "stream_channel": self.inputs.stream_channel,
            }
            if self.endpoint is not None:
                payload["endpoint"] = self.endpoint.as_dict()
            await async_save_video_cache(self._store, payload)


def _decode_cache_payload(
    payload: Mapping[str, Any] | None,
    *,
    expected_did: str,
) -> tuple[
    DreameLawnMowerCameraStreamRuntimeInputs | None,
    DreameLawnMowerLanVideoEndpoint | None,
]:
    if not isinstance(payload, Mapping) or payload.get("did") != expected_did:
        return None, None
    product_id = _text(payload.get("product_id"))
    device_name = _text(payload.get("device_name"))
    if product_id is None or device_name is None:
        return None, None
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="lan_video_cache",
        did=expected_did,
        product_id=product_id,
        device_name=device_name,
        stream_channel=payload.get("stream_channel", 0),
    )
    endpoint_payload = payload.get("endpoint")
    endpoint = _decode_endpoint(endpoint_payload, inputs)
    return inputs, endpoint


def _decode_endpoint(
    payload: Any,
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
) -> DreameLawnMowerLanVideoEndpoint | None:
    if not isinstance(payload, Mapping):
        return None
    address = _text(payload.get("address"))
    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError):
        return None
    if address is None or not _valid_cached_address(address) or not 0 < port <= 65535:
        return None
    if payload.get("product_id") != inputs.product_id:
        return None
    if payload.get("device_name") != inputs.device_name:
        return None
    return DreameLawnMowerLanVideoEndpoint(
        product_id=str(inputs.product_id),
        device_name=str(inputs.device_name),
        address=address,
        port=port,
        response_version=_text(payload.get("response_version")) or "cached",
        source=_text(payload.get("source")) or "cache",
    )


def _lan_only_inputs(
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
) -> DreameLawnMowerCameraStreamRuntimeInputs:
    return DreameLawnMowerCameraStreamRuntimeInputs(
        source="lan_video_cache",
        did=inputs.did,
        channel_id=inputs.xp2p_id,
        product_id=inputs.product_id,
        device_name=inputs.device_name,
        stream_channel=inputs.stream_channel,
        live_command=inputs.live_command,
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _valid_cached_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.version == 4
        and not address.is_unspecified
        and not address.is_multicast
        and not address.is_loopback
    )
