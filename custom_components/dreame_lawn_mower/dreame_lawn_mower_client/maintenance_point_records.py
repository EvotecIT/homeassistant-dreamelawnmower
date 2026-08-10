"""Parse and describe mower-native app-map maintenance-point records."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .client_shared_helpers import _operation_value_type

_REQUIRED_KEYS = frozenset({"id", "param", "point", "time", "type"})
_SUPPORTED_POINT_VECTOR_LENGTHS = frozenset({2, 3})
_MAINTENANCE_POINT_TYPE = 1


def app_map_maintenance_point_ids(entries: Sequence[Any]) -> list[int]:
    """Return positive ids from recognized app-map maintenance-point records."""
    result: list[int] = []
    for entry in entries:
        if not is_app_map_maintenance_point_record(entry):
            continue
        point_id = entry.get("id")
        if (
            not isinstance(point_id, int)
            or isinstance(point_id, bool)
            or point_id <= 0
            or point_id in result
        ):
            continue
        result.append(point_id)
    return result


def app_map_point_type_codes(entries: Sequence[Any]) -> list[int]:
    """Return numeric vendor type codes from recognized point records."""
    result: list[int] = []
    for entry in entries:
        if not _is_app_map_point_record(entry):
            continue
        point_type = entry.get("type")
        if (
            isinstance(point_type, int)
            and not isinstance(point_type, bool)
            and point_type not in result
        ):
            result.append(point_type)
    return sorted(result)


def app_map_point_record_diagnostics(
    entries: Sequence[Any],
) -> dict[str, Any]:
    """Describe rejected point value shapes without exposing values."""
    exact_shape_count = 0
    parser_accepted_count = 0
    identified_count = 0
    rejection_counts: dict[str, int] = {}
    grouped_shapes: dict[tuple[Any, ...], int] = {}

    for entry in entries:
        reasons = _maintenance_point_rejection_reasons(entry)
        for reason in reasons:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        if not isinstance(entry, Mapping) or set(entry) != _REQUIRED_KEYS:
            continue
        exact_shape_count += 1
        shape = _maintenance_point_value_shape(entry)
        grouped_shapes[shape] = grouped_shapes.get(shape, 0) + 1
        if _is_app_map_point_record(entry):
            parser_accepted_count += 1
            point_id = entry.get("id")
            if (
                isinstance(point_id, int)
                and not isinstance(point_id, bool)
                and point_id > 0
            ):
                identified_count += 1

    value_shapes: list[dict[str, Any]] = []
    for shape, count in sorted(grouped_shapes.items(), key=lambda item: repr(item[0])):
        (
            id_type,
            param_type,
            point_type,
            time_type,
            vendor_type,
            point_length,
            point_item_types,
        ) = shape
        value_shape: dict[str, Any] = {
            "count": count,
            "id_type": id_type,
            "param_type": param_type,
            "point_type": point_type,
            "time_type": time_type,
            "type_type": vendor_type,
        }
        if point_length is not None:
            value_shape["point_length"] = point_length
            value_shape["point_item_types"] = list(point_item_types)
        value_shapes.append(value_shape)

    return {
        "total_count": len(entries),
        "exact_shape_count": exact_shape_count,
        "parser_accepted_count": parser_accepted_count,
        "identified_count": identified_count,
        "rejection_reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(rejection_counts.items())
        ],
        "value_type_shapes": value_shapes,
    }


def is_app_map_maintenance_point_record(value: object) -> bool:
    """Recognize A2-family maintenance points, excluding patrol points."""
    return (
        _is_app_map_point_record(value)
        and value.get("type") == _MAINTENANCE_POINT_TYPE
    )


def _is_app_map_point_record(value: object) -> bool:
    """Recognize an exact A2-family point record without assigning semantics."""
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_KEYS:
        return False
    point_type = value.get("type")
    if not isinstance(point_type, int) or isinstance(point_type, bool):
        return False
    coordinates = value.get("point")
    if not _is_supported_point_vector(coordinates):
        return False
    return all(
        isinstance(coordinate, int | float)
        and not isinstance(coordinate, bool)
        and math.isfinite(float(coordinate))
        for coordinate in coordinates
    )


def _maintenance_point_rejection_reasons(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return ["not_object"]
    if set(value) != _REQUIRED_KEYS:
        return ["unexpected_keys"]

    reasons: list[str] = []
    point_id = value.get("id")
    if not isinstance(point_id, int) or isinstance(point_id, bool) or point_id <= 0:
        reasons.append("id_not_positive_integer")

    point_type = value.get("type")
    if not isinstance(point_type, int) or isinstance(point_type, bool):
        reasons.append("type_not_integer")

    coordinates = value.get("point")
    if not _is_supported_point_vector(coordinates):
        reasons.append("point_not_coordinate_pair")
    elif not all(
        isinstance(coordinate, int | float)
        and not isinstance(coordinate, bool)
        and math.isfinite(float(coordinate))
        for coordinate in coordinates
    ):
        reasons.append("point_coordinates_not_finite_numbers")
    return reasons


def _is_supported_point_vector(value: object) -> bool:
    """Accept observed XY and opaque three-value mower point vectors."""
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes | bytearray)
        and len(value) in _SUPPORTED_POINT_VECTOR_LENGTHS
    )


def _maintenance_point_value_shape(value: Mapping[str, Any]) -> tuple[Any, ...]:
    coordinates = value.get("point")
    if isinstance(coordinates, Sequence) and not isinstance(
        coordinates,
        str | bytes | bytearray,
    ):
        point_length: int | None = len(coordinates)
        point_item_types = tuple(
            sorted({_operation_value_type(item) for item in coordinates})
        )
    else:
        point_length = None
        point_item_types = ()
    return (
        _operation_value_type(value.get("id")),
        _operation_value_type(value.get("param")),
        _operation_value_type(coordinates),
        _operation_value_type(value.get("time")),
        _operation_value_type(value.get("type")),
        point_length,
        point_item_types,
    )
