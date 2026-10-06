"""Owned map-view composition using native app and vector reads."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

from .client_app_map_view import app_map_view, preferred_map_view
from .client_map_helpers import _map_view_current_app_map_index
from .client_refresh import _run_state_worker
from .client_state_reads import async_read_device_state
from .models import DreameLawnMowerMapView

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .map_visuals import MapRenderStyle


async def async_map_view(
    client: DreameLawnMowerClient,
    *,
    timeout: float,
    interval: float,
    label_scale: float,
    style: MapRenderStyle | None,
) -> DreameLawnMowerMapView:
    cancelled = Event()

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
            app_view = await async_read_device_state(
                client,
                lambda _device: DreameLawnMowerMapView(
                    source="app_action_map",
                    error=error,
                    diagnostics=client._safe_map_diagnostics(
                        source="app_action_map", reason="app_action_map_failed"
                    ),
                ),
                refresh=False,
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
        # The remaining legacy fallback owns its device/network worker until it
        # completes. Native app/vector success never starts that legacy path.
        legacy = await _run_state_worker(
            lambda: client._sync_refresh_legacy_map_view(
                timeout,
                interval,
                label_scale=label_scale,
                style=style,
            ),
            cancelled,
        )
        legacy = client._with_fallback_app_maps(legacy, app_view)
        return legacy if legacy.available or legacy.image_png is not None else app_view

    return await client._async_cloud_read(read)
