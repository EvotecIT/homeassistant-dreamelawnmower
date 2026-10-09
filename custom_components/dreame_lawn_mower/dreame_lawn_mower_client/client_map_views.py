"""Owned map-view composition using native app and vector reads."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from .client_app_map_view import app_map_view, preferred_map_view
from .client_map_helpers import _map_view_current_app_map_index
from .client_state_reads import async_read_device_state
from .exceptions import DeviceException, DreameLawnMowerConnectionError
from .models import DreameLawnMowerMapView

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .map_types import MapData
    from .map_visuals import MapRenderStyle


async def async_map_view(
    client: DreameLawnMowerClient,
    *,
    timeout: float,
    interval: float,
    label_scale: float,
    style: MapRenderStyle | None,
) -> DreameLawnMowerMapView:
    async def read(_cloud: DreameCloudSession) -> DreameLawnMowerMapView:
        try:
            maps = await client.async_get_app_maps(include_payload=True)
            app_view = await async_read_device_state(
                client,
                lambda _device: app_map_view(
                    client,
                    maps,
                    legacy_error=None,
                    legacy_reason="app_action_map_primary",
                    label_scale=label_scale,
                    style=style,
                ),
                refresh=False,
            )
        except Exception as err:  # noqa: BLE001 - retain map-source failure evidence
            error = f"Legacy map unavailable; app map failed: {err}"
            app_view = DreameLawnMowerMapView(
                source="app_action_map", error=error,
            )
        vector_view = client._with_fallback_app_maps(
            await client.async_refresh_vector_map_view(
                label_scale=label_scale,
                style=style,
                current_map_index=_map_view_current_app_map_index(app_view),
            ),
            app_view,
        )
        preferred = preferred_map_view(client, app_view, vector_view)
        if preferred is not None:
            return preferred
        legacy = await client._async_refresh_legacy_map_view(
            timeout, interval, label_scale=label_scale, style=style,
        )
        legacy = client._with_fallback_app_maps(legacy, app_view)
        return legacy if legacy.available or legacy.image_png is not None else app_view

    return await client._async_cloud_read(read)


async def async_legacy_map_view(
    client: DreameLawnMowerClient,
    *, timeout: float, interval: float,
    label_scale: float, style: MapRenderStyle | None,
) -> DreameLawnMowerMapView:
    """Refresh natively, await legacy map arrival, and reuse the existing renderer."""
    async def read(_cloud: DreameCloudSession) -> DreameLawnMowerMapView:
        def prepare(device: DreameMowerDevice) -> tuple[MapData | None, bool]:
            current = device.status.current_map
            pending = current is None and device._map_manager is not None
            if pending:
                device.update_map()
            return current, pending

        try:
            map_data, pending = await async_read_device_state(
                client, prepare, refresh=True,
            )
            deadline = time.monotonic() + max(timeout, 0)
            while pending and map_data is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(max(interval, 0.1), remaining))
                if time.monotonic() >= deadline:
                    break
                map_data = await async_read_device_state(
                    client, lambda device: device.status.current_map, refresh=False,
                    deadline=deadline,
                )
            if pending and map_data is None:
                return DreameLawnMowerMapView(
                    source="legacy_current_map",
                    error="No map data returned by the legacy current-map path.",
                )
            return await async_read_device_state(
                client,
                lambda _device: client._render_legacy_map_view(
                    map_data, label_scale=label_scale, style=style,
                ),
                refresh=False,
                deadline=deadline if timeout > 0 else None,
            )
        except (DeviceException, DreameLawnMowerConnectionError) as err:
            # State may be unavailable after the failed read. Diagnostics are
            # optional; reporting the original error must not acquire it again.
            return DreameLawnMowerMapView(
                source="legacy_current_map", error=str(err),
            )

    return await client._async_cloud_read(read)
