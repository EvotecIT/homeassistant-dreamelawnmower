"""Private, bounded restart previews; never restored as live mower telemetry."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import time
from io import BytesIO
from typing import Any

from homeassistant.helpers.storage import Store
from PIL import Image

from .const import DOMAIN

CONF_MAP_RESTART_PREVIEW = "map_restart_preview"
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_AGE_SECONDS = 24 * 60 * 60


def preview_store(hass: Any, entry_id: str) -> Store:
    """Keep one private HA-managed record per configuration entry."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.map_preview", private=True)


def preview_scope(device_id: str, render_context: tuple[Any, ...]) -> str:
    """Bind previews to the mower, selected map, and presentation settings."""
    payload = json.dumps([device_id, render_context], sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


async def async_remove_restart_preview(hass: Any, entry_id: str) -> None:
    """Honor opt-out without requiring a successfully loaded coordinator."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    preview = getattr(coordinator, "map_restart_preview", None)
    if isinstance(preview, RestartMapPreview):
        await preview.async_remove()
    else:
        await preview_store(hass, entry_id).async_remove()


def encode_preview(
    image: bytes, scope: str, *, now: float | None = None
) -> dict[str, Any] | None:
    """Persist only a bounded JPEG and its identity/age, not URLs or telemetry."""
    if len(image) > MAX_PREVIEW_BYTES or not image.startswith(b"\xff\xd8\xff"):
        return None
    return {
        "scope": scope,
        "saved_at": time.time() if now is None else now,
        "jpeg": base64.b64encode(image).decode("ascii"),
    }


def decode_preview(
    record: Any, scope: str, *, now: float | None = None
) -> tuple[bytes, float] | None:
    """Reject corrupt, expired, future-dated, or differently scoped records."""
    if not isinstance(record, dict) or record.get("scope") != scope:
        return None
    saved_at = record.get("saved_at")
    if not isinstance(saved_at, (float, int)):
        return None
    age = (time.time() if now is None else now) - saved_at
    if not 0 <= age <= MAX_PREVIEW_AGE_SECONDS:
        return None
    encoded = record.get("jpeg")
    if not isinstance(encoded, str) or len(encoded) > (MAX_PREVIEW_BYTES + 2) // 3 * 4:
        return None
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(image) > MAX_PREVIEW_BYTES or not image.startswith(b"\xff\xd8\xff"):
        return None
    return image, float(saved_at)


def verified_preview(record: Any, scope: str) -> tuple[bytes, float] | None:
    """Validate stored pixels in an executor before exposing a restart image."""
    decoded = decode_preview(record, scope)
    if decoded is None:
        return None
    try:
        with Image.open(BytesIO(decoded[0])) as image:
            if image.format != "JPEG" or image.width * image.height > 16_000_000:
                return None
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    return decoded


class RestartMapPreview:
    """Own lazy restore and rate-limited persistence for one primary map camera."""

    def __init__(self, hass: Any, entry_id: str):
        self._hass = hass
        self._store = preview_store(hass, entry_id)
        self._loaded = False
        self._record: Any = None
        self._last_saved = 0.0
        self._last_scope: str | None = None
        self._enabled = True
        self._write_lock = asyncio.Lock()

    async def async_load(self, scope: str) -> tuple[bytes, float] | None:
        """Read at most once; a later known map may match the cached record."""
        if not self._enabled:
            return None
        if not self._loaded:
            self._record = await self._store.async_load()
            self._loaded = True
        restored = await self._hass.async_add_executor_job(
            verified_preview, self._record, scope
        )
        return restored if self._enabled else None

    async def async_save(self, image: bytes, scope: str) -> None:
        """Save no more than once per minute, except for a different map/style."""
        async with self._write_lock:
            if self._enabled:
                await self._async_save_locked(image, scope)

    async def _async_save_locked(self, image: bytes, scope: str) -> None:
        now = time.monotonic()
        if scope == self._last_scope and now - self._last_saved < 60:
            return
        record = encode_preview(image, scope)
        if record is None:
            return
        task = asyncio.create_task(self._store.async_save(record))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        self._last_scope = scope
        self._last_saved = now

    async def async_remove(self) -> None:
        """Opt out before draining writes so late work cannot recreate the file."""
        self._enabled = False
        async with self._write_lock:
            await self._store.async_remove()
            self._record = None
