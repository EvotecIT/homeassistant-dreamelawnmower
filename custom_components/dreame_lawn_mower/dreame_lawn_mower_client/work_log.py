"""Mower-native lifetime work-log totals."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .exceptions import DreameLawnMowerConnectionError

WORK_LOG_TOTALS_REQUEST = {"m": "g", "t": "MIHIS"}


@dataclass(frozen=True, slots=True)
class DreameLawnMowerWorkLogTotals:
    """Lifetime totals reported by the mower's MIHIS action."""

    total_mowed_area_sqm: float
    total_mowing_time_minutes: int
    total_mowing_sessions: int
    history_start_epoch: int | None = None

    def as_dict(self) -> dict[str, int | float | None]:
        """Return the aggregate values as a plain mapping."""
        return {
            "total_mowed_area_sqm": self.total_mowed_area_sqm,
            "total_mowing_time_minutes": self.total_mowing_time_minutes,
            "total_mowing_sessions": self.total_mowing_sessions,
            "history_start_epoch": self.history_start_epoch,
        }


def work_log_totals_from_app_data(
    response: Mapping[str, Any],
) -> DreameLawnMowerWorkLogTotals:
    """Decode a successful MIHIS response into typed lifetime totals."""
    if response.get("r") != 0:
        raise DreameLawnMowerConnectionError("MIHIS returned an unsuccessful result.")
    data = response.get("d")
    if not isinstance(data, Mapping):
        raise DreameLawnMowerConnectionError("MIHIS returned no totals data.")

    area = _non_negative_number(data.get("area"), field="area")
    mowing_time = _non_negative_integer(data.get("time"), field="time")
    sessions = _non_negative_integer(data.get("count"), field="count")
    start = data.get("start")
    history_start = (
        None if start is None else _non_negative_integer(start, field="start")
    )
    return DreameLawnMowerWorkLogTotals(
        total_mowed_area_sqm=area,
        total_mowing_time_minutes=mowing_time,
        total_mowing_sessions=sessions,
        history_start_epoch=history_start,
    )


def _non_negative_number(value: Any, *, field: str) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise DreameLawnMowerConnectionError(
            f"MIHIS returned an invalid {field} total."
        )
    return float(value)


def _non_negative_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DreameLawnMowerConnectionError(
            f"MIHIS returned an invalid {field} total."
        )
    return value
