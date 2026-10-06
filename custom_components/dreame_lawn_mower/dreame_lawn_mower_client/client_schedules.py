"""Mower-native schedule discovery, reading, and guarded writes."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from functools import wraps
from typing import Any

from .app_read_transport import run_app_read
from .batch_device_data import decode_batch_schedule_payload
from .client_schedule_edits import _DreameLawnMowerScheduleEditsMixin
from .client_schedule_tables import _DreameLawnMowerScheduleTablesMixin
from .client_settings_helpers import (
    _batch_schedule_keys,
    _schedule_entry_overview,
    _schedule_plan_overview,
    _schedule_upload_overview,
)
from .client_shared_helpers import (
    _ensure_app_write_succeeded,
    _positive_int,
)
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)
from .payload_utils import _json_safe
from .schedule import (
    EMPTY_SCHEDULE_VERSION,
    SCHEDULE_CHUNK_SIZE,
    build_schedule_enable_status_request,
    build_schedule_upload_requests,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
    schedule_write_block_reason,
)
from .schedule_read_plan import (
    read_document_generation,
    read_document_slot,
    read_document_text,
    read_map_indices,
    read_schedules,
    read_slot,
    read_start_evidence,
)

SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS = 5.0
SCHEDULE_READ_DEADLINE_SECONDS = 10.0
SCHEDULE_READ_TIMEOUT_SECONDS = 5.0


def _serialized_schedule_operation(method):
    """Keep schedule reads and read/modify/write operations coherent."""

    @wraps(method)
    def serialized(self, *args, **kwargs):
        with self._schedule_operation_lock:
            return method(self, *args, **kwargs)

    return serialized


class _DreameLawnMowerClientSchedulesMixin(
    _DreameLawnMowerScheduleTablesMixin, _DreameLawnMowerScheduleEditsMixin
):
    """Own schedule protocol operations independently of other settings."""

    def _sync_require_schedule_write_allowed(self) -> None:
        """Check fresh normalized task state inside the schedule operation lock."""
        device = self._sync_update_device(force_request_properties=True)
        snapshot = self._snapshot_from_device(device, fresh_task_state=True)
        reason = schedule_write_block_reason(snapshot)
        if reason is not None:
            raise DreameLawnMowerCommandRejectedError(reason)

    @_serialized_schedule_operation
    def _sync_get_schedule_start_evidence(self) -> dict[str, Any]:
        """Never replace failed map discovery with likely-slot guesses for a start."""
        return run_app_read(
            read_start_evidence(self), self._sync_call_app_action,
        )

    @_serialized_schedule_operation
    def _sync_get_app_schedules(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
        include_current_task: bool = True,
    ) -> dict[str, Any]:
        return run_app_read(
            read_schedules(
                self, include_raw, map_indices, chunk_size, include_current_task
            ),
            self._sync_call_app_action,
        )

    def _sync_get_app_schedule_slot(
        self,
        *,
        map_index: int,
        chunk_size: int,
        include_raw: bool,
        deadline: float,
        reserve_alternate: bool = True,
    ) -> tuple[dict[str, Any], Exception | None]:
        return run_app_read(
            read_slot(
                self,
                map_index=map_index,
                chunk_size=chunk_size,
                include_raw=include_raw,
                deadline=deadline,
                reserve_alternate=reserve_alternate,
            ),
            self._sync_call_app_action,
        )

    def _sync_get_document_schedule_slot(
        self,
        *,
        map_index: int,
        chunk_size: int,
        include_raw: bool,
        deadline: float,
        metadata_deadline: float | None = None,
        reserve_generation: bool = True,
    ) -> tuple[dict[str, Any], Exception | None]:
        return run_app_read(
            read_document_slot(
                self,
                map_index=map_index,
                chunk_size=chunk_size,
                include_raw=include_raw,
                deadline=deadline,
                metadata_deadline=metadata_deadline,
                reserve_generation=reserve_generation,
            ),
            self._sync_call_app_action,
        )

    def _sync_get_document_schedule_generation(
        self,
        *,
        map_index: int,
        chunk_size: int,
        include_raw: bool,
        deadline: float,
        metadata_deadline: float,
        generation: int,
    ) -> tuple[dict[str, Any], Exception | None]:
        return run_app_read(
            read_document_generation(
                self,
                map_index=map_index,
                chunk_size=chunk_size,
                include_raw=include_raw,
                deadline=deadline,
                metadata_deadline=metadata_deadline,
                generation=generation,
            ),
            self._sync_call_app_action,
        )

    @_serialized_schedule_operation
    def _sync_set_app_schedule_plan_enabled(
        self,
        map_index: int,
        plan_id: int,
        enabled: bool,
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute a schedule enable-status app action request."""
        if execute and not confirm_write:
            raise ValueError(
                "Schedule writes require confirm_write=True when execute=True."
            )

        schedules = self._sync_get_app_schedules(
            map_indices=[map_index], include_current_task=False
        )
        if not schedules.get("schedules"):
            raise DreameLawnMowerConnectionError(
                f"No schedule metadata returned for map index {map_index}."
            )
        schedule = schedules["schedules"][0]
        if execute:
            self._sync_require_schedule_write_allowed()
        if schedule.get("protocol") == "tables":
            return self._sync_set_schedule_table_enabled(
                schedule=schedule,
                plan_id=plan_id,
                enabled=enabled,
                execute=execute,
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
            response = self._sync_call_app_action(request)
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

    @_serialized_schedule_operation
    def _sync_plan_app_schedule_upload(
        self,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        execute: bool = False,
        confirm_write: bool = False,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Build or execute a full schedule upload request sequence."""
        if execute and not confirm_write:
            raise ValueError(
                "Schedule writes require confirm_write=True when execute=True."
            )
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")
        if not isinstance(plans, Sequence) or isinstance(plans, str | bytes):
            raise ValueError("plans must be a sequence of schedule plan mappings.")

        schedules = self._sync_get_app_schedules(
            map_indices=[map_index], include_current_task=False
        )
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
            self._sync_require_schedule_write_allowed()
            responses: list[Any] = []
            response_data_items: list[Any] = []
            for request in requests:
                response = self._sync_call_app_action(request)
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

    def _sync_get_batch_schedules(
        self,
        include_raw: bool = False,
        map_index_hint: int | None = None,
        discover_map_index: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode schedule data from batch device data."""
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        deadline = time.monotonic() + timeout if timeout is not None else None
        if map_index_hint is None and discover_map_index:
            map_index_hint = self._sync_get_current_app_map_index(deadline=deadline)
        batch_data = self._sync_get_batch_device_data(
            _batch_schedule_keys(),
            deadline=deadline,
        )
        return decode_batch_schedule_payload(
            batch_data,
            include_raw=include_raw,
            map_index_hint=map_index_hint,
        )

    def _sync_get_app_schedule_text(
        self,
        *,
        size: int,
        version: int,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
        deadline: float | None = None,
        document_version: int = 2,
    ) -> tuple[str, int, int]:
        return run_app_read(
            read_document_text(
                self,
                size=size,
                version=version,
                chunk_size=chunk_size,
                deadline=deadline,
                document_version=document_version,
            ),
            self._sync_call_app_action,
        )

    def _app_schedule_map_indices(
        self, map_indices: Sequence[int] | None, *, deadline: float | None = None
    ) -> list[int]:
        return run_app_read(
            read_map_indices(self, map_indices, deadline=deadline),
            self._sync_call_app_action,
        )
