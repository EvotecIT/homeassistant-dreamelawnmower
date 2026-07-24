"""Discovery, firmware, and operation diagnostics helpers."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .app_protocol import (
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    MOWER_TASK_PROPERTY_KEY,
    decode_mower_status_blob,
    decode_mower_task_status,
    mower_property_hint,
    mower_realtime_property_name,
)
from .client_constants import (
    REMOTE_CONTROL_MAX_ROTATION,
    REMOTE_CONTROL_MAX_VELOCITY,
)
from .client_shared_helpers import _operation_value_type
from .exceptions import (
    DeviceException,
    DreameLawnMowerAuthError,
    DreameLawnMowerTwoFactorRequiredError,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .models import (
    SUPPORTED_ACCOUNT_TYPES,
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
    descriptor_from_cloud_record,
    remote_control_block_reason,
    remote_control_state_safe,
)
from .payload_utils import (
    _as_optional_text,
    _json_safe,
)

_FIRMWARE_DESCRIPTION_PREFERRED_KEYS = (
    "en",
    "en_us",
    "en-us",
    "default",
    "content",
    "contents",
    "text",
    "detail",
    "details",
    "description",
    "desc",
    "release_note",
    "release_notes",
    "releasenote",
    "releasenotes",
    "changelog",
    "change_log",
    "update_content",
    "updatecontent",
    "upgrade_content",
    "upgradecontent",
    "data",
)
_FIRMWARE_DESCRIPTION_METADATA_KEYS = {
    "code",
    "success",
    "status",
    "curversion",
    "newversion",
    "firmwaretype",
    "hasnewfirmware",
    "force",
    "url",
    "md5",
    "size",
}


def _sync_discover_devices(
    username: str,
    password: str,
    country: str,
    account_type: str,
) -> list[DreameLawnMowerDescriptor]:
    if account_type not in SUPPORTED_ACCOUNT_TYPES:
        raise DreameLawnMowerAuthError(f"Unsupported account type: {account_type}")

    from .protocol import DreameMowerProtocol

    protocol = DreameMowerProtocol(
        username=username,
        password=password,
        country=country,
        prefer_cloud=True,
        account_type=account_type,
    )

    try:
        protocol.cloud.login()
    except DeviceException as err:
        raise DreameLawnMowerAuthError(str(err)) from err

    if protocol.cloud.two_factor_url:
        raise DreameLawnMowerTwoFactorRequiredError(protocol.cloud.two_factor_url)

    if not protocol.cloud.logged_in:
        raise DreameLawnMowerAuthError("Unable to log into the Dreame or MOVA cloud.")

    records = protocol.cloud.get_devices()
    if not records:
        return []

    if isinstance(records, dict):
        items = records.get("page", {}).get("records", records)
    else:
        items = records

    found: list[DreameLawnMowerDescriptor] = []
    for record in items:
        descriptor = descriptor_from_cloud_record(
            record,
            account_type=account_type,
            country=country,
        )
        if descriptor is not None:
            found.append(descriptor)

    found.sort(key=lambda item: item.title.lower())
    return found


def _parse_firmware_description(
    value: Any,
) -> tuple[str | None, bool, Mapping[str, Any] | None]:
    text = _as_optional_text(value)
    if text is None:
        return None, False, None

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text, True, None

    if isinstance(parsed, Mapping):
        code = parsed.get("code")
        success = parsed.get("success")
        msg = _as_optional_text(parsed.get("msg"))
        if (isinstance(success, bool) and not success) or (
            isinstance(code, int) and code != 0
        ):
            return (
                None,
                False,
                {
                    "code": code,
                    "success": success,
                    "msg": msg,
                },
            )

    parsed_text = _firmware_description_text(parsed)
    if parsed_text is not None:
        return parsed_text, True, None

    return text, True, None


def _firmware_description_text(value: Any) -> str | None:
    parts = _firmware_description_parts(value)
    if not parts:
        return None

    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = _normalize_firmware_description_text(part)
        if normalized is None:
            continue
        dedupe_key = normalized.casefold()
        if dedupe_key in seen:
            continue
        cleaned.append(normalized)
        seen.add(dedupe_key)

    return "\n".join(cleaned) if cleaned else None


def _firmware_description_parts(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _as_optional_text(value)
        if text is None:
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return [text]
        nested = _firmware_description_parts(parsed)
        return nested or [text]

    if isinstance(value, Mapping):
        preferred_parts: list[str] = []
        fallback_parts: list[str] = []
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _FIRMWARE_DESCRIPTION_METADATA_KEYS:
                continue
            item_parts = _firmware_description_parts(item)
            if not item_parts:
                continue
            if (
                normalized_key in _FIRMWARE_DESCRIPTION_PREFERRED_KEYS
                or normalized_key.isnumeric()
            ):
                preferred_parts.extend(item_parts)
            else:
                fallback_parts.extend(item_parts)
        return preferred_parts or fallback_parts

    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        parts: list[str] = []
        for item in value:
            parts.extend(_firmware_description_parts(item))
        return parts

    return []


def _normalize_firmware_description_text(value: str) -> str | None:
    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _normalize_cloud_firmware_check(
    value: Any,
    *,
    current_version: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "cloud_check_device_version",
        "available": False,
        "update_available": None,
        "current_version": current_version,
        "latest_version": None,
        "firmware_type": None,
        "force_update": None,
        "status": None,
        "changelog": None,
        "changelog_available": False,
    }
    if not isinstance(value, Mapping):
        result["errors"] = [{"stage": "response", "error": "invalid_response"}]
        return result

    code = value.get("code")
    success = value.get("success")
    if (isinstance(code, int) and code != 0) or (
        isinstance(success, bool) and not success
    ):
        result["errors"] = [
            {
                "stage": "response",
                "error": "cloud_error",
                "code": code,
                "success": success if isinstance(success, bool) else None,
                "msg": _as_optional_text(value.get("msg")),
            }
        ]
        return result

    result["available"] = True
    current_version_value = _as_optional_text(value.get("curVersion"))
    if current_version_value is not None:
        result["current_version"] = current_version_value

    latest_version = _as_optional_text(value.get("newVersion"))
    if latest_version is not None:
        result["latest_version"] = latest_version

    firmware_type = _as_optional_text(value.get("firmwareType"))
    if firmware_type is not None:
        result["firmware_type"] = firmware_type

    force_update = value.get("force")
    if isinstance(force_update, bool):
        result["force_update"] = force_update

    result["status"] = value.get("status")

    has_new_firmware = value.get("hasNewFirmware")
    if isinstance(has_new_firmware, bool):
        result["update_available"] = has_new_firmware
    elif (
        latest_version is not None
        and result["current_version"] is not None
        and latest_version != result["current_version"]
    ):
        result["update_available"] = True

    changelog, changelog_available, changelog_error = _parse_firmware_description(
        value.get("description")
    )
    result["changelog"] = changelog
    result["changelog_available"] = changelog_available
    if changelog_error is not None:
        result["changelog_error"] = dict(changelog_error)

    return result


def _merge_error_text(
    existing: str | None,
    stage: str,
    error: str,
) -> str:
    entry = f"{stage}: {error}"
    if existing:
        return f"{existing}; {entry}"
    return entry


def _operation_snapshot_summary(snapshot: DreameLawnMowerSnapshot) -> dict[str, Any]:
    """Return a compact, stable snapshot for field-test logs."""
    raw_attributes = snapshot.raw_attributes or {}
    return {
        "device": snapshot.descriptor.title,
        "descriptor": {
            "did": snapshot.descriptor.did,
            "name": snapshot.descriptor.name,
            "model": snapshot.descriptor.model,
            "display_model": snapshot.descriptor.display_model,
            "account_type": snapshot.descriptor.account_type,
            "country": snapshot.descriptor.country,
            "host_present": bool(snapshot.descriptor.host),
            "token_present": bool(snapshot.descriptor.token),
        },
        "available": snapshot.available,
        "online": snapshot.online,
        "state": snapshot.state,
        "mower_state": snapshot.mower_state,
        "state_name": snapshot.state_name,
        "mower_state_name": snapshot.mower_state_name,
        "activity": snapshot.activity,
        "task_status": snapshot.task_status,
        "mowing_task_status": snapshot.mowing_task_status,
        "task_status_name": snapshot.task_status_name,
        "mowing_task_status_name": snapshot.mowing_task_status_name,
        "task_status_source": snapshot.task_status_source,
        "mowing_session_active": snapshot.mowing_session_active,
        "task_resumable": snapshot.task_resumable,
        "battery_level": snapshot.battery_level,
        "charging": snapshot.charging,
        "raw_charging": snapshot.raw_charging,
        "docked": snapshot.docked,
        "raw_docked": snapshot.raw_docked,
        "started": snapshot.started,
        "raw_started": snapshot.raw_started,
        "mowing": snapshot.mowing,
        "paused": snapshot.paused,
        "returning": snapshot.returning,
        "raw_returning": snapshot.raw_returning,
        "scheduled_mow": snapshot.scheduled_mow,
        "scheduled_clean": snapshot.scheduled_clean,
        "shortcut_task": snapshot.shortcut_task,
        "mapping_available": snapshot.mapping_available,
        "error_code": snapshot.error_code,
        "error_name": snapshot.error_name,
        "error_text": snapshot.error_text,
        "error_display": snapshot.error_display,
        "error_source": snapshot.error_source,
        "status_notice_code": snapshot.status_notice_code,
        "status_notice_name": snapshot.status_notice_name,
        "status_notice_display": snapshot.status_notice_display,
        "status_notice_tier": snapshot.status_notice_tier,
        "status_notice_source": snapshot.status_notice_source,
        "raw_error_code": snapshot.raw_error_code,
        "realtime_error_code": snapshot.realtime_error_code,
        "child_lock": snapshot.child_lock,
        "mowing_mode": snapshot.mowing_mode,
        "mowing_mode_name": snapshot.mowing_mode_name,
        "mowed_area": snapshot.mowed_area,
        "mowing_time": snapshot.mowing_time,
        "cleaning_mode": snapshot.cleaning_mode,
        "cleaning_mode_name": snapshot.cleaning_mode_name,
        "capabilities": list(snapshot.capabilities),
        "firmware_version": snapshot.firmware_version,
        "hardware_version": snapshot.hardware_version,
        "serial_number": snapshot.serial_number,
        "cloud_update_time": snapshot.cloud_update_time,
        "unknown_property_count": snapshot.unknown_property_count,
        "realtime_property_count": snapshot.realtime_property_count,
        "last_realtime_method": snapshot.last_realtime_method,
        "manual_drive_safe": remote_control_state_safe(snapshot),
        "manual_drive_block_reason": remote_control_block_reason(snapshot),
        "raw_state_signals": _json_safe(
            {
                key: raw_attributes.get(key)
                for key in (
                    "mower_state",
                    "status",
                    "error",
                    "charging",
                    "docked",
                    "started",
                    "running",
                    "paused",
                    "returning",
                    "mapping",
                    "fast_mapping",
                    "has_saved_map",
                    "has_temporary_map",
                )
                if key in raw_attributes
            }
        ),
    }


def _operation_property_summary(
    properties: Mapping[Any, Any],
    *,
    unknown_prefix: str | None = None,
) -> dict[str, Any]:
    """Return compact live property evidence for operation snapshots."""
    entries: list[dict[str, Any]] = []
    known_keys: list[str] = []
    unknown_keys: list[str] = []
    value_type_counts: dict[str, int] = {}

    for key, value in properties.items():
        key_text = str(key)
        payload = value if isinstance(value, Mapping) else {}
        property_hint = mower_property_hint(key_text)
        property_name = mower_realtime_property_name(
            key_text, payload.get("property_name")
        )
        property_value = payload.get("value") if isinstance(value, Mapping) else value
        value_type = _operation_value_type(property_value)
        value_type_counts[value_type] = value_type_counts.get(value_type, 0) + 1

        if unknown_prefix is not None:
            if property_name.startswith(unknown_prefix):
                unknown_keys.append(key_text)
            else:
                known_keys.append(key_text)

        status_blob = None
        status_blob_keys = {
            MOWER_RAW_STATUS_PROPERTY_KEY,
            MOWER_RUNTIME_STATUS_PROPERTY_KEY,
        }
        if key_text in status_blob_keys:
            decoded = decode_mower_status_blob(property_value, source="operation")
            status_blob = decoded.as_dict() if decoded is not None else None
        task_status = None
        if key_text == MOWER_TASK_PROPERTY_KEY:
            task_status = decode_mower_task_status(property_value)

        entry = {
            "key": key_text,
            "property_name": property_name or None,
            "siid": _json_safe(payload.get("siid")),
            "piid": _json_safe(payload.get("piid")),
            "code": _json_safe(payload.get("code")),
            "value_type": value_type,
            "value_preview": _operation_short_preview(property_value),
            "status_blob": status_blob,
            "task_status": task_status,
        }
        if property_hint is not None:
            entry["property_hint"] = property_hint
        entries.append(entry)

    entries.sort(key=lambda item: item["key"])
    known_keys.sort()
    unknown_keys.sort()
    summary: dict[str, Any] = {
        "count": len(entries),
        "value_type_counts": value_type_counts,
        "entries": entries[:30],
    }
    if unknown_prefix is not None:
        summary["known_keys"] = known_keys
        summary["unknown_keys"] = unknown_keys
    return summary


def _operation_short_preview(value: Any) -> Any:
    normalized = _json_safe(value, max_depth=3)
    if isinstance(normalized, str):
        return normalized if len(normalized) <= 120 else f"{normalized[:117]}..."
    if isinstance(normalized, list):
        preview = normalized[:10]
        if len(normalized) > 10:
            preview.append(f"... +{len(normalized) - 10} items")
        return preview
    if isinstance(normalized, Mapping):
        return {key: normalized[key] for key in list(normalized.keys())[:10]}
    return normalized


def _validate_remote_control_step(*, rotation: int, velocity: int) -> None:
    if not isinstance(rotation, int) or isinstance(rotation, bool):
        raise ValueError("rotation must be an integer")
    if not isinstance(velocity, int) or isinstance(velocity, bool):
        raise ValueError("velocity must be an integer")
    if abs(rotation) > REMOTE_CONTROL_MAX_ROTATION:
        raise ValueError(
            f"rotation must be between {-REMOTE_CONTROL_MAX_ROTATION} and "
            f"{REMOTE_CONTROL_MAX_ROTATION}"
        )
    if abs(velocity) > REMOTE_CONTROL_MAX_VELOCITY:
        raise ValueError(
            f"velocity must be between {-REMOTE_CONTROL_MAX_VELOCITY} and "
            f"{REMOTE_CONTROL_MAX_VELOCITY}"
        )
