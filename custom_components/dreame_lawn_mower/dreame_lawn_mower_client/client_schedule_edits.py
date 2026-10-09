"""Synchronous entrypoint for qualified native schedule start-time edits."""

from __future__ import annotations

from threading import RLock
from typing import Any

from .client_schedule_write_transport import run_schedule_write
from .client_transport import _DreameLawnMowerClientTransport
from .schedule_edit_plan import plan_schedule_start_time


class _DreameLawnMowerScheduleEditsMixin(_DreameLawnMowerClientTransport):
    """Retain legacy transaction ownership while sharing native edit policy."""

    _schedule_operation_lock: RLock

    def _sync_set_app_schedule_task_start_time(
        self, map_index: int, plan_id: int, week_day: int, task_index: int,
        start: int, execute: bool, confirm_write: bool,
    ) -> dict[str, Any]:
        with self._schedule_operation_lock:
            return run_schedule_write(self, plan_schedule_start_time(
                self._descriptor.model, map_index, plan_id, week_day, task_index,
                start, execute, confirm_write,
            ))
