"""Extract JSON payloads from Home Assistant Dreame lawn mower log lines."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG_MARKERS = {
    "batch_device_data_probe": "Captured Dreame lawn mower batch device data probe",
    "debug_snapshot": "Captured Dreame lawn mower debug snapshot",
    "map_probe": "Captured Dreame lawn mower map probe",
    "operation_snapshot": "Captured Dreame lawn mower operation snapshot",
    "preference_probe": "Captured Dreame lawn mower preference probe",
    "schedule_probe": "Captured Dreame lawn mower schedule probe",
    "task_status_probe": "Captured Dreame lawn mower task status probe",
    "weather_probe": "Captured Dreame lawn mower weather probe",
}


@dataclass(frozen=True)
class ExtractedPayload:
    """One JSON payload extracted from a Home Assistant log export."""

    kind: str | None
    payload: dict[str, Any]


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact triage summary for a mower debug or probe payload."""

    payload = _payload_body(payload)
    if isinstance(payload.get("captures"), list):
        return _summarize_field_trip_payload(payload)
    if (
        payload.get("source") == "batch_device_data_probe"
        or any(
            key in payload
            for key in (
                "batch_schedule",
                "batch_mowing_preferences",
                "batch_ota_info",
            )
        )
    ):
        return _summarize_batch_device_data_payload(payload)
    if (
        payload.get("source") == "app_action_mowing_preferences"
        or payload.get("property_hint") == "2.52"
    ):
        return _summarize_preference_payload(payload)
    if payload.get("source") == "app_action_weather_protection":
        return _summarize_weather_payload(payload)
    if payload.get("source") == "cloud_property_task_status":
        return _summarize_task_status_payload(payload)
    if (
        isinstance(payload.get("schedules"), list)
        or isinstance(payload.get("schedule_selection"), dict)
        or isinstance(payload.get("current_task"), dict)
    ):
        return _summarize_schedule_payload(payload)

    snapshot = _as_mapping(payload.get("snapshot"))
    descriptor = _as_mapping(payload.get("descriptor") or snapshot.get("descriptor"))
    device = _as_mapping(payload.get("device"))
    triage = _as_mapping(payload.get("triage"))
    reconciliation = _as_mapping(payload.get("state_reconciliation"))
    error = _as_mapping(reconciliation.get("error"))
    flags = _as_mapping(reconciliation.get("flags"))
    manual_drive = _as_mapping(reconciliation.get("manual_drive"))
    raw_attributes = _as_mapping(snapshot.get("raw_attributes"))
    raw_state_signals = _as_mapping(snapshot.get("raw_state_signals"))
    unknown_property_summary = _as_mapping(payload.get("unknown_property_summary"))
    realtime_summary = _as_mapping(payload.get("realtime_summary"))
    cloud_property_summary = _as_mapping(payload.get("cloud_property_summary"))
    map_payload = _as_mapping(payload.get("map"))
    map_view = _as_mapping(payload.get("map_view"))
    app_maps = _as_mapping(payload.get("app_maps"))
    firmware_update = _as_mapping(payload.get("firmware_update"))
    remote_control_support = _as_mapping(payload.get("remote_control_support"))
    status_blob = _as_mapping(payload.get("status_blob"))

    summary: dict[str, Any] = {
        "label": payload.get("label"),
        "diagnostic_schema_version": payload.get("diagnostic_schema_version")
        or triage.get("schema_version"),
        "captured_at": payload.get("captured_at"),
        "name": descriptor.get("name") or device.get("name"),
        "model": descriptor.get("display_model") or descriptor.get("model"),
        "activity": reconciliation.get("activity") or snapshot.get("activity"),
        "state": reconciliation.get("state") or snapshot.get("state"),
        "state_name": reconciliation.get("state_name") or snapshot.get("state_name"),
        "raw_mower_state": reconciliation.get("raw_mower_state")
        or raw_attributes.get("mower_state")
        or raw_state_signals.get("mower_state"),
        "battery_level": snapshot.get("battery_level"),
        "error": {
            "active": error.get("active"),
            "code": error.get("code", snapshot.get("error_code")),
            "name": error.get("name", snapshot.get("error_name")),
            "display": error.get("display", snapshot.get("error_display")),
            "raw_attribute": error.get("raw_attribute"),
        },
        "flags": {
            "charging": flags.get("charging", snapshot.get("charging")),
            "raw_charging": flags.get("raw_charging", snapshot.get("raw_charging")),
            "docked": flags.get("docked", snapshot.get("docked")),
            "raw_docked": flags.get("raw_docked", snapshot.get("raw_docked")),
            "mowing": flags.get("mowing", snapshot.get("mowing")),
            "paused": flags.get("paused", snapshot.get("paused")),
            "returning": flags.get("returning", snapshot.get("returning")),
            "raw_returning": flags.get("raw_returning", snapshot.get("raw_returning")),
            "started": flags.get("started", snapshot.get("started")),
            "raw_started": flags.get("raw_started", snapshot.get("raw_started")),
        },
        "manual_drive": {
            "safe": manual_drive.get("safe", snapshot.get("manual_drive_safe")),
            "block_reason": manual_drive.get(
                "block_reason",
                snapshot.get("manual_drive_block_reason"),
            ),
        },
        "warnings": reconciliation.get("warnings", []),
        "triage_issues": triage.get("issues", []),
        "suggested_next_capture": triage.get("suggested_next_capture", []),
        "errors": payload.get("errors", []),
        "unknown_property_count": device.get(
            "unknown_property_count",
            snapshot.get(
                "unknown_property_count",
                unknown_property_summary.get("count"),
            ),
        ),
        "realtime_property_count": device.get(
            "realtime_property_count",
            snapshot.get("realtime_property_count", realtime_summary.get("count")),
        ),
        "last_realtime_method": snapshot.get("last_realtime_method"),
    }

    if cloud_property_summary:
        summary["cloud_property_summary"] = {
            "requested_key_count": cloud_property_summary.get("requested_key_count"),
            "non_empty_keys": cloud_property_summary.get("non_empty_keys", []),
            "decoded_labels": cloud_property_summary.get("decoded_labels", {}),
            "state_keys": cloud_property_summary.get("state_keys", {}),
            "blob_keys": cloud_property_summary.get("blob_keys", {}),
        }

    if map_payload:
        summary["map"] = {
            "source": map_payload.get("source"),
            "available": map_payload.get("available"),
            "has_image": map_payload.get("has_image"),
            "error": map_payload.get("error"),
        }

    if map_view:
        summary["map_view"] = _map_view_summary(map_view)

    if app_maps:
        summary["app_maps"] = _app_maps_summary(app_maps)

    if firmware_update:
        firmware_summary = {
            "current_version": firmware_update.get("current_version"),
            "update_state": firmware_update.get("update_state"),
            "update_available": firmware_update.get("update_available"),
            "warnings": firmware_update.get("warnings", []),
            "reason": firmware_update.get("reason"),
        }
        latest_version = firmware_update.get("latest_version")
        if latest_version is not None:
            firmware_summary["latest_version"] = latest_version
        for key in (
            "cloud_check_available",
            "cloud_check_update_available",
            "release_summary_available",
            "debug_catalog_available",
            "debug_catalog_current_version_present",
            "debug_catalog_changelog_available",
        ):
            value = firmware_update.get(key)
            if value is not None:
                firmware_summary[key] = value

        debug_latest_versions = [
            item.get("latest_release_version")
            for item in (
                firmware_update.get("debug_catalog_latest_release_candidates") or []
            )
            if isinstance(item, dict) and item.get("latest_release_version")
        ]
        if debug_latest_versions:
            firmware_summary["debug_catalog_latest_release_versions"] = (
                debug_latest_versions
            )

        summary["firmware_update"] = firmware_summary

    if remote_control_support:
        summary["remote_control_support"] = {
            "supported": remote_control_support.get("supported"),
            "active": remote_control_support.get("active"),
            "state_safe": remote_control_support.get("state_safe"),
            "state_block_reason": remote_control_support.get("state_block_reason"),
            "siid": remote_control_support.get("siid"),
            "piid": remote_control_support.get("piid"),
            "reason": remote_control_support.get("reason"),
        }

    if status_blob:
        summary["status_blob"] = {
            "source": status_blob.get("source"),
            "length": status_blob.get("length"),
            "frame_valid": status_blob.get("frame_valid"),
            "notes": status_blob.get("notes", []),
        }

    return _drop_empty(summary)


def extract_payloads(text: str, *, kind: str | None = None) -> list[ExtractedPayload]:
    """Extract all mower JSON payloads from text.

    Home Assistant logs prefix the JSON with human-readable text, while downloaded
    diagnostics are already plain JSON. This helper accepts both forms so captures
    can be turned into test fixtures without manual trimming.
    """

    if kind is not None and kind not in LOG_MARKERS:
        raise ValueError(f"Unsupported payload kind: {kind}")

    stripped = text.strip()
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object payload")
        return [ExtractedPayload(kind=kind, payload=payload)]

    decoder = json.JSONDecoder()
    payloads: list[ExtractedPayload] = []
    seen_offsets: set[int] = set()

    for detected_kind, offset in _candidate_json_offsets(text, kind=kind):
        if offset in seen_offsets:
            continue
        seen_offsets.add(offset)

        try:
            payload, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(ExtractedPayload(kind=detected_kind, payload=payload))

    return payloads


def extract_first_payload(text: str, *, kind: str | None = None) -> dict[str, Any]:
    """Extract the first mower payload from text."""

    payloads = extract_payloads(text, kind=kind)
    if not payloads:
        raise ValueError("No Dreame lawn mower JSON payload found")
    return payloads[0].payload


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_empty(item)) not in (None, {}, [])
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value


def _map_view_summary(map_view: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _as_mapping(map_view.get("diagnostics"))
    cloud_property_summary = _as_mapping(diagnostics.get("cloud_property_summary"))
    return _drop_empty(
        {
            "source": map_view.get("source"),
            "available": map_view.get("available"),
            "has_image": map_view.get("has_image"),
            "error": map_view.get("error"),
            "diagnostic_reason": diagnostics.get("reason"),
            "cloud_property_summary": {
                "requested_key_count": cloud_property_summary.get(
                    "requested_key_count"
                ),
                "non_empty_keys": cloud_property_summary.get("non_empty_keys", []),
                "decoded_labels": cloud_property_summary.get("decoded_labels", {}),
                "state_keys": cloud_property_summary.get("state_keys", {}),
                "blob_keys": cloud_property_summary.get("blob_keys", {}),
            },
        }
    )


def _app_maps_summary(app_maps: dict[str, Any]) -> dict[str, Any]:
    maps = app_maps.get("maps") if isinstance(app_maps.get("maps"), list) else []
    return _drop_empty(
        {
            "source": app_maps.get("source"),
            "available": app_maps.get("available"),
            "map_count": app_maps.get("map_count"),
            "current_map_index": app_maps.get("current_map_index"),
            "errors": app_maps.get("errors", []),
            "maps": [
                _app_map_entry_summary(item)
                for item in maps
                if isinstance(item, dict)
            ],
        }
    )


def _app_map_entry_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = _as_mapping(item.get("summary"))
    return _drop_empty(
        {
            "idx": item.get("idx"),
            "current": item.get("current"),
            "created": item.get("created"),
            "available": item.get("available"),
            "hash_match": item.get("hash_match"),
            "summary": {
                "map_area_count": summary.get("map_area_count"),
                "boundary_point_count": summary.get("boundary_point_count"),
                "spot_count": summary.get("spot_count"),
                "spot_boundary_point_count": summary.get(
                    "spot_boundary_point_count"
                ),
                "semantic_count": summary.get("semantic_count"),
                "semantic_boundary_point_count": summary.get(
                    "semantic_boundary_point_count"
                ),
                "semantic_key_counts": summary.get("semantic_key_counts"),
                "trajectory_count": summary.get("trajectory_count"),
                "trajectory_point_count": summary.get("trajectory_point_count"),
                "cut_relation_count": summary.get("cut_relation_count"),
            },
            "error": item.get("error"),
        }
    )


def _summarize_field_trip_payload(payload: dict[str, Any]) -> dict[str, Any]:
    captures = [
        summarize_payload(capture)
        for capture in payload.get("captures", [])
        if isinstance(capture, dict)
    ]
    steps = [
        {
            "label": step.get("label"),
            "ok": step.get("ok"),
            "error": step.get("error"),
        }
        for step in payload.get("steps", [])
        if isinstance(step, dict)
    ]
    settings = _as_mapping(payload.get("settings"))
    return _drop_empty(
        {
            "execute": payload.get("execute"),
            "device_index": payload.get("device_index"),
            "capture_count": len(captures),
            "step_count": len(steps),
            "settings": {
                "dock": settings.get("dock"),
                "include_map": settings.get("include_map"),
                "include_firmware": settings.get("include_firmware"),
                "velocity": settings.get("velocity"),
                "rotation": settings.get("rotation"),
                "duration": settings.get("duration"),
                "settle": settings.get("settle"),
            },
            "steps": steps,
            "captures": captures,
        }
    )


def _summarize_schedule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    schedules = [
        schedule
        for schedule in payload.get("schedules", [])
        if isinstance(schedule, dict)
    ]
    current_task = _as_mapping(payload.get("current_task"))
    selection = _as_mapping(payload.get("schedule_selection"))
    errors = payload.get("errors", [])

    return _drop_empty(
        {
            "current_task": {
                "start_time": current_task.get("start_time"),
                "end_time": current_task.get("end_time"),
                "plan_id": current_task.get("plan_id"),
                "version": current_task.get("version"),
            },
            "schedule_count": len(schedules),
            "schedule_selection": {
                "mode": selection.get("mode"),
                "active_version": selection.get("active_version"),
                "active_version_filter_applied": selection.get(
                    "active_version_filter_applied"
                ),
                "included_schedule_count": selection.get("included_schedule_count"),
                "hidden_schedule_count": selection.get("hidden_schedule_count"),
                "included_schedules": selection.get("included_schedules", []),
                "hidden_schedules": selection.get("hidden_schedules", []),
            },
            "schedules": [
                _schedule_entry_summary(schedule) for schedule in schedules
            ],
            "error_count": len(errors) if isinstance(errors, list) else None,
            "errors": errors,
        }
    )


def _schedule_entry_summary(schedule: dict[str, Any]) -> dict[str, Any]:
    plans = [
        plan
        for plan in schedule.get("plans", [])
        if isinstance(plan, dict)
    ]
    return _drop_empty(
        {
            "idx": schedule.get("idx"),
            "label": schedule.get("label"),
            "version": schedule.get("version"),
            "enabled_plan_count": schedule.get("enabled_plan_count"),
            "plan_count": len(plans),
            "error": schedule.get("error"),
        }
    )


def _summarize_preference_payload(payload: dict[str, Any]) -> dict[str, Any]:
    maps = [
        map_entry
        for map_entry in payload.get("maps", [])
        if isinstance(map_entry, dict)
    ]
    errors = payload.get("errors", [])

    return _drop_empty(
        {
            "source": payload.get("source"),
            "available": payload.get("available"),
            "property_hint": payload.get("property_hint"),
            "map_count": len(maps),
            "maps": [_preference_map_summary(map_entry) for map_entry in maps],
            "error_count": len(errors) if isinstance(errors, list) else None,
            "errors": errors,
        }
    )


def _preference_map_summary(map_entry: dict[str, Any]) -> dict[str, Any]:
    preferences = [
        preference
        for preference in map_entry.get("preferences", [])
        if isinstance(preference, dict)
    ]
    return _drop_empty(
        {
            "idx": map_entry.get("idx"),
            "label": map_entry.get("label"),
            "available": map_entry.get("available"),
            "mode_name": map_entry.get("mode_name"),
            "area_count": map_entry.get("area_count"),
            "preference_count": len(preferences),
            "preferences": [
                _preference_entry_summary(preference)
                for preference in preferences
            ],
            "error": map_entry.get("error"),
        }
    )


def _preference_entry_summary(preference: dict[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "area_id": preference.get("area_id"),
            "reported_version": preference.get("reported_version"),
            "efficient_mode_name": preference.get("efficient_mode_name"),
            "mowing_height_cm": preference.get("mowing_height_cm"),
            "mowing_direction_mode_name": preference.get(
                "mowing_direction_mode_name"
            ),
            "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
            "edge_mowing_auto": preference.get("edge_mowing_auto"),
            "edge_mowing_safe": preference.get("edge_mowing_safe"),
            "obstacle_avoidance_enabled": preference.get(
                "obstacle_avoidance_enabled"
            ),
            "obstacle_avoidance_height_cm": preference.get(
                "obstacle_avoidance_height_cm"
            ),
            "obstacle_avoidance_distance_cm": preference.get(
                "obstacle_avoidance_distance_cm"
            ),
            "obstacle_avoidance_ai_classes": preference.get(
                "obstacle_avoidance_ai_classes",
            ),
        }
    )


def _summarize_batch_device_data_payload(payload: dict[str, Any]) -> dict[str, Any]:
    schedule = _as_mapping(payload.get("batch_schedule"))
    preferences = _as_mapping(payload.get("batch_mowing_preferences"))
    ota = _as_mapping(payload.get("batch_ota_info"))
    schedule_errors = schedule.get("errors")
    preference_errors = preferences.get("errors")
    ota_errors = ota.get("errors")
    return _drop_empty(
        {
            "source": payload.get("source"),
            "captured_at": payload.get("captured_at"),
            "batch_schedule": {
                "available": schedule.get("available"),
                "schedule_count": len(schedule.get("schedules", []))
                if isinstance(schedule.get("schedules"), list)
                else None,
                "schedules": [
                    _schedule_entry_summary(item)
                    for item in schedule.get("schedules", [])
                    if isinstance(item, dict)
                ],
                "error_count": (
                    len(schedule_errors) if isinstance(schedule_errors, list) else None
                ),
                "errors": schedule_errors if schedule_errors else None,
            },
            "batch_mowing_preferences": {
                "available": preferences.get("available"),
                "property_hint": preferences.get("property_hint"),
                "map_count": len(preferences.get("maps", []))
                if isinstance(preferences.get("maps"), list)
                else None,
                "maps": [
                    _preference_map_summary(item)
                    for item in preferences.get("maps", [])
                    if isinstance(item, dict)
                ],
                "error_count": (
                    len(preference_errors)
                    if isinstance(preference_errors, list)
                    else None
                ),
                "errors": preference_errors if preference_errors else None,
            },
            "batch_ota_info": {
                "available": ota.get("available"),
                "update_available": ota.get("update_available"),
                "auto_upgrade_enabled": ota.get("auto_upgrade_enabled"),
                "ota_info": ota.get("ota_info"),
                "ota_status": ota.get("ota_status"),
                "error_count": (
                    len(ota_errors) if isinstance(ota_errors, list) else None
                ),
                "errors": ota_errors if ota_errors else None,
            },
        }
    )


def _summarize_weather_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors = payload.get("errors")
    warnings = payload.get("warnings")
    return _drop_empty(
        {
            "source": payload.get("source"),
            "available": payload.get("available"),
            "fault_hint": payload.get("fault_hint"),
            "present_config_keys": payload.get("present_config_keys"),
            "weather_switch_enabled": payload.get("weather_switch_enabled"),
            "rain_protection_enabled": payload.get("rain_protection_enabled"),
            "rain_protection_active": payload.get("rain_protection_active"),
            "rain_protection_duration_hours": payload.get(
                "rain_protection_duration_hours"
            ),
            "rain_sensor_sensitivity": payload.get("rain_sensor_sensitivity"),
            "rain_protect_end_time": payload.get("rain_protect_end_time"),
            "rain_protect_end_time_iso": payload.get("rain_protect_end_time_iso"),
            "rain_protect_end_time_present": payload.get(
                "rain_protect_end_time_present"
            ),
            "rain_protection_raw": payload.get("rain_protection_raw"),
            "error_count": len(errors) if isinstance(errors, list) else None,
            "warning_count": len(warnings) if isinstance(warnings, list) else None,
            "errors": errors if errors else None,
            "warnings": warnings if warnings else None,
        }
    )


def _summarize_task_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = _as_mapping(payload.get("summary"))
    errors = payload.get("errors")
    return _drop_empty(
        {
            "source": payload.get("source"),
            "available": payload.get("available"),
            "captured_at": payload.get("captured_at"),
            "entry_count": payload.get("entry_count"),
            "state": summary.get("state"),
            "runtime_status": summary.get("runtime_status"),
            "bluetooth_connected": summary.get("bluetooth_connected"),
            "task_status": summary.get("task_status"),
            "error": summary.get("error"),
            "error_active": summary.get("error_active"),
            "battery_level": summary.get("battery_level"),
            "device_time": summary.get("device_time"),
            "status_matrix": summary.get("status_matrix"),
            "auxiliary_live_properties": summary.get("auxiliary_live_properties"),
            "service_5_latest": summary.get("service_5_latest"),
            "unknown_non_empty_keys": summary.get("unknown_non_empty_keys"),
            "error_count": len(errors) if isinstance(errors, list) else None,
            "errors": errors if errors else None,
        }
    )


def _candidate_json_offsets(
    text: str, *, kind: str | None
) -> list[tuple[str | None, int]]:
    markers = (
        {kind: LOG_MARKERS[kind]}
        if kind is not None
        else LOG_MARKERS
    )
    offsets: list[tuple[str | None, int]] = []

    for marker_kind, marker in markers.items():
        search_from = 0
        while True:
            marker_index = text.find(marker, search_from)
            if marker_index == -1:
                break

            json_index = text.find("{", marker_index)
            if json_index != -1:
                offsets.append((marker_kind, json_index))
            search_from = marker_index + len(marker)

    if offsets:
        return sorted(offsets, key=lambda item: item[1])

    first_json = text.find("{")
    if first_json != -1:
        offsets.append((kind, first_json))
    return offsets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract Dreame lawn mower JSON from Home Assistant log lines or "
            "downloaded diagnostics."
        )
    )
    parser.add_argument("input", type=Path, help="Log or JSON file to parse")
    parser.add_argument(
        "--kind",
        choices=sorted(LOG_MARKERS),
        help="Only extract a specific log payload kind",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Return all matching payloads as a JSON array",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write pretty JSON to this path instead of stdout",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a compact triage summary instead of the full payload",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8", errors="replace")
    payloads = extract_payloads(text, kind=args.kind)
    if not payloads:
        raise SystemExit("No Dreame lawn mower JSON payload found")

    output: Any
    if args.all:
        output = []
        for payload in payloads:
            item = (
                summarize_payload(payload.payload)
                if args.summary
                else payload.payload
            )
            key = "summary" if args.summary else "payload"
            output.append({"kind": payload.kind, key: item})
    else:
        output = (
            summarize_payload(payloads[0].payload)
            if args.summary
            else payloads[0].payload
        )

    rendered = json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
