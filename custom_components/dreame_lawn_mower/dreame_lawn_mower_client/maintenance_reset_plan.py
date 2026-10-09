"""Shared guarded maintenance reset policy and transport effects."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .client_shared_helpers import _ensure_app_write_succeeded
from .exceptions import DreameLawnMowerConnectionError
from .maintenance import (
    build_cms_set_request,
    maintenance_item_status,
    maintenance_status_from_app_data,
    reset_cms_counter,
)
from .payload_utils import _json_safe


@dataclass(frozen=True)
class ReadMaintenance:
    """Read decoded counter state through the shared maintenance reader."""


@dataclass(frozen=True)
class WriteMaintenance:
    action: Mapping[str, Any]


type MaintenanceRequest = ReadMaintenance | WriteMaintenance


def plan_maintenance_reset(
    item: str, execute: bool = False, confirm_write: bool = False
) -> Generator[MaintenanceRequest, Any, dict[str, Any]]:
    """Share preflight, dry-run and confirmation evidence across transports."""
    if execute and not confirm_write:
        raise ValueError(
            "Maintenance resets require confirm_write=True when execute=True."
        )

    status = yield ReadMaintenance()
    values = status.get("raw_cms")
    if not isinstance(values, Sequence) or isinstance(
        values,
        str | bytes | bytearray,
    ):
        raise DreameLawnMowerConnectionError(
            "Could not read CMS maintenance counters before planning reset."
        )

    updated_values = reset_cms_counter(values, item)
    request = build_cms_set_request(updated_values)
    before = maintenance_item_status(status, item)
    planned_status = maintenance_status_from_app_data(
        {"value": updated_values},
        source="planned_maintenance_reset",
    )
    after = maintenance_item_status(planned_status, item)
    result: dict[str, Any] = {
        "source": "app_action_maintenance_cms",
        "action": "reset_maintenance_counter",
        "item": after.get("key") if isinstance(after, Mapping) else item,
        "item_name": after.get("name") if isinstance(after, Mapping) else item,
        "dry_run": not execute,
        "executed": False,
        "changed": list(values) != updated_values,
        "previous_cms": list(values),
        "updated_cms": updated_values,
        "previous_item": before,
        "updated_item": after,
        "request": request,
    }

    if not execute:
        return result

    response = yield WriteMaintenance(request)
    response_data = _ensure_app_write_succeeded(
        response,
        operation="Maintenance reset",
    )
    result["dry_run"] = False
    result["executed"] = True
    result["response"] = _json_safe(response, max_depth=4)
    result["response_data"] = _json_safe(response_data, max_depth=4)
    try:
        refreshed = yield ReadMaintenance()
        result["refreshed_cms"] = refreshed.get("raw_cms")
        result["refreshed_item"] = maintenance_item_status(refreshed, item)
    except Exception as err:  # noqa: BLE001 - write result is still useful
        result["refresh_error"] = str(err)
    return result


def capture_maintenance_result(
    plan: Generator[MaintenanceRequest, Any, dict[str, Any]],
    result: list[dict[str, Any]],
) -> Generator[MaintenanceRequest, Any]:
    result.append((yield from plan))


def run_maintenance_reset(
    plan: Generator[MaintenanceRequest, Any, dict[str, Any]],
    read: Callable[[], dict[str, Any]],
    write: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    """Drive the shared policy with legacy synchronous operations."""
    result: list[dict[str, Any]] = []
    driver = capture_maintenance_result(plan, result)
    try:
        request = next(driver)
        while True:
            try:
                response = (
                    read()
                    if isinstance(request, ReadMaintenance)
                    else write(request.action)
                )
            except Exception as error:
                request = driver.throw(error)
            else:
                request = driver.send(response)
    except StopIteration:
        return result[0]
    finally:
        driver.close()
