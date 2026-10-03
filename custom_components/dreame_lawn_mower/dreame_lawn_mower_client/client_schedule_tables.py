"""Table schedule transport and writes with authoritative readback."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .client_shared_helpers import _ensure_app_write_succeeded
from .exceptions import DreameLawnMowerConnectionError, mark_write_attempted
from .payload_utils import _json_safe
from .schedule_tables import (
    combine_schedule_table_weeks,
    decode_schedule_table_task,
    decode_schedule_tables,
    schedule_table_ids,
)


class _DreameLawnMowerScheduleTablesMixin:
    """Adapt table records to the same model used by document schedules."""

    def _sync_read_schedule_tables(
        self,
        *,
        map_index: int,
        deadline: float,
        include_raw: bool = False,
        include_tasks: bool = True,
    ) -> dict[str, Any]:
        response = self._sync_call_app_action(
            {"m": "g", "t": "SCHDI", "d": list(schedule_table_ids(map_index))},
            retry_count=0,
            timeout=5.0,
            deadline=deadline,
        )
        plans = decode_schedule_tables(_table_data(response), map_index=map_index)
        errors: list[dict[str, Any]] = []
        for plan in plans:
            task_weeks = []
            if include_tasks:
                for task_id in plan["task_references"]:
                    try:
                        task_response = self._sync_call_app_action(
                            {"m": "g", "t": "SCHDC", "d": [plan["table_id"], task_id]},
                            retry_count=0,
                            timeout=5.0,
                            deadline=deadline,
                        )
                        task_weeks.append(
                            decode_schedule_table_task(
                                _table_data(task_response),
                                task_id=task_id,
                            )
                        )
                    except Exception as err:  # noqa: BLE001 - retain observed flags
                        plan["tasks_complete"] = False
                        errors.append(
                            {
                                "plan_id": plan["plan_id"],
                                "task_id": task_id,
                                "error": str(err),
                            }
                        )
                plan["weeks"] = combine_schedule_table_weeks(task_weeks)
            else:
                plan["tasks_complete"] = not plan["task_references"]
        result = {
            "idx": map_index,
            "label": f"map_{map_index}",
            "protocol": "tables",
            "available": bool(plans),
            "plans": plans,
            "plan_count": len(plans),
            "enabled_plan_count": sum(plan["enabled"] for plan in plans),
            "read_status": "partial" if errors else "complete",
            "task_errors": errors,
        }
        if include_raw:
            result["raw_info"] = _json_safe(response, max_depth=6)
        return result

    def _sync_set_schedule_table_enabled(
        self,
        *,
        schedule: Mapping[str, Any],
        plan_id: int,
        enabled: bool,
        execute: bool,
    ) -> dict[str, Any]:
        plans = schedule["plans"]
        if {plan["table_id"] for plan in plans} != set(
            schedule_table_ids(schedule["idx"])
        ):
            raise ValueError("Both schedule table states must be known before a write.")
        target = next((plan for plan in plans if plan["plan_id"] == plan_id), None)
        if target is None:
            raise ValueError(f"Schedule plan {plan_id} was not reported by the mower.")
        if enabled and (not target["tasks_complete"] or not target["weeks"]):
            raise ValueError(
                "Cannot enable a schedule with missing or no enabled tasks."
            )
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
            response = self._sync_call_app_action(request, retry_count=0)
            _ensure_app_write_succeeded(
                response,
                operation="Schedule table write",
                allow_missing_data=True,
            )
            observed = self._sync_read_schedule_tables(
                map_index=schedule["idx"],
                deadline=time.monotonic() + 5.0,
                include_tasks=False,
            )
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
                    plan
                    for plan in plans
                    if plan["table_id"] == observed_plan["table_id"]
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


def _table_data(response: Any) -> Any:
    """Require an explicit success code; a failure value is not an empty list."""
    if (
        not isinstance(response, Mapping)
        or response.get("r") != 0
        or isinstance(response.get("r"), bool)
    ):
        raise DreameLawnMowerConnectionError(
            "Schedule table read was not acknowledged."
        )
    return response.get("d")
