"""Synchronous entrypoint for qualified native schedule start-time edits."""

from __future__ import annotations

from typing import Any

from .client_schedule_write_transport import run_schedule_write
from .schedule_edit_plan import plan_schedule_start_time


class _DreameLawnMowerScheduleEditsMixin:
    """Retain legacy transaction ownership while sharing native edit policy."""

    def _sync_set_app_schedule_task_start_time(
        self, map_index, plan_id, week_day, task_index, start, execute, confirm_write
    ) -> dict[str, Any]:
        with self._schedule_operation_lock:
            return run_schedule_write(self, plan_schedule_start_time(
                self._descriptor.model, map_index, plan_id, week_day, task_index,
                start, execute, confirm_write,
            ))
