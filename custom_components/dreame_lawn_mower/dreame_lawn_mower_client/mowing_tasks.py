"""Mower-native app task requests and response validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MOWING_TASK_EDGE = 101
MOWING_TASK_ZONE = 102
MOWING_TASK_SPOT = 103
MOWING_TASK_MAINTENANCE_POINT = 109


class MowingTaskResponseError(ValueError):
    """Raised when the mower rejects or does not acknowledge a task request."""


def build_zone_mowing_request(zone_ids: Sequence[int]) -> dict[str, Any]:
    """Build the Dreame app action used to mow one or more saved zones."""
    return _build_mowing_task_request(
        MOWING_TASK_ZONE,
        "region",
        _normalize_area_ids(zone_ids, area_type="zone"),
    )


def build_spot_mowing_request(spot_ids: Sequence[int]) -> dict[str, Any]:
    """Build the Dreame app action used to mow one or more saved spot areas."""
    return _build_mowing_task_request(
        MOWING_TASK_SPOT,
        "area",
        _normalize_area_ids(spot_ids, area_type="spot"),
    )


def build_edge_mowing_request(
    contour_ids: Sequence[Sequence[int]],
) -> dict[str, Any]:
    """Build the Dreame app action used to mow saved map edges."""
    normalized: list[list[int]] = []
    for contour_id in contour_ids:
        if len(contour_id) < 2:
            raise ValueError("Each contour id must contain two integer values.")
        normalized.append([int(contour_id[0]), int(contour_id[1])])
    if not normalized:
        raise ValueError("At least one contour id pair is required.")
    return _build_mowing_task_request(MOWING_TASK_EDGE, "edge", normalized)


def build_maintenance_point_request(
    point_ids: Sequence[int],
) -> dict[str, Any]:
    """Build the app action that drives to a configured maintenance point."""
    normalized = _normalize_area_ids(point_ids, area_type="maintenance point")
    if len(normalized) > 5:
        raise ValueError("At most five maintenance point ids are supported.")
    return _build_mowing_task_request(
        MOWING_TASK_MAINTENANCE_POINT,
        "point",
        normalized,
    )


def ensure_mowing_task_succeeded(
    response: Any,
    *,
    task_name: str,
) -> Any:
    """Return a successful app response or raise for missing/rejected replies."""
    if not isinstance(response, Mapping):
        raise MowingTaskResponseError(
            f"The mower did not acknowledge the {task_name} request."
        )

    result = response.get("r")
    response_data = response.get("d")
    nested_result = (
        response_data.get("r") if isinstance(response_data, Mapping) else None
    )
    if result != 0 or nested_result not in (None, 0):
        detail = response.get("msg") or response.get("message") or response.get("d")
        suffix = f": {detail}" if detail not in (None, "", {}, []) else ""
        failure_result = result if result != 0 else nested_result
        raise MowingTaskResponseError(
            f"The mower rejected the {task_name} request "
            f"(result {failure_result!r}){suffix}."
        )
    return response


def _normalize_area_ids(
    area_ids: Sequence[int],
    *,
    area_type: str,
) -> list[int]:
    normalized: list[int] = []
    for area_id in area_ids:
        if isinstance(area_id, bool):
            raise ValueError(f"{area_type.capitalize()} ids must be positive integers.")
        try:
            value = int(area_id)
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"{area_type.capitalize()} ids must be positive integers."
            ) from err
        if value <= 0:
            raise ValueError(f"{area_type.capitalize()} ids must be positive integers.")
        normalized.append(value)

    if not normalized:
        raise ValueError(f"At least one {area_type} id is required.")
    return normalized


def _build_mowing_task_request(
    operation: int,
    target_key: str,
    target_ids: Sequence[Any],
) -> dict[str, Any]:
    return {
        "m": "a",
        "p": 0,
        "o": operation,
        "d": {target_key: list(target_ids)},
    }
