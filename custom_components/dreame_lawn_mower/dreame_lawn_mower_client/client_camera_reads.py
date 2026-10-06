"""Native read-only camera discovery using the common capability policy."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

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
