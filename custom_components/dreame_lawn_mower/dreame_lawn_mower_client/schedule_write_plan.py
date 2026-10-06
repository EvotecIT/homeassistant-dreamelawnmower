"""Shared schedule document/table write policy, independent of transport."""

from __future__ import annotations

import time
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .client_settings_helpers import (
    _schedule_entry_overview,
    _schedule_plan_overview,
    _schedule_upload_overview,
)
from .client_shared_helpers import _ensure_app_write_succeeded, _positive_int
from .exceptions import DreameLawnMowerConnectionError, mark_write_attempted
from .payload_utils import _json_safe
from .schedule import (
    EMPTY_SCHEDULE_VERSION,
    SCHEDULE_CHUNK_SIZE,
    build_schedule_enable_status_request,
    build_schedule_upload_requests,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
)
from .schedule_tables import schedule_table_ids


@dataclass(frozen=True)
class ReadSchedules:
    map_index: int
    include_raw: bool = False


@dataclass(frozen=True)
class RequireWriteAllowed:
    """Force fresh task-state validation before a schedule mutation."""


@dataclass(frozen=True)
class ScheduleCommand:
    action: Mapping[str, Any]
    retry_count: int | None = None
    timeout: float | None = None


@dataclass(frozen=True)
class ReadTableFlags:
    map_index: int
    deadline: float


type ScheduleWriteRequest = (
    ReadSchedules | RequireWriteAllowed | ScheduleCommand | ReadTableFlags
)


def plan_schedule_enabled(
    map_index: int,
    plan_id: int,
    enabled: bool,
    execute: bool = False,
    confirm_write: bool = False,
) -> Generator[ScheduleWriteRequest, Any, dict[str, Any]]:
    """Build or execute a schedule enable-status app action request."""
    if execute and not confirm_write:
        raise ValueError(
            "Schedule writes require confirm_write=True when execute=True."
        )

    schedules = yield ReadSchedules(map_index)
    if not schedules.get("schedules"):
        raise DreameLawnMowerConnectionError(
            f"No schedule metadata returned for map index {map_index}."
        )
    schedule = schedules["schedules"][0]
    if execute:
        yield RequireWriteAllowed()
    if schedule.get("protocol") == "tables":
        return (
            yield from plan_table_enabled(
                schedule=schedule,
                plan_id=plan_id,
                enabled=enabled,
                execute=execute,
            )
        )
    version = _positive_int(schedule.get("version"))
    if version is None or version == EMPTY_SCHEDULE_VERSION:
        raise DreameLawnMowerConnectionError(
            f"No writable schedule version returned for map index {map_index}."
        )
    plans = schedule.get("plans")
    if not isinstance(plans, list):
        raise DreameLawnMowerConnectionError(
            f"No decoded schedule plans returned for map index {map_index}."
        )

    updated_plans: list[dict[str, Any]] = []
    previous_enabled: bool | None = None
    found = False
    for plan in plans:
        if not isinstance(plan, Mapping):
            continue
        updated_plan = dict(plan)
        if _positive_int(updated_plan.get("plan_id")) == plan_id:
            previous_enabled = bool(updated_plan.get("enabled"))
            updated_plan["enabled"] = bool(enabled)
            found = True
        updated_plans.append(updated_plan)
    if not found:
        raise ValueError(
            f"Schedule plan {plan_id} was not found for map index {map_index}."
        )

    request = build_schedule_enable_status_request(
        map_index=map_index,
        version=version,
        plans=updated_plans,
        document_version=schedule.get("document_version", 2),
    )
    target_enabled = bool(enabled)
    result: dict[str, Any] = {
        "source": "app_action_schedule_write",
        "action": "set_schedule_plan_enabled",
        "dry_run": not execute,
        "executed": False,
        "map_index": map_index,
        "plan_id": plan_id,
        "previous_enabled": previous_enabled,
        "enabled": target_enabled,
        "changed": (
            previous_enabled is not None and previous_enabled != target_enabled
        ),
        "schedule": _schedule_entry_overview(schedule),
        "target_plan": _schedule_plan_overview(
            updated_plans,
            plan_id=plan_id,
            previous_enabled=previous_enabled,
            enabled=target_enabled,
        ),
        "version": version,
        "request": request,
    }
    if execute:
        response = yield ScheduleCommand(request)
        response_data = _ensure_app_write_succeeded(
            response,
            operation="Schedule write",
        )
        result["executed"] = True
        result["response"] = _json_safe(response, max_depth=4)
        result["response_data"] = _json_safe(response_data, max_depth=4)
        result["acknowledged_plan_states"] = [
            {"plan_id": plan["plan_id"], "enabled": bool(plan.get("enabled"))}
            for plan in updated_plans
        ]
        acknowledged_version = (
            response_data.get("v") if isinstance(response_data, Mapping) else None
        )
        if (
            isinstance(acknowledged_version, int)
            and not isinstance(acknowledged_version, bool)
            and 0 <= acknowledged_version < EMPTY_SCHEDULE_VERSION
        ):
            # Enabling a plan changes the document checksum on newer firmware.
            # Keep the submitted version in the request and schedule overview.
            result["version"] = acknowledged_version
    return result


def plan_schedule_upload(
    map_index: int,
    plans: Sequence[Mapping[str, Any]],
    execute: bool = False,
    confirm_write: bool = False,
    chunk_size: int = SCHEDULE_CHUNK_SIZE,
) -> Generator[ScheduleWriteRequest, Any, dict[str, Any]]:
    """Build or execute a full schedule upload request sequence."""
    if execute and not confirm_write:
        raise ValueError(
            "Schedule writes require confirm_write=True when execute=True."
        )
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    if not isinstance(plans, Sequence) or isinstance(plans, str | bytes):
        raise ValueError("plans must be a sequence of schedule plan mappings.")

    schedules = yield ReadSchedules(map_index)
    if not schedules.get("schedules"):
        raise DreameLawnMowerConnectionError(
            f"No schedule metadata returned for map index {map_index}."
        )
    schedule = schedules["schedules"][0]
    if schedule.get("protocol") == "tables":
        raise ValueError(
            "Full plan upload is unavailable for table schedules. "
            "Edit task times in the vendor app or use a Home Assistant schedule."
        )
    version = _positive_int(schedule.get("version"))
    if version is None or version == EMPTY_SCHEDULE_VERSION:
        raise DreameLawnMowerConnectionError(
            f"No writable schedule version returned for map index {map_index}."
        )
    current_plans = schedule.get("plans")
    if not isinstance(current_plans, list):
        raise DreameLawnMowerConnectionError(
            f"No decoded schedule plans returned for map index {map_index}."
        )

    if schedule.get("document_version") == 3 or any(
        plan.get("task_payload_format") == "framed"
        for plan in current_plans
        if isinstance(plan, Mapping)
    ):
        raise ValueError(
            "Full plan upload is unavailable for V3/framed schedules. "
            "Edit task times in the vendor app or use a Home Assistant schedule."
        )

    try:
        payload_text = encode_schedule_payload_text(list(plans))
        normalized_plans = decode_schedule_payload_text(payload_text)
    except Exception as err:  # noqa: BLE001 - caller gets readable validator text
        raise ValueError(f"Invalid schedule plans: {err}") from err

    current_payload_text = encode_schedule_payload_text(current_plans)
    requests = build_schedule_upload_requests(
        map_index=map_index,
        payload_text=payload_text,
        version=version,
        chunk_size=chunk_size,
    )
    request_candidate: dict[str, Any] | None = (
        requests[0]
        if len(requests) == 1
        else {"sequence": requests}
        if requests
        else None
    )
    result: dict[str, Any] = {
        "source": "app_action_schedule_write",
        "action": "upload_schedule_plans",
        "dry_run": not execute,
        "executed": False,
        "map_index": map_index,
        "changed": current_payload_text != payload_text,
        "version": version,
        "chunk_size": chunk_size,
        "chunk_count": max(len(requests) - 1, 0),
        "payload_size": len(payload_text.encode("utf-8")),
        "schedule": _schedule_entry_overview(schedule),
        "target_schedule": _schedule_upload_overview(normalized_plans),
        "request": request_candidate,
    }
    if execute:
        yield RequireWriteAllowed()
        responses: list[Any] = []
        response_data_items: list[Any] = []
        for request in requests:
            response = yield ScheduleCommand(request)
            response_data = _ensure_app_write_succeeded(
                response,
                operation="Schedule upload",
            )
            responses.append(_json_safe(response, max_depth=4))
            response_data_items.append(_json_safe(response_data, max_depth=4))
        result["executed"] = True
        result["response"] = responses
        result["response_data"] = response_data_items
    return result


def plan_table_enabled(
    *,
    schedule: Mapping[str, Any],
    plan_id: int,
    enabled: bool,
    execute: bool,
) -> Generator[ScheduleWriteRequest, Any, dict[str, Any]]:
    plans = schedule["plans"]
    if {plan["table_id"] for plan in plans} != set(schedule_table_ids(schedule["idx"])):
        raise ValueError("Both schedule table states must be known before a write.")
    target = next((plan for plan in plans if plan["plan_id"] == plan_id), None)
    if target is None:
        raise ValueError(f"Schedule plan {plan_id} was not reported by the mower.")
    if enabled and (not target["tasks_complete"] or not target["weeks"]):
        raise ValueError("Cannot enable a schedule with missing or no enabled tasks.")
    request = {"m": "s", "t": "SCHDS", "d": [target["table_id"], int(enabled)]}
    result = {
        "source": "app_action_schedule_write",
        "protocol": "tables",
        "action": "set_schedule_plan_enabled",
        "map_index": schedule["idx"],
        "plan_id": plan_id,
        "enabled": bool(enabled),
        "previous_enabled": target["enabled"],
        "changed": target["enabled"] != bool(enabled),
        "dry_run": not execute,
        "executed": False,
        "confirmed": False,
        "request": request,
    }
    if not execute:
        return result
    try:
        response = yield ScheduleCommand(request, retry_count=0)
        _ensure_app_write_succeeded(
            response,
            operation="Schedule table write",
            allow_missing_data=True,
        )
        observed = yield ReadTableFlags(schedule["idx"], time.monotonic() + 5.0)
        confirmed_target = next(
            (plan for plan in observed["plans"] if plan["plan_id"] == plan_id), None
        )
        # Both flags matter: enabling one table can disable its sibling.
        if (
            {plan["table_id"] for plan in observed["plans"]}
            != {plan["table_id"] for plan in plans}
            or confirmed_target is None
            or confirmed_target["enabled"] != bool(enabled)
        ):
            raise DreameLawnMowerConnectionError(
                "Schedule table write was not confirmed by complete flag readback."
            )
        for observed_plan in observed["plans"]:
            original = next(
                plan for plan in plans if plan["table_id"] == observed_plan["table_id"]
            )
            if original["task_references"] != observed_plan["task_references"]:
                raise DreameLawnMowerConnectionError(
                    "Schedule tasks changed during flag readback; refresh required."
                )
            observed_plan["weeks"] = original["weeks"]
            observed_plan["tasks_complete"] = original["tasks_complete"]
        result.update(executed=True, confirmed=True, confirmed_schedule=observed)
        return result
    except Exception as err:
        mark_write_attempted(err, fields=["schedule"])
        raise
