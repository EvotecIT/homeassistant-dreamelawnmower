"""Authenticated cloud presence while a stored mower cannot answer RPC."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .cloud_presence import cloud_device_online
from .exceptions import DreameLawnMowerConnectionError
from .models import DreameLawnMowerSnapshot

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient


class MowerOfflineError(DreameLawnMowerConnectionError):
    """Authenticated device information explicitly reports the mower offline."""


def offline_snapshot(
    client: DreameLawnMowerClient, info: Mapping[str, Any],
) -> DreameLawnMowerSnapshot | None:
    """Keep cached readings unavailable and never invent physical mower state."""
    if cloud_device_online(info) is not False:
        return None
    previous = client._latest_snapshot or DreameLawnMowerSnapshot(
        descriptor=client.descriptor,
        available=False,
        state="unknown",
        state_name="Unknown",
        activity="unknown",
        device_connected=False,
        cloud_connected=False,
    )
    snapshot = replace(
        previous,
        available=False,
        online=False,
        device_connected=False,
    )
    with client._runtime_geometry_lock:
        client._latest_snapshot = snapshot
    return snapshot


async def async_cached_offline_snapshot(
    client: DreameLawnMowerClient,
) -> DreameLawnMowerSnapshot | None:
    """Recheck presence at the shared bounded rate before retrying startup."""
    previous = client._latest_snapshot
    if previous is None or previous.online is not False:
        return None
    info = await client._async_get_cached_cloud_device_info()
    return offline_snapshot(client, info) if isinstance(info, Mapping) else None


def require_online_startup(
    client: DreameLawnMowerClient, info: Mapping[str, Any],
) -> None:
    """Only an explicit authenticated offline result may bypass initial RPC."""
    if offline_snapshot(client, info) is not None:
        client._latest_cloud_device_info = dict(info)
        client._cloud_device_info_refreshed_at = time.monotonic()
        raise MowerOfflineError("The mower is offline.")
