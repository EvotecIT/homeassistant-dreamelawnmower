"""Native read-only camera discovery using the common capability policy."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .camera_probe import CAMERA_PROBE_PROPERTY_KEYS, build_camera_probe_payload
from .client_camera import (
    _cloud_user_feature_summary,
    camera_feature_support_from_device,
)
from .client_state_reads import async_read_device_state
from .exceptions import DreameLawnMowerConnectionError
from .models import DreameLawnMowerCameraFeatureSupport

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_probe_camera_sources(
    client: DreameLawnMowerClient, *, language: str, request_device_properties: bool,
) -> dict[str, object]:
    """Own refresh, cloud scanning and optional device probing as one operation."""
    from .client_camera_probe import async_camera_property_probe

    async def read(_cloud: DreameCloudSession) -> dict[str, object]:
        support = await async_camera_feature_support(
            client, refresh=True, include_cloud=True, language=language)
        cloud_properties = await client.async_scan_cloud_properties(
            keys=CAMERA_PROBE_PROPERTY_KEYS, siids=None, piid_start=1, piid_end=1,
            chunk_size=50, language=language, only_values=False,
        )
        device_properties = (
            await async_camera_property_probe(client)
            if request_device_properties else {"skipped": True}
        )
        return build_camera_probe_payload(
            descriptor=client._descriptor, support=support,
            cloud_properties=cloud_properties, device_properties=device_properties,
        )

    return await client._async_cloud_read(read)


async def async_camera_feature_support(
    client: DreameLawnMowerClient,
    *,
    refresh: bool,
    include_cloud: bool,
    language: str | None,
) -> DreameLawnMowerCameraFeatureSupport:
    """Own state and optional cloud discovery through caller cancellation/close."""
    async def read(_cloud: DreameCloudSession) -> DreameLawnMowerCameraFeatureSupport:
        support = await async_read_device_state(
            client, camera_feature_support_from_device, refresh=refresh
        )
        if not include_cloud:
            return support
        try:
            features = _cloud_user_feature_summary(
                await client.async_get_cloud_user_features(language=language)
            )
        except DreameLawnMowerConnectionError as err:
            return replace(support, cloud_user_features_error=str(err))
        return replace(support, cloud_user_features=features)

    return await client._async_cloud_read(read)
