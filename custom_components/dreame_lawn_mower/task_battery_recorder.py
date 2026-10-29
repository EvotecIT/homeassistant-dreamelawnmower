"""Optional battery backfill through Home Assistant's supported recorder API."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Any

from homeassistant.core import State
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .dreame_lawn_mower_client.task_history import TaskDetail
from .dreame_lawn_mower_client.task_observations import (
    BatterySample,
    battery_observation,
)


async def async_recorder_battery(
    hass: Any, coordinator: Any, task: TaskDetail
) -> dict[str, Any] | None:
    """Backfill only this mower's exact interval, never infer past settings.

    Recorder samples do not prove polling coverage between changes. They are
    useful for the curve and conservative observed drops, and always labelled
    as partial recorder history. No SQL or recorder database internals are used.
    """
    if "recorder" not in getattr(getattr(hass, "config", None), "components", set()):
        return None
    start, end = task.started_at, task.ended_at
    if start is None or end is None or not 0 < end - start <= 7 * 86400:
        return None
    registry = er.async_get(hass)
    unique_id = coordinator.client.descriptor.unique_id
    battery_id = registry.async_get_entity_id("sensor", DOMAIN, f"{unique_id}_battery")
    mower_id = registry.async_get_entity_id("lawn_mower", DOMAIN, f"{unique_id}_mower")
    if battery_id is None or mower_id is None:
        return None
    # Lazy imports keep recorder optional, including HA installations that
    # deliberately exclude the integration's battery entity from recording.
    # HA 2025.1 exposes this public helper here; newer stubs omit its re-export.
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.components.recorder.history import get_significant_states

    history = await get_instance(hass).async_add_executor_job(
        partial(
            get_significant_states,
            hass,
            datetime.fromtimestamp(start, UTC),
            datetime.fromtimestamp(end, UTC),
            [battery_id, mower_id],
            include_start_time_state=True,
            significant_changes_only=False,
            minimal_response=False,
            no_attributes=True,
        )
    )
    states = [(state, "battery") for state in history.get(battery_id, [])]
    states += [(state, "activity") for state in history.get(mower_id, [])]
    ordered = sorted(
        ((state, kind) for state, kind in states if isinstance(state, State)),
        key=lambda item: (item[0].last_updated, item[1] == "battery"),
    )
    activity: str | None = None
    interrupted = False
    samples = []
    for state, kind in ordered[:10_000]:
        if kind == "activity":
            activity = state.state
            interrupted = interrupted or activity not in {
                "mowing",
                "returning",
                "paused",
            }
            continue
        at = int(state.last_updated.timestamp())
        if not start <= at <= end:
            continue
        try:
            level = int(state.state)
        except (TypeError, ValueError):
            interrupted = True
            continue
        if 0 <= level <= 100:
            samples.append(
                BatterySample(
                    at,
                    level,
                    activity == "docked",
                    activity in {"mowing", "returning", "paused"},
                    not interrupted,
                )
            )
            interrupted = False
        else:
            interrupted = True
    if not samples:
        return None
    result = battery_observation(
        samples[:500],
        start=start,
        end=end,
        area=task.area_sqm,
        source="recorder",
    )
    result.update(
        coverage="partial",
        settings={},
        settings_observed_at=None,
        settings_timing="unavailable",
    )
    return result
