"""Thin wiring from the existing mission owner to observed-duration consumers."""

from __future__ import annotations

from typing import Any

from .dreame_lawn_mower_client.session_timing import ObservedMowingTimer
from .runtime_cache import runtime_mission_session_generation


def observe_mowing_time(
    coordinator: Any, snapshot: Any, mission_active: bool | None
) -> None:
    """Record successful observations after the coordinator's ordering checks."""
    timer = getattr(coordinator, "observed_mowing_timer", None)
    if timer is None:
        timer = coordinator.observed_mowing_timer = ObservedMowingTimer()
    if not getattr(snapshot, "available", False):
        timer.interrupt()
        return
    interval = getattr(coordinator, "update_interval", None)
    max_gap = max(130.0, interval.total_seconds() * 2 + 10) if interval else 130.0
    timer.observe(
        generation=runtime_mission_session_generation(
            getattr(coordinator, "runtime_telemetry_cache", None)
        )
        or 0,
        session_active=mission_active,
        mowing=getattr(snapshot, "activity", None) == "mowing",
        max_gap_seconds=max_gap,
    )


def observed_mowing_time_attributes(coordinator: Any) -> dict[str, Any]:
    """Expose the same measurement contract to mower attributes and sensors."""
    timer = getattr(coordinator, "observed_mowing_timer", None)
    return {
        "observed_mowing_time": timer.minutes if timer is not None else None,
        "observed_mowing_time_details": timer.attributes()
        if timer is not None
        else None,
    }
