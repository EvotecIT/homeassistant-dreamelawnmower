"""Reusable map-source probe helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import DreameLawnMowerDescriptor, DreameLawnMowerMapView

MAP_PROBE_PROPERTY_KEYS = (
    "1.1",
    "2.1",
    "2.2",
    "6.1",
    "6.2",
    "6.3",
    "6.4",
    "6.5",
    "6.6",
    "6.7",
    "6.8",
    "6.9",
    "6.10",
    "6.11",
    "6.12",
    "6.13",
    "6.14",
    "6.15",
    "6.16",
    "6.17",
    "6.18",
    "6.19",
    "6.20",
)


def _trim_device_record(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return map-relevant cloud device metadata without heavy raw payloads."""
    if not record:
        return {}

    device_info = record.get("deviceInfo", {}) or {}
    key_define = record.get("keyDefine", {}) or {}
    return {
        "model": record.get("model"),
        "sub_model": record.get("subModel") or record.get("submodel"),
        "custom_name": record.get("customName") or record.get("name"),
        "latest_status": record.get("latestStatus"),
        "battery": record.get("battery"),
        "online": record.get("online"),
        "status": device_info.get("status"),
        "display_name": device_info.get("displayName"),
        "product_id": device_info.get("productId") or record.get("productId"),
        "feature": device_info.get("feature"),
        "permit": device_info.get("permit"),
        "extension_id": device_info.get("extensionId"),
        "key_define_ver": key_define.get("ver"),
        "key_define_url_present": bool(key_define.get("url")),
    }


def _find_device_list_record(
    descriptor: DreameLawnMowerDescriptor,
    device_list_page: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Return the matching device record from a cloud list page."""
    if not device_list_page:
        return None

    result = device_list_page.get("result", device_list_page)
    page = result.get("page", result) if isinstance(result, Mapping) else {}
    records = page.get("records", []) if isinstance(page, Mapping) else []
    for record in records:
        if isinstance(record, Mapping) and record.get("did") == descriptor.did:
            return record
    return None


def build_map_probe_payload(
    *,
    descriptor: DreameLawnMowerDescriptor,
    map_view: DreameLawnMowerMapView,
    cloud_properties: Mapping[str, Any] | None,
    cloud_device_info: Mapping[str, Any] | None,
    cloud_device_list_page: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a JSON-safe map-source probe payload."""
    return {
        "descriptor": {
            "name": descriptor.name,
            "model": descriptor.model,
            "display_model": descriptor.display_model,
            "account_type": descriptor.account_type,
            "country": descriptor.country,
        },
        "legacy_current_map": map_view.as_dict(),
        "cloud_properties": dict(cloud_properties or {}),
        "cloud_device_info": _trim_device_record(cloud_device_info),
        "cloud_device_list_record": _trim_device_record(
            _find_device_list_record(descriptor, cloud_device_list_page)
        ),
    }
