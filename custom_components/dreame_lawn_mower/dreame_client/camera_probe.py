"""Reusable camera/photo probe helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .map_probe import build_cloud_property_summary
from .models import (
    DreameLawnMowerCameraFeatureSupport,
    DreameLawnMowerDescriptor,
)

CAMERA_PROBE_PROPERTY_KEYS = (
    "10001.1",
    "10001.2",
    "10001.4",
    "10001.5",
    "10001.6",
    "10001.7",
    "10001.99",
    "10001.103",
    "10001.1003",
    "10001.1100",
)


def build_camera_probe_payload(
    *,
    descriptor: DreameLawnMowerDescriptor,
    support: DreameLawnMowerCameraFeatureSupport,
    cloud_properties: Mapping[str, Any] | None,
    device_properties: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a JSON-safe camera/photo probe payload."""
    return {
        "descriptor": {
            "name": descriptor.name,
            "model": descriptor.model,
            "display_model": descriptor.display_model,
            "account_type": descriptor.account_type,
            "country": descriptor.country,
        },
        "support": support.as_dict(),
        "cloud_properties": dict(cloud_properties or {}),
        "cloud_property_summary": build_cloud_property_summary(cloud_properties),
        "device_properties": dict(device_properties or {}),
    }
