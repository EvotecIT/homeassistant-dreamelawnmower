"""Build privacy-aware integration report sections."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .debug import sanitize_debug_data, sanitize_diagnostic_text

_SYSTEM_INFO_KEYS = (
    "installation_type",
    "version",
    "dev",
    "hassio",
    "virtualenv",
    "python_version",
    "docker",
    "arch",
    "container_arch",
    "os_name",
    "os_version",
    "host_os",
    "supervisor",
    "docker_version",
    "chassis",
)

_SENSITIVE_ENTITY_STATE_NAMES = frozenset(
    {
        "runtimepositionx",
        "runtimepositiony",
        "serialnumber",
    }
)

_MAINTENANCE_POINT_REJECTION_REASONS = frozenset(
    {
        "id_not_positive_integer",
        "not_object",
        "point_coordinates_not_finite_numbers",
        "point_not_coordinate_pair",
        "type_not_integer",
        "unexpected_keys",
    }
)
_MAINTENANCE_POINT_VALUE_TYPES = frozenset(
    {"array", "bool", "null", "number", "object", "string"}
)


def build_report_context(
    *,
    system_info: Mapping[str, Any],
    integration_version: object,
    config_entry: object,
) -> dict[str, Any]:
    """Return version and host facts needed to reproduce integration issues."""
    return {
        "integration_version": str(integration_version),
        "home_assistant": {
            key: system_info[key] for key in _SYSTEM_INFO_KEYS if key in system_info
        },
        "config_entry": {
            "state": _enum_value(getattr(config_entry, "state", None)),
            "disabled_by": _enum_value(getattr(config_entry, "disabled_by", None)),
            "version": getattr(config_entry, "version", None),
            "minor_version": getattr(config_entry, "minor_version", None),
        },
    }


def build_coordinator_diagnostics(coordinator: object) -> dict[str, Any]:
    """Return the latest coordinator health without exposing account data."""
    update_interval = getattr(coordinator, "update_interval", None)
    last_exception = getattr(coordinator, "last_exception", None)
    performance = getattr(coordinator, "performance", None)
    last_map_probe = getattr(coordinator, "last_map_probe_result", None)
    return {
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "last_exception_type": (
            type(last_exception).__name__ if last_exception is not None else None
        ),
        "last_exception": (
            sanitize_diagnostic_text(last_exception)
            if last_exception is not None
            else None
        ),
        "update_interval_seconds": (
            update_interval.total_seconds()
            if hasattr(update_interval, "total_seconds")
            else None
        ),
        "performance": (
            performance.as_dict() if hasattr(performance, "as_dict") else None
        ),
        "maintenance_points": build_maintenance_point_diagnostics(coordinator),
        "last_maintenance_point_probe": (
            dict(last_map_probe) if isinstance(last_map_probe, Mapping) else None
        ),
    }


def build_maintenance_point_diagnostics(
    coordinator: object,
    *,
    map_probe_payload: Mapping[str, Any] | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Return point availability evidence without map coordinates or payloads."""
    app_maps = getattr(coordinator, "app_maps", None)
    vector_details = getattr(coordinator, "vector_map_details", None)
    source = "coordinator_cache"

    if isinstance(map_probe_payload, Mapping):
        probed_app_maps = map_probe_payload.get("app_maps")
        if isinstance(probed_app_maps, Mapping):
            app_maps = probed_app_maps
        vector_view = map_probe_payload.get("batch_vector_map")
        if isinstance(vector_view, Mapping):
            probed_vector_details = vector_view.get("details")
            if isinstance(probed_vector_details, Mapping):
                vector_details = probed_vector_details
        source = "map_probe"

    app_map_entries = _maintenance_app_map_entries(app_maps)
    vector_map_entries = _maintenance_vector_map_entries(vector_details)
    current_map_index = None
    if isinstance(map_probe_payload, Mapping) and isinstance(app_maps, Mapping):
        current_map_index = _map_index(app_maps.get("current_map_index"))
    if current_map_index is None:
        current_map_index = _map_index(
            getattr(coordinator, "selected_map_index", None)
        )
    if current_map_index is None and isinstance(app_maps, Mapping):
        current_map_index = _map_index(app_maps.get("current_map_index"))
    if current_map_index is None and isinstance(vector_details, Mapping):
        current_map_index = _map_index(vector_details.get("map_index"))

    current_app_entry = next(
        (
            entry
            for entry in app_map_entries
            if entry.get("map_index") == current_map_index
        ),
        None,
    )
    current_vector_entry = next(
        (
            entry
            for entry in vector_map_entries
            if entry.get("map_index") == current_map_index
        ),
        None,
    )
    current_app_point_count = (
        current_app_entry.get("point_count") if current_app_entry else None
    )
    current_app_point_ids = (
        current_app_entry.get("maintenance_point_ids")
        if current_app_entry
        else []
    )
    current_vector_point_ids = (
        current_vector_entry.get("point_ids") if current_vector_entry else []
    )
    mismatch = bool(current_app_point_count) and not current_vector_point_ids
    control_point_ids = current_vector_point_ids or current_app_point_ids

    result = {
        "source": source,
        "current_map_index": current_map_index,
        "selected_point_id": _positive_int(
            getattr(coordinator, "selected_maintenance_point_id", None)
        ),
        "control_ready": bool(control_point_ids),
        "control_point_source": (
            "vector_map"
            if current_vector_point_ids
            else "app_map"
            if current_app_point_ids
            else None
        ),
        "app_points_without_vector_ids": mismatch,
        "app_maps": app_map_entries,
        "vector_maps": vector_map_entries,
    }
    if source == "map_probe":
        result["captured_at"] = captured_at
    return result


def _maintenance_app_map_entries(value: object) -> list[dict[str, Any]]:
    maps = value.get("maps") if isinstance(value, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(
        maps,
        str | bytes | bytearray,
    ):
        return []

    result: list[dict[str, Any]] = []
    for item in maps:
        if not isinstance(item, Mapping):
            continue
        summary = item.get("summary")
        if not isinstance(summary, Mapping):
            summary = {}
        payload_keys = item.get("payload_keys")
        point_shapes = summary.get("point_entry_shapes")
        point_ids = summary.get("maintenance_point_ids")
        point_type_codes = summary.get("point_type_codes")
        record_validation = _maintenance_point_record_validation(
            summary.get("point_record_validation")
        )
        entry = {
            "map_index": _map_index(item.get("idx")),
            "current": bool(item.get("current")),
            "available": bool(item.get("available")),
            "point_payload_present": (
                "point" in payload_keys
                if isinstance(payload_keys, Sequence)
                and not isinstance(payload_keys, str | bytes | bytearray)
                else None
            ),
            "point_count": _non_negative_int(summary.get("point_count")),
            "point_entry_shapes": (
                list(point_shapes)
                if isinstance(point_shapes, Sequence)
                and not isinstance(point_shapes, str | bytes | bytearray)
                else []
            ),
            "maintenance_point_ids": (
                [
                    point_id
                    for point_id in point_ids
                    if _positive_int(point_id) is not None
                ]
                if isinstance(point_ids, Sequence)
                and not isinstance(point_ids, str | bytes | bytearray)
                else []
            ),
            "point_type_codes": (
                [
                    point_type
                    for point_type in point_type_codes
                    if isinstance(point_type, int)
                    and not isinstance(point_type, bool)
                ]
                if isinstance(point_type_codes, Sequence)
                and not isinstance(
                    point_type_codes,
                    str | bytes | bytearray,
                )
                else []
            ),
        }
        if record_validation is not None:
            entry["point_record_validation"] = record_validation
        result.append(entry)
    return result


def _maintenance_point_record_validation(
    value: object,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None

    result: dict[str, Any] = {
        key: _non_negative_int(value.get(key))
        for key in (
            "total_count",
            "exact_shape_count",
            "parser_accepted_count",
            "identified_count",
        )
    }
    reasons = value.get("rejection_reason_counts")
    result["rejection_reason_counts"] = (
        [
            {
                "reason": reason,
                "count": count,
            }
            for item in reasons
            if isinstance(item, Mapping)
            and isinstance((reason := item.get("reason")), str)
            and reason in _MAINTENANCE_POINT_REJECTION_REASONS
            and (count := _positive_int(item.get("count"))) is not None
        ]
        if isinstance(reasons, Sequence)
        and not isinstance(
            reasons,
            str | bytes | bytearray,
        )
        else []
    )

    shapes = value.get("value_type_shapes")
    safe_shapes: list[dict[str, Any]] = []
    if isinstance(shapes, Sequence) and not isinstance(
        shapes,
        str | bytes | bytearray,
    ):
        for shape in shapes:
            if not isinstance(shape, Mapping):
                continue
            count = _positive_int(shape.get("count"))
            if count is None:
                continue
            safe_shape: dict[str, Any] = {"count": count}
            for key in (
                "id_type",
                "param_type",
                "point_type",
                "time_type",
                "type_type",
            ):
                value_type = shape.get(key)
                if (
                    isinstance(value_type, str)
                    and value_type in _MAINTENANCE_POINT_VALUE_TYPES
                ):
                    safe_shape[key] = value_type
            point_length = _non_negative_int(shape.get("point_length"))
            if point_length is not None:
                safe_shape["point_length"] = point_length
                point_item_types = shape.get("point_item_types")
                safe_shape["point_item_types"] = (
                    sorted(
                        {
                            item_type
                            for item_type in point_item_types
                            if isinstance(item_type, str)
                            and item_type in _MAINTENANCE_POINT_VALUE_TYPES
                        }
                    )
                    if isinstance(point_item_types, Sequence)
                    and not isinstance(
                        point_item_types,
                        str | bytes | bytearray,
                    )
                    else []
                )
            safe_shapes.append(safe_shape)
    result["value_type_shapes"] = safe_shapes
    return result


def _maintenance_vector_map_entries(value: object) -> list[dict[str, Any]]:
    maps = value.get("maps") if isinstance(value, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(
        maps,
        str | bytes | bytearray,
    ):
        maps = [value] if isinstance(value, Mapping) else []

    result: list[dict[str, Any]] = []
    for item in maps:
        if not isinstance(item, Mapping):
            continue
        point_ids = item.get("clean_point_ids")
        safe_point_ids = (
            [
                point_id
                for point_id in point_ids
                if _positive_int(point_id) is not None
            ]
            if isinstance(point_ids, Sequence)
            and not isinstance(point_ids, str | bytes | bytearray)
            else []
        )
        result.append(
            {
                "map_index": _map_index(item.get("map_index")),
                "point_count": _non_negative_int(item.get("clean_point_count")),
                "point_ids": safe_point_ids,
            }
        )
    return result


def _map_index(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _positive_int(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else None
    )


def _non_negative_int(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def build_entity_diagnostics(
    registry_entries: Iterable[object],
    state_getter: Callable[[str], object | None],
) -> list[dict[str, Any]]:
    """Return sanitized state for every entity owned by one config entry."""
    entities: list[dict[str, Any]] = []
    for registry_entry in registry_entries:
        entity_id = str(getattr(registry_entry, "entity_id", ""))
        domain = entity_id.partition(".")[0] or None
        state = state_getter(entity_id) if entity_id else None
        state_value = getattr(state, "state", None)
        attributes = getattr(state, "attributes", {}) if state is not None else {}
        original_name = getattr(registry_entry, "original_name", None)
        translation_key = getattr(registry_entry, "translation_key", None)
        unique_id = getattr(registry_entry, "unique_id", None)
        if _entity_state_is_sensitive(original_name, translation_key, unique_id):
            state_value = "**REDACTED**" if state_value is not None else None
        entities.append(
            {
                "domain": domain,
                "original_name": original_name,
                "translation_key": translation_key,
                "entity_category": _enum_value(
                    getattr(registry_entry, "entity_category", None)
                ),
                "disabled_by": _enum_value(
                    getattr(registry_entry, "disabled_by", None)
                ),
                "loaded": state is not None,
                "available": state_value not in {None, "unavailable"},
                "state": state_value,
                "attributes": sanitize_debug_data(attributes),
            }
        )

    entities.sort(
        key=lambda item: (
            str(item.get("domain") or ""),
            str(item.get("original_name") or item.get("translation_key") or ""),
        )
    )
    return entities


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _entity_state_is_sensitive(
    original_name: object,
    translation_key: object,
    unique_id: object,
) -> bool:
    """Return whether an entity's scalar state contains private report data."""
    normalized_values = (
        "".join(character for character in str(value).casefold() if character.isalnum())
        for value in (original_name, translation_key, unique_id)
        if value is not None
    )
    return any(
        value.endswith(sensitive_name)
        for value in normalized_values
        for sensitive_name in _SENSITIVE_ENTITY_STATE_NAMES
    )
