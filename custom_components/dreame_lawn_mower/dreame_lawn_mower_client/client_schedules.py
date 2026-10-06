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
from .client_schedule_write_transport import run_schedule_write
from .client_settings_helpers import (
    _batch_schedule_keys,
)
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
)
from .schedule import (
    SCHEDULE_CHUNK_SIZE,
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
from .schedule_write_plan import plan_schedule_enabled, plan_schedule_upload

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
        return run_schedule_write(
            self,
            plan_schedule_enabled(
                map_index, plan_id, enabled, execute, confirm_write
            ),
        )

    @_serialized_schedule_operation
    def _sync_plan_app_schedule_upload(
        self,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        execute: bool = False,
        confirm_write: bool = False,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        return run_schedule_write(
            self,
            plan_schedule_upload(
                map_index, plans, execute, confirm_write, chunk_size
            ),
        )

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
