"""Shared schedule read plan, driven by synchronous or native async transport.

Each yield describes one read-only RPC. The plan retains document/table probing,
fair slot budgets and rotating recovery; drivers only perform the requested I/O.
"""

from __future__ import annotations

import time
from collections.abc import Generator, Mapping, Sequence
from typing import Any, Protocol

from .client_map_helpers import _app_map_entries_are_valid, _normalize_app_map_entries
from .client_settings_helpers import _dedupe_ints
from .client_shared_helpers import _app_action_data, _positive_int
from .exceptions import DreameLawnMowerConnectionError
from .payload_utils import _json_safe
from .schedule import (
    EMPTY_SCHEDULE_VERSION,
    SCHEDULE_CHUNK_SIZE,
    decode_schedule_payload_text,
    schedule_task_summary,
)
from .schedule_document import ScheduleDocumentReader
from .schedule_read_transport import ScheduleReadRequest
from .schedule_tables import (
    combine_schedule_table_weeks,
    decode_schedule_table_task,
    decode_schedule_tables,
    schedule_table_ids,
)
from .schedule_tables import (
    schedule_table_data as _table_data,
)

SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS = 5.0
SCHEDULE_READ_DEADLINE_SECONDS = 10.0
SCHEDULE_READ_TIMEOUT_SECONDS = 5.0


class ScheduleReadState(Protocol):
    _account_type: str
    _schedule_protocols: dict[int, str]
    _schedule_document_versions: dict[int, int]
    _schedule_document_retry_versions: dict[int, int]
    _app_schedule_retry_offset: int


def read_start_evidence(
    self: ScheduleReadState,
) -> Generator[ScheduleReadRequest, Any, dict[str, Any]]:
    """Read start evidence only from a validated fresh map inventory."""
    response = yield ScheduleReadRequest(
        {"m": "g", "t": "MAPL"},
        retry_count=0,
        timeout=5.0,
    )
    entries = _normalize_app_map_entries(response)
    if (
        not isinstance(response, Mapping) or response.get("r") != 0
        or not _app_map_entries_are_valid(response, entries)
    ):
        raise DreameLawnMowerConnectionError(
            "Native schedule map inventory is unknown."
        )
    created = [entry for entry in entries if entry["created"]]
    indices = [entry["idx"] for entry in created]
    payload = yield from read_schedules(
        self,
        map_indices=[-1, *indices],
        include_current_task=False,
    )
    return {
        "map_indices": indices,
        "map_inventory_valid": True,
        "current_map_index": next(
            (entry["idx"] for entry in created if entry["current"]), None
        ),
        "schedules": payload["schedules"],
    }


def read_schedules(
    self: ScheduleReadState,
    include_raw: bool = False,
    map_indices: Sequence[int] | None = None,
    chunk_size: int = SCHEDULE_CHUNK_SIZE,
    include_current_task: bool = True,
) -> Generator[ScheduleReadRequest, Any, dict[str, Any]]:
    """Fetch and decode mower schedules through read-only app actions."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    result: dict[str, Any] = {
        "source": "app_action_schedule",
        "available": False,
        "current_task": None,
        "schedules": [],
        "errors": [],
    }
    if include_current_task:
        try:
            current_task_deadline = (
                time.monotonic() + SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS
            )
            task_result = yield ScheduleReadRequest(
                {"m": "g", "t": "SCHDT", "d": {"t": 0}},
                retry_count=0,
                timeout=SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS,
                deadline=current_task_deadline,
            )
            result["raw_current_task"] = _json_safe(task_result, max_depth=4)
            task_data = _app_action_data(task_result)
            result["current_task"] = schedule_task_summary(task_data)
        except Exception as err:
            result["errors"].append({"stage": "current_task", "error": str(err)})
    schedule_started_at = time.monotonic()
    schedule_deadline = schedule_started_at + SCHEDULE_READ_DEADLINE_SECONDS
    map_discovery_deadline = schedule_deadline
    if map_indices is None:
        map_discovery_deadline = min(
            schedule_deadline, schedule_started_at + SCHEDULE_READ_DEADLINE_SECONDS / 4
        )
    schedule_indices = yield from read_map_indices(
        self, map_indices, deadline=map_discovery_deadline
    )
    # Reserve recovery time while giving every slot a fair first probe.
    first_pass_deadline = schedule_deadline
    if len(schedule_indices) > 1:
        now = time.monotonic()
        remaining = max(0.0, schedule_deadline - now)
        recovery_reserve = min(SCHEDULE_READ_TIMEOUT_SECONDS, remaining / 2)
        first_pass_deadline = schedule_deadline - recovery_reserve
    failed_schedule_positions: list[int] = []
    for position, map_index in enumerate(schedule_indices):
        now = time.monotonic()
        remaining = max(0.0, first_pass_deadline - now)
        remaining_slots = len(schedule_indices) - position
        slot_deadline = min(first_pass_deadline, now + remaining / remaining_slots)
        schedule_result, error = yield from read_slot(
            self,
            map_index=map_index,
            chunk_size=chunk_size,
            include_raw=include_raw,
            deadline=slot_deadline,
        )
        if error is not None:
            failed_schedule_positions.append(position)
            result["errors"].append(
                {"idx": map_index, "stage": "schedule", "error": str(error)}
            )
        elif schedule_result.get("plans"):
            result["available"] = True
        result["schedules"].append(schedule_result)
    # Rotate failed slots so a slow first slot cannot always consume recovery.
    retry_offset = self._app_schedule_retry_offset
    if failed_schedule_positions:
        retry_offset %= len(failed_schedule_positions)
        failed_schedule_positions = [
            *failed_schedule_positions[retry_offset:],
            *failed_schedule_positions[:retry_offset],
        ]
        self._app_schedule_retry_offset = (retry_offset + 1) % len(
            failed_schedule_positions
        )
    for position in failed_schedule_positions:
        now = time.monotonic()
        if now >= schedule_deadline:
            break
        map_index = schedule_indices[position]
        retry_deadline = min(schedule_deadline, now + SCHEDULE_READ_TIMEOUT_SECONDS)
        schedule_result, error = yield from read_slot(
            self,
            map_index=map_index,
            chunk_size=chunk_size,
            include_raw=include_raw,
            deadline=retry_deadline,
            reserve_alternate=False,
        )
        result["schedules"][position] = schedule_result
        prior_error = next(
            (
                item
                for item in result["errors"]
                if item.get("idx") == map_index and item.get("stage") == "schedule"
            ),
            None,
        )
        if error is None:
            if prior_error is not None:
                result["errors"].remove(prior_error)
            if schedule_result.get("plans"):
                result["available"] = True
        elif prior_error is not None:
            prior_error["error"] = str(error)
    return result


def read_slot(
    self: ScheduleReadState,
    *,
    map_index: int,
    chunk_size: int,
    include_raw: bool,
    deadline: float,
    reserve_alternate: bool = True,
) -> Generator[ScheduleReadRequest, Any, tuple[dict[str, Any], Exception | None]]:
    """Select a protocol from validated replies, using brand only for order."""
    preferred = self._schedule_protocols.get(map_index)
    if preferred is None:
        preferred = "tables" if self._account_type == "mova" else "document"
    protocols = [preferred]
    if map_index >= 0:
        protocols.append("document" if preferred == "tables" else "tables")
    else:
        protocols = ["document"]
    errors: list[str] = []
    for position, protocol in enumerate(protocols):
        probe_deadline = deadline
        if (
            map_index >= 0
            and position == 0
            and (map_index not in self._schedule_protocols)
            and reserve_alternate
        ):
            now = time.monotonic()
            probe_deadline = now + max(0.0, deadline - now) / 2
        if protocol == "tables":
            try:
                result = yield from read_tables(
                    map_index=map_index,
                    deadline=probe_deadline,
                    include_raw=include_raw,
                )
                self._schedule_protocols[map_index] = protocol
                return (result, None)
            except Exception as err:
                errors.append(f"{protocol}: {err}")
        else:
            result, error = yield from read_document_slot(
                self,
                map_index=map_index,
                chunk_size=chunk_size,
                include_raw=include_raw,
                deadline=deadline,
                metadata_deadline=probe_deadline,
                reserve_generation=reserve_alternate,
            )
            if error is None:
                result["protocol"] = protocol
                result["read_status"] = "complete"
                self._schedule_protocols[map_index] = protocol
                return (result, None)
            errors.append(f"{protocol}: {error}")
        if time.monotonic() >= deadline:
            break
    error = DreameLawnMowerConnectionError("; ".join(errors))
    return (
        {
            "idx": map_index,
            "label": "default" if map_index == -1 else f"map_{map_index}",
            "available": False,
            "read_status": "unknown",
            "error": str(error),
        },
        error,
    )


def read_document_slot(
    self: ScheduleReadState,
    *,
    map_index: int,
    chunk_size: int,
    include_raw: bool,
    deadline: float,
    metadata_deadline: float | None = None,
    reserve_generation: bool = True,
) -> Generator[ScheduleReadRequest, Any, tuple[dict[str, Any], Exception | None]]:
    """Negotiate document generations without consuming the next slot's budget."""
    preferred = self._schedule_document_versions.get(map_index, 2)
    if not reserve_generation:
        recovery_generation = preferred
        if map_index in self._schedule_document_versions:
            recovery_generation = 3 if preferred == 2 else 2
        preferred = self._schedule_document_retry_versions.get(
            map_index, recovery_generation
        )
    generations = [preferred, 3 if preferred == 2 else 2]
    metadata_end = metadata_deadline if metadata_deadline is not None else deadline
    errors: list[str] = []
    for position, generation in enumerate(generations):
        now = time.monotonic()
        if now >= metadata_end:
            break
        probe_end = metadata_end
        if reserve_generation and map_index not in self._schedule_document_versions:
            probe_end = now + (metadata_end - now) / (len(generations) - position)
        result, error = yield from read_document_generation(
            self,
            map_index=map_index,
            chunk_size=chunk_size,
            include_raw=include_raw,
            deadline=deadline,
            metadata_deadline=probe_end,
            generation=generation,
        )
        if error is None:
            self._schedule_document_versions[map_index] = generation
            self._schedule_document_retry_versions.pop(map_index, None)
            result["document_version"] = generation
            return (result, None)
        errors.append(f"V{generation}: {error}")
    if not reserve_generation:
        self._schedule_document_retry_versions[map_index] = 3 if preferred == 2 else 2
    error = DreameLawnMowerConnectionError(
        "; ".join(errors) or "Schedule read timed out."
    )
    return ({"idx": map_index, "available": False, "error": str(error)}, error)


def read_document_generation(
    self: ScheduleReadState,
    *,
    map_index: int,
    chunk_size: int,
    include_raw: bool,
    deadline: float,
    metadata_deadline: float,
    generation: int,
) -> Generator[ScheduleReadRequest, Any, tuple[dict[str, Any], Exception | None]]:
    """Read metadata and chunks from one internally consistent generation."""
    schedule_result: dict[str, Any] = {
        "idx": map_index,
        "label": "default" if map_index == -1 else f"map_{map_index}",
        "available": False,
    }
    try:
        info_result = yield ScheduleReadRequest(
            {"m": "g", "t": f"SCHDIV{generation}", "d": {"i": map_index}},
            retry_count=0,
            timeout=SCHEDULE_READ_TIMEOUT_SECONDS,
            deadline=metadata_deadline,
        )
        schedule_result["raw_info"] = _json_safe(info_result, max_depth=4)
        info = _app_action_data(info_result)
        if not isinstance(info, Mapping) or "l" not in info or "v" not in info:
            raise DreameLawnMowerConnectionError(
                f"SCHDIV{generation} returned invalid schedule metadata."
            )
        size = _positive_int(info.get("l"))
        version = _positive_int(info.get("v"))
        if (
            size is None
            or version is None
            or version > EMPTY_SCHEDULE_VERSION
            or isinstance(info.get("l"), bool)
            or isinstance(info.get("v"), bool)
            or ("i" in info and info["i"] != map_index)
        ):
            raise DreameLawnMowerConnectionError(
                f"SCHDIV{generation} returned invalid schedule identity, "
                "size, or version."
            )
        schedule_result["size"] = size
        schedule_result["version"] = version
        if not size or version is None or version == EMPTY_SCHEDULE_VERSION:
            schedule_result["plans"] = []
            return (schedule_result, None)
        payload_text, chunk_count, offset = yield from read_document_text(
            self,
            size=size,
            version=version,
            chunk_size=chunk_size,
            deadline=deadline,
            document_version=generation,
        )
        plans = decode_schedule_payload_text(payload_text)
        schedule_result.update(
            {
                "available": bool(plans),
                "chunk_count": chunk_count,
                "downloaded_size": offset,
                "plan_count": len(plans),
                "enabled_plan_count": sum(1 for plan in plans if plan.get("enabled")),
                "plans": plans,
            }
        )
        if include_raw:
            schedule_result["raw_text"] = payload_text
    except Exception as err:
        schedule_result["error"] = str(err)
        return (schedule_result, err)
    return (schedule_result, None)


def read_document_text(
    self: ScheduleReadState,
    *,
    size: int,
    version: int,
    chunk_size: int = SCHEDULE_CHUNK_SIZE,
    deadline: float | None = None,
    document_version: int = 2,
) -> Generator[ScheduleReadRequest, Any, tuple[str, int, int]]:
    reader = ScheduleDocumentReader(
        size=size,
        version=version,
        chunk_size=chunk_size,
        document_version=document_version,
    )
    while not reader.complete:
        response = yield ScheduleReadRequest(
            reader.request(),
            retry_count=0,
            timeout=SCHEDULE_READ_TIMEOUT_SECONDS,
            deadline=deadline,
        )
        reader.append_response(response)
    return reader.result()


def read_tables(
    *,
    map_index: int,
    deadline: float,
    include_raw: bool = False,
    include_tasks: bool = True,
) -> Generator[ScheduleReadRequest, Any, dict[str, Any]]:
    response = yield ScheduleReadRequest(
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
                    task_response = yield ScheduleReadRequest(
                        {"m": "g", "t": "SCHDC", "d": [plan["table_id"], task_id]},
                        retry_count=0,
                        timeout=5.0,
                        deadline=deadline,
                    )
                    task_weeks.append(
                        decode_schedule_table_task(
                            _table_data(task_response), task_id=task_id
                        )
                    )
                except Exception as err:
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


def read_map_indices(
    self: ScheduleReadState,
    map_indices: Sequence[int] | None,
    *,
    deadline: float | None = None,
) -> Generator[ScheduleReadRequest, Any, list[int]]:
    if map_indices is not None:
        return _dedupe_ints(map_indices)
    try:
        response = yield ScheduleReadRequest(
            {"m": "g", "t": "MAPL"},
            deadline=deadline,
        )
        entries = _normalize_app_map_entries(response)
        detected = (
            [entry["idx"] for entry in entries]
            if _app_map_entries_are_valid(response, entries)
            else [0, 1]
        )
    except Exception:  # noqa: BLE001 - retain likely-slot diagnostic fallback
        detected = [0, 1]
    return _dedupe_ints([-1, *detected])
