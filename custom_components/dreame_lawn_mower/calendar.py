"""Read-only schedule calendar for Dreame lawn mower."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity

_LOGGER = logging.getLogger(__name__)

SCHEDULE_LOOKAHEAD_DAYS = 14


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower schedule calendar."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DreameLawnMowerScheduleCalendar(coordinator)])


class DreameLawnMowerScheduleCalendar(DreameLawnMowerEntity, CalendarEntity):
    """Read-only calendar built from mower-native app schedules."""

    _attr_name = "Schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_schedule_calendar"
        self._cached_event: CalendarEvent | None = None
        self._cached_event_count: int | None = None
        self._cached_selection: dict[str, Any] | None = None
        self._last_error: str | None = None

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next schedule event."""
        return self._cached_event

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return cached schedule diagnostics for the calendar entity."""
        return schedule_calendar_attributes(
            selection=self._cached_selection,
            event_count=self._cached_event_count,
            last_error=self._last_error,
        )

    @property
    def available(self) -> bool:
        """Return whether the calendar can currently serve schedule data."""
        return self.coordinator.data is not None and self._last_error is None

    async def async_update(self) -> None:
        """Refresh the cached upcoming schedule event."""
        now = dt_util.now()
        events = await self.async_get_events(
            self.hass,
            now,
            now + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS),
        )
        self._cached_event = events[0] if events else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return mower schedule events in the requested time window."""
        try:
            payload = await self.coordinator.client.async_get_app_schedules()
        except Exception as err:  # noqa: BLE001 - calendar should stay read-only
            self._last_error = str(err)
            self._cached_event_count = 0
            self._cached_selection = None
            _LOGGER.debug("Failed to fetch mower schedules: %s", err)
            return []
        self._last_error = None
        self._cached_selection = schedule_calendar_selection(payload)
        events = schedule_calendar_events(
            payload,
            start_date,
            end_date,
            mower_name=self._descriptor.name,
        )
        self._cached_event_count = len(events)
        self._cached_event = events[0] if events else None
        return events


def schedule_calendar_events(
    payload: Mapping[str, Any],
    start_date: datetime,
    end_date: datetime,
    *,
    include_all_schedules: bool = False,
    mower_name: str | None = None,
) -> list[CalendarEvent]:
    """Build Home Assistant calendar events from decoded app schedule data."""
    local_start = _as_local(start_date)
    local_end = _as_local(end_date)
    if local_end <= local_start:
        return []

    events: list[CalendarEvent] = []
    active_version = (
        None if include_all_schedules else _active_schedule_version(payload)
    )
    for day in _candidate_days(local_start.date(), local_end.date()):
        week_day = _schedule_week_day(day)
        for schedule in payload.get("schedules") or []:
            if not isinstance(schedule, Mapping):
                continue
            if active_version is not None and schedule.get("version") != active_version:
                continue
            map_index = schedule.get("idx")
            map_label = _schedule_label(schedule)
            for plan in schedule.get("plans") or []:
                if not isinstance(plan, Mapping) or not plan.get("enabled"):
                    continue
                for week in plan.get("weeks") or []:
                    if (
                        not isinstance(week, Mapping)
                        or week.get("week_day") != week_day
                    ):
                        continue
                    events.extend(
                        _task_events(
                            day=day,
                            week=week,
                            plan=plan,
                            map_index=map_index,
                            map_label=map_label,
                            local_start=local_start,
                            local_end=local_end,
                            mower_name=mower_name,
                        )
                    )
    return sorted(events, key=lambda event: event.start_datetime_local)


def schedule_calendar_selection(
    payload: Mapping[str, Any],
    *,
    include_all_schedules: bool = False,
) -> dict[str, Any]:
    """Return why schedule slots are included or hidden by the calendar."""
    schedules = [
        schedule
        for schedule in payload.get("schedules") or []
        if isinstance(schedule, Mapping)
    ]
    active_version = (
        None if include_all_schedules else _active_schedule_version(payload)
    )
    included_schedules: list[dict[str, Any]] = []
    hidden_schedules: list[dict[str, Any]] = []

    for schedule in schedules:
        target = included_schedules
        if active_version is not None and schedule.get("version") != active_version:
            target = hidden_schedules
        target.append(_schedule_selection_entry(schedule))

    return {
        "mode": "all_schedules" if include_all_schedules else "active_schedule",
        "active_version": active_version,
        "active_version_filter_applied": bool(
            active_version is not None and not include_all_schedules
        ),
        "included_schedule_count": len(included_schedules),
        "hidden_schedule_count": len(hidden_schedules),
        "included_schedules": included_schedules,
        "hidden_schedules": hidden_schedules,
    }


def schedule_calendar_attributes(
    *,
    selection: Mapping[str, Any] | None,
    event_count: int | None,
    last_error: str | None,
) -> dict[str, Any]:
    """Return compact Home Assistant attributes for schedule diagnostics."""
    attributes: dict[str, Any] = {}
    if event_count is not None:
        attributes["event_count"] = event_count
    if selection:
        attributes["schedule_selection"] = dict(selection)
    if last_error:
        attributes["last_error"] = last_error
    return attributes


def _active_schedule_version(payload: Mapping[str, Any]) -> int | None:
    current_task = payload.get("current_task")
    if not isinstance(current_task, Mapping):
        return None
    try:
        return int(current_task["version"])
    except (KeyError, TypeError, ValueError):
        return None


def _schedule_selection_entry(schedule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": schedule.get("idx"),
        "label": _schedule_label(schedule),
        "version": schedule.get("version"),
        "enabled_plan_count": _enabled_plan_count(schedule),
    }


def _enabled_plan_count(schedule: Mapping[str, Any]) -> int:
    value = schedule.get("enabled_plan_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return sum(
        1
        for plan in schedule.get("plans") or []
        if isinstance(plan, Mapping) and plan.get("enabled")
    )


def _task_events(
    *,
    day: date,
    week: Mapping[str, Any],
    plan: Mapping[str, Any],
    map_index: Any,
    map_label: str,
    local_start: datetime,
    local_end: datetime,
    mower_name: str | None,
) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    for task_index, task in enumerate(week.get("tasks") or []):
        if not isinstance(task, Mapping):
            continue
        start_minute = _schedule_minute(task.get("start"))
        end_minute = _schedule_minute(task.get("end"))
        if start_minute is None or end_minute is None:
            continue
        event_start = _combine_schedule_time(day, start_minute)
        event_end = _combine_schedule_time(day, end_minute)
        if event_end <= event_start:
            event_end += timedelta(days=1)
        if event_end <= local_start or event_start >= local_end:
            continue
        type_name = str(task.get("type_name") or "mowing").replace("_", " ")
        summary = f"{type_name.capitalize()} ({map_label} plan {plan.get('plan_id')})"
        description = _event_description(
            mower_name=mower_name,
            map_label=map_label,
            plan=plan,
            task=task,
        )
        events.append(
            CalendarEvent(
                start=event_start,
                end=event_end,
                summary=summary,
                description=description,
                uid=(
                    f"dreame-mower-{map_index}-{plan.get('plan_id')}-"
                    f"{day.isoformat()}-{task_index}"
                ),
            )
        )
    return events


def _event_description(
    *,
    mower_name: str | None,
    map_label: str,
    plan: Mapping[str, Any],
    task: Mapping[str, Any],
) -> str:
    lines = [
        f"Mower: {mower_name or 'Dreame lawn mower'}",
        f"Schedule: {map_label}",
        f"Plan: {plan.get('plan_id')}",
        f"Task: {str(task.get('type_name') or 'mowing').replace('_', ' ')}",
    ]
    if task.get("cyclic"):
        lines.append("Cyclic: yes")
    if task.get("regions"):
        lines.append(f"Regions: {task.get('regions')}")
    return "\n".join(lines)


def _candidate_days(start_day: date, end_day: date) -> list[date]:
    first_day = start_day - timedelta(days=1)
    day_count = (end_day - first_day).days + 1
    return [first_day + timedelta(days=offset) for offset in range(day_count)]


def _schedule_week_day(value: date) -> int:
    return (value.weekday() + 1) % 7


def _schedule_label(schedule: Mapping[str, Any]) -> str:
    idx = schedule.get("idx")
    if idx == -1:
        return "default schedule"
    return f"map {idx}"


def _schedule_minute(value: Any) -> int | None:
    try:
        minute = int(value)
    except (TypeError, ValueError):
        return None
    if minute < 0 or minute >= 24 * 60:
        return None
    return minute


def _combine_schedule_time(value: date, minute: int) -> datetime:
    return datetime.combine(
        value,
        time(minute // 60, minute % 60),
        tzinfo=dt_util.DEFAULT_TIME_ZONE,
    )


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_local(value)
