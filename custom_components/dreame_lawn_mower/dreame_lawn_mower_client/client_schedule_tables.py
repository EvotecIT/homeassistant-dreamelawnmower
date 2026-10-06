"""Table schedule transport and writes with authoritative readback."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .app_read_transport import run_app_read
from .client_schedule_write_transport import run_schedule_write
from .schedule_read_plan import read_tables
from .schedule_write_plan import plan_table_enabled


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
        return run_app_read(
            read_tables(
                map_index=map_index,
                deadline=deadline,
                include_raw=include_raw,
                include_tasks=include_tasks,
            ),
            self._sync_call_app_action,
        )

    def _sync_set_schedule_table_enabled(
        self,
        *,
        schedule: Mapping[str, Any],
        plan_id: int,
        enabled: bool,
        execute: bool,
    ) -> dict[str, Any]:
        return run_schedule_write(
            self,
            plan_table_enabled(
                schedule=schedule, plan_id=plan_id, enabled=enabled, execute=execute
            ),
        )
