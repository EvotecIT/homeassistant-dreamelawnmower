"""Shared native schedule discovery and bounded document reads."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from threading import RLock
from typing import Any, Concatenate, Protocol

from .batch_device_data import decode_batch_schedule_payload
from .client_map_helpers import _app_map_entries_are_valid, _normalize_app_map_entries
from .client_schedule_tables import _DreameLawnMowerScheduleTablesMixin
from .client_settings_helpers import (
    _batch_schedule_keys,
    _dedupe_ints,
)
from .client_shared_helpers import (
    _app_action_data,
    _positive_int,
)
from .exceptions import (
    DreameLawnMowerConnectionError,
)
from .payload_utils import _json_safe
from .schedule import (
    EMPTY_SCHEDULE_VERSION,
    SCHEDULE_CHUNK_SIZE,
    decode_schedule_payload_text,
    schedule_task_summary,
)

SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS = 5.0
SCHEDULE_READ_DEADLINE_SECONDS = 10.0
SCHEDULE_READ_TIMEOUT_SECONDS = 5.0


class _ScheduleOperationOwner(Protocol):
    _schedule_operation_lock: RLock


def _serialized_schedule_operation[OwnerT: _ScheduleOperationOwner, **P, ResultT](
    method: Callable[Concatenate[OwnerT, P], ResultT],
) -> Callable[Concatenate[OwnerT, P], ResultT]:
    """Keep schedule reads and read/modify/write operations coherent."""

    @wraps(method)
    def serialized(self: OwnerT, /, *args: P.args, **kwargs: P.kwargs) -> ResultT:
        with self._schedule_operation_lock:
            return method(self, *args, **kwargs)

    return serialized


class _DreameLawnMowerScheduleReadsMixin(_DreameLawnMowerScheduleTablesMixin):
    """Own document/table discovery and bounded reads shared by schedule writes."""

    _schedule_operation_lock: RLock
    _schedule_protocols: dict[int, str]
    _schedule_document_versions: dict[int, int]
    _schedule_document_retry_versions: dict[int, int]
    _account_type: str


    @_serialized_schedule_operation
    def _sync_get_schedule_start_evidence(self) -> dict[str, Any]:
        """Never replace failed map discovery with likely-slot guesses for a start."""
        response = self._sync_call_app_action(
            {"m": "g", "t": "MAPL"},
            retry_count=0,
            timeout=5.0,
        )
        entries = _normalize_app_map_entries(response)
        if response.get("r") != 0 or not _app_map_entries_are_valid(response, entries):
            raise DreameLawnMowerConnectionError(
                "Native schedule map inventory is unknown."
            )
        created = [entry for entry in entries if entry["created"]]
        indices = [entry["idx"] for entry in created]
        payload = self._sync_get_app_schedules(
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


    @_serialized_schedule_operation
    def _sync_get_app_schedules(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
        include_current_task: bool = True,
    ) -> dict[str, Any]:
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
                task_result = self._sync_call_app_action(
                    {"m": "g", "t": "SCHDT", "d": {"t": 0}},
                    retry_count=0,
                    timeout=SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS,
                    deadline=current_task_deadline,
                )
                result["raw_current_task"] = _json_safe(task_result, max_depth=4)
                task_data = _app_action_data(task_result)
                result["current_task"] = schedule_task_summary(task_data)
            except Exception as err:  # noqa: BLE001 - optional diagnostic
                result["errors"].append({"stage": "current_task", "error": str(err)})

        schedule_started_at = time.monotonic()
        schedule_deadline = schedule_started_at + SCHEDULE_READ_DEADLINE_SECONDS
        map_discovery_deadline = schedule_deadline
        if map_indices is None:
            # Treat MAPL as another fair-budget participant so a nonresponsive
            # discovery probe cannot consume the schedule slots' whole window.
            map_discovery_deadline = min(
                schedule_deadline,
                schedule_started_at + SCHEDULE_READ_DEADLINE_SECONDS / 4,
            )
        schedule_indices = self._app_schedule_map_indices(
            map_indices,
            deadline=map_discovery_deadline,
        )
        first_pass_deadline = schedule_deadline
        if len(schedule_indices) > 1:
            now = time.monotonic()
            remaining = max(0.0, schedule_deadline - now)
            # Reserve enough of the shared window for one slower slot to make
            # meaningful progress after every slot receives a fair first pass.
            recovery_reserve = min(
                SCHEDULE_READ_TIMEOUT_SECONDS,
                remaining / 2,
            )
            first_pass_deadline = schedule_deadline - recovery_reserve
        failed_schedule_positions: list[int] = []
        for position, map_index in enumerate(schedule_indices):
            now = time.monotonic()
            remaining = max(0.0, first_pass_deadline - now)
            remaining_slots = len(schedule_indices) - position
            slot_deadline = min(
                first_pass_deadline,
                now + remaining / remaining_slots,
            )
            schedule_result, error = self._sync_get_app_schedule_slot(
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

        # A fair first pass prevents one slow slot from starving the rest. If
        # later slots return quickly, spend the unused shared budget on one
        # recovery pass so a valid early slot is not permanently limited to
        # only its initial fraction of the operation deadline.
        retry_offset = getattr(self, "_app_schedule_retry_offset", 0)
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
            retry_deadline = min(
                schedule_deadline,
                now + SCHEDULE_READ_TIMEOUT_SECONDS,
            )
            schedule_result, error = self._sync_get_app_schedule_slot(
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


    def _sync_get_app_schedule_slot(
        self,
        *,
        map_index: int,
        chunk_size: int,
        include_raw: bool,
        deadline: float,
        reserve_alternate: bool = True,
    ) -> tuple[dict[str, Any], Exception | None]:
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
                and map_index not in self._schedule_protocols
                and reserve_alternate
            ):
                now = time.monotonic()
                probe_deadline = now + max(0.0, deadline - now) / 2
            if protocol == "tables":
                try:
                    result = self._sync_read_schedule_tables(
                        map_index=map_index,
                        deadline=probe_deadline,
                        include_raw=include_raw,
                    )
                    self._schedule_protocols[map_index] = protocol
                    return result, None
                except Exception as err:  # noqa: BLE001 - optional protocol probe
                    errors.append(f"{protocol}: {err}")
            else:
                result, error = self._sync_get_document_schedule_slot(
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
                    return result, None
                errors.append(f"{protocol}: {error}")
            if time.monotonic() >= deadline:
                break
        error = DreameLawnMowerConnectionError("; ".join(errors))
        return {
            "idx": map_index,
            "label": "default" if map_index == -1 else f"map_{map_index}",
            "available": False,
            "read_status": "unknown",
            "error": str(error),
        }, error


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
        """Negotiate document generations without consuming the next slot's budget."""
        preferred = self._schedule_document_versions.get(map_index, 2)
        if not reserve_generation:
            # Give slow metadata a full recovery window, rotating that window
            # across generations after failure instead of repeatedly letting
            # an unsupported generation consume it. A remembered generation
            # already failed the first pass, so try its alternate first.
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
            result, error = self._sync_get_document_schedule_generation(
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
                return result, None
            errors.append(f"V{generation}: {error}")
        if not reserve_generation:
            self._schedule_document_retry_versions[map_index] = (
                3 if preferred == 2 else 2
            )
        error = DreameLawnMowerConnectionError(
            "; ".join(errors) or "Schedule read timed out."
        )
        return {"idx": map_index, "available": False, "error": str(error)}, error


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
        """Read metadata and chunks from one internally consistent generation."""
        schedule_result: dict[str, Any] = {
            "idx": map_index,
            "label": "default" if map_index == -1 else f"map_{map_index}",
            "available": False,
        }
        try:
            info_result = self._sync_call_app_action(
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
                return schedule_result, None

            payload_text, chunk_count, offset = self._sync_get_app_schedule_text(
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
                    "enabled_plan_count": sum(
                        1 for plan in plans if plan.get("enabled")
                    ),
                    "plans": plans,
                }
            )
            if include_raw:
                schedule_result["raw_text"] = payload_text
        except Exception as err:  # noqa: BLE001 - caller keeps probing other maps
            schedule_result["error"] = str(err)
            return schedule_result, err
        return schedule_result, None


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
        if batch_data is None:
            return {
                "source": "batch_device_data_schedule",
                "available": False,
                "current_task": None,
                "schedules": [],
                "errors": [
                    {
                        "stage": "schedule",
                        "error": "Batch device data returned no schedule payload.",
                    }
                ],
            }
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
        chunks = bytearray()
        offset = 0
        chunk_count = 0
        while offset < size:
            request_size = min(chunk_size, size - offset)
            chunk_result = self._sync_call_app_action(
                {
                    "m": "g",
                    "t": f"SCHDDV{document_version}",
                    "d": {"s": offset, "l": request_size, "v": version},
                },
                retry_count=0,
                timeout=SCHEDULE_READ_TIMEOUT_SECONDS,
                deadline=deadline,
            )
            data = _app_action_data(chunk_result)
            if not isinstance(data, Mapping) or "d" not in data:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV{document_version} returned invalid chunk "
                    f"at offset {offset}."
                )
            text = str(data.get("d") or "")
            encoded = text.encode("utf-8")
            returned_size = _positive_int(data.get("l"))
            if any(
                key in data
                and (
                    isinstance(data[key], bool) or _positive_int(data[key]) != expected
                )
                for key, expected in (("s", offset), ("v", version))
            ):
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV{document_version} returned a mismatched chunk identity."
                )
            if "l" in data and (
                isinstance(data["l"], bool) or returned_size != len(encoded)
            ):
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV{document_version} returned an invalid chunk size."
                )
            if not encoded:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV{document_version} returned empty data at offset {offset}."
                )
            if len(chunks) + len(encoded) > size:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV{document_version} returned too much data "
                    f"at offset {offset}."
                )
            chunks.extend(encoded)
            offset += returned_size if returned_size else len(encoded)
            chunk_count += 1
        return chunks.decode("utf-8"), chunk_count, offset


    def _app_schedule_map_indices(
        self,
        map_indices: Sequence[int] | None,
        *,
        deadline: float | None = None,
    ) -> list[int]:
        if map_indices is not None:
            return _dedupe_ints(map_indices)
        return _dedupe_ints([-1, *self._app_map_indices(None, deadline=deadline)])
