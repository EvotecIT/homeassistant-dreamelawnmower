"""Reusable map-source probe helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import DreameLawnMowerDescriptor, DreameLawnMowerMapView

REDACT_PROBE_KEYS = {
    "bindDomain",
    "did",
    "host",
    "localip",
    "mac",
    "masterName",
    "masterUid",
    "masterUid2UUID",
    "password",
    "sn",
    "token",
    "uid",
    "username",
}

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

MAP_HISTORY_PROPERTY_KEYS = (
    "6.1",  # MAP_DATA
    "6.3",  # OBJECT_NAME
    "6.13",  # OLD_MAP_DATA
)

MAP_CANDIDATE_TERMS = (
    "map",
    "m_path",
    "current_map",
    "object_name",
    "obj_name",
    "boundary",
    "polygon",
    "area",
    "zone",
    "point",
    "path",
    "route",
    "coordinate",
)

PROPERTY_VALUE_PREVIEW_LENGTH = 140


def _redact_probe_value(value: Any) -> Any:
    """Return JSON-safe probe data without stable account or device IDs."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "**REDACTED**"
                if str(key) in REDACT_PROBE_KEYS
                else _redact_probe_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_probe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


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


def _compact_cloud_payload(value: Any) -> dict[str, Any]:
    """Return top-level shape and redacted preview for unknown cloud payloads."""
    if value is None:
        return {"present": False}
    redacted = _redact_probe_value(value)
    if isinstance(redacted, Mapping):
        return {
            "present": True,
            "type": "object",
            "keys": [str(key) for key in list(redacted)[:30]],
            "preview": _property_value_preview(redacted),
        }
    if isinstance(redacted, list):
        return {
            "present": True,
            "type": "array",
            "count": len(redacted),
            "preview": _property_value_preview(redacted),
        }
    return {
        "present": True,
        "type": _property_value_type(redacted),
        "preview": _property_value_preview(redacted),
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


def _entry_has_value(entry: Mapping[str, Any]) -> bool:
    """Return whether a probed property entry carries a useful value."""
    for key in ("value", "values", "data", "raw", "content"):
        value = entry.get(key)
        if value not in (None, "", [], {}):
            return True
    return False


def _property_value(entry: Mapping[str, Any]) -> Any:
    for key in ("value", "values", "data", "raw", "content"):
        value = entry.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _history_entry_value(entry: Mapping[str, Any]) -> Any:
    for key in ("value", "val", "values", "data", "raw", "content"):
        value = entry.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _property_value_type(value: Any) -> str:
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def _property_value_preview(value: Any) -> Any:
    if isinstance(value, list):
        preview = [_redact_probe_value(item) for item in value[:10]]
        if len(value) > 10:
            preview.append(f"... +{len(value) - 10} items")
        return preview
    if isinstance(value, Mapping):
        preview: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 10:
                preview["..."] = f"+{len(value) - 10} keys"
                break
            preview[str(key)] = _redact_probe_value(item)
        return preview
    if isinstance(value, str):
        text = value.strip()
        if len(text) > PROPERTY_VALUE_PREVIEW_LENGTH:
            return f"{text[:PROPERTY_VALUE_PREVIEW_LENGTH]}..."
        return text
    return _redact_probe_value(value)


def _map_candidate_reason(key: str, value: Any) -> str | None:
    haystacks = [key.casefold()]
    if isinstance(value, str):
        haystacks.append(value[:2000].casefold())
    elif isinstance(value, Mapping):
        haystacks.extend(str(item).casefold() for item in value.keys())

    for term in MAP_CANDIDATE_TERMS:
        folded = term.casefold()
        if any(folded in haystack for haystack in haystacks):
            return f"contains_{term}"
    if isinstance(value, Mapping):
        return "object_payload"
    return None


def build_cloud_property_summary(
    cloud_properties: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a compact index of useful property probe results."""
    payload = dict(cloud_properties or {})
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        entries = []

    non_empty_keys: list[str] = []
    hinted_keys: dict[str, str] = {}
    decoded_labels: dict[str, str] = {}
    decoded_label_sources: dict[str, str] = {}
    blob_keys: dict[str, int] = {}
    unknown_non_empty_keys: list[str] = []
    value_type_counts: dict[str, int] = {}
    candidate_map_properties: list[dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue

        key = str(entry.get("key") or "")
        if not key:
            continue

        value = _property_value(entry)
        value_type = _property_value_type(value)
        value_type_counts[value_type] = value_type_counts.get(value_type, 0) + 1

        if value is not None:
            non_empty_keys.append(key)

        property_hint = entry.get("property_hint")
        if property_hint:
            hinted_keys[key] = str(property_hint)
        elif value is not None:
            unknown_non_empty_keys.append(key)

        decoded_label = entry.get("decoded_label")
        if decoded_label:
            decoded_labels[key] = str(decoded_label)

        decoded_label_source = entry.get("decoded_label_source")
        if decoded_label_source:
            decoded_label_sources[key] = str(decoded_label_source)

        value_bytes_len = entry.get("value_bytes_len")
        if isinstance(value_bytes_len, int):
            blob_keys[key] = value_bytes_len

        candidate_reason = _map_candidate_reason(key, value)
        if candidate_reason and value is not None:
            candidate_map_properties.append(
                {
                    "key": key,
                    "reason": candidate_reason,
                    "value_type": value_type,
                    "value_preview": _property_value_preview(value),
                }
            )

    return {
        "requested_key_count": payload.get("requested_key_count", 0),
        "returned_entry_count": payload.get("returned_entry_count", 0),
        "displayed_entry_count": payload.get("displayed_entry_count", len(entries)),
        "non_empty_entry_count": len(non_empty_keys),
        "hinted_entry_count": len(hinted_keys),
        "decoded_entry_count": len(decoded_labels),
        "blob_entry_count": len(blob_keys),
        "unknown_non_empty_entry_count": len(unknown_non_empty_keys),
        "candidate_map_entry_count": len(candidate_map_properties),
        "non_empty_keys": non_empty_keys,
        "unknown_non_empty_keys": unknown_non_empty_keys,
        "hinted_keys": hinted_keys,
        "decoded_labels": decoded_labels,
        "decoded_label_sources": decoded_label_sources,
        "blob_keys": blob_keys,
        "value_type_counts": value_type_counts,
        "candidate_map_properties": candidate_map_properties[:20],
    }


def build_cloud_property_history_summary(
    history: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return compact evidence from read-only property-history probes."""
    payload = dict(history or {})
    keys: dict[str, Any] = {}
    populated_keys: list[str] = []

    for key, value in payload.items():
        if isinstance(value, Mapping) and value.get("error"):
            keys[str(key)] = {
                "entry_count": 0,
                "has_value": False,
                "error": value.get("error"),
            }
            continue

        entries = value if isinstance(value, list) else []
        latest = entries[0] if entries and isinstance(entries[0], Mapping) else {}
        latest_value = _history_entry_value(latest)
        has_value = latest_value is not None
        if has_value:
            populated_keys.append(str(key))
        keys[str(key)] = {
            "entry_count": len(entries),
            "has_value": has_value,
            "latest_time": latest.get("time") or latest.get("ts"),
            "latest_value_type": _property_value_type(latest_value),
            "latest_value_preview": _property_value_preview(latest_value),
        }

    return {
        "requested_keys": list(payload),
        "populated_keys": populated_keys,
        "populated_key_count": len(populated_keys),
        "keys": keys,
    }


def build_cloud_key_definition_summary(
    cloud_key_definition: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a compact summary of Dreame's public device key definition."""
    payload = dict(cloud_key_definition or {})
    definition = payload.get("payload")
    key_define = (
        definition.get("keyDefine", {})
        if isinstance(definition, Mapping)
        else {}
    )
    root_keys = (
        sorted(str(key) for key in definition)
        if isinstance(definition, Mapping)
        else []
    )
    key_define_keys = (
        sorted(str(key) for key in key_define)
        if isinstance(key_define, Mapping)
        else []
    )
    return {
        "url_present": bool(payload.get("url_present") or payload.get("url")),
        "ver": payload.get("ver"),
        "source": payload.get("source"),
        "fetched": bool(payload.get("fetched")),
        "error": payload.get("error"),
        "root_keys": root_keys[:20],
        "key_define_count": len(key_define_keys),
        "key_define_keys": key_define_keys[:30],
    }


def build_map_probe_payload(
    *,
    descriptor: DreameLawnMowerDescriptor,
    map_view: DreameLawnMowerMapView,
    cloud_properties: Mapping[str, Any] | None,
    cloud_device_info: Mapping[str, Any] | None,
    cloud_device_list_page: Mapping[str, Any] | None,
    cloud_property_history: Mapping[str, Any] | None = None,
    cloud_user_features: Any = None,
    cloud_device_otc_info: Any = None,
    cloud_key_definition: Mapping[str, Any] | None = None,
    app_maps: Mapping[str, Any] | None = None,
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
        "cloud_property_summary": build_cloud_property_summary(cloud_properties),
        "cloud_property_history": _redact_probe_value(cloud_property_history or {}),
        "cloud_property_history_summary": build_cloud_property_history_summary(
            cloud_property_history
        ),
        "cloud_device_info": _trim_device_record(cloud_device_info),
        "cloud_device_list_record": _trim_device_record(
            _find_device_list_record(descriptor, cloud_device_list_page)
        ),
        "cloud_user_features": _redact_probe_value(cloud_user_features),
        "cloud_device_otc_info": _compact_cloud_payload(cloud_device_otc_info),
        "cloud_key_definition": build_cloud_key_definition_summary(
            cloud_key_definition
        ),
        "app_maps": _redact_probe_value(app_maps or {}),
    }
