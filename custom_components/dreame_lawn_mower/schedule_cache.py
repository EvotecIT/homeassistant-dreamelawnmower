"""Schedule cache validation and merge policy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


def schedule_entry_has_usable_data(value: Any) -> bool:
    """Return whether one slot was read authoritatively, including empty plans."""
    if not isinstance(value, Mapping) or value.get("error"):
        return False
    plans = value.get("plans")
    return isinstance(plans, Sequence) and not isinstance(
        plans,
        str | bytes | bytearray,
    )


def schedule_payload_has_usable_data(
    payload: Mapping[str, Any] | None,
) -> bool:
    """Return whether at least one authoritative schedule slot is cached."""
    schedules = payload.get("schedules") if isinstance(payload, Mapping) else None
    return bool(
        isinstance(schedules, Sequence)
        and not isinstance(schedules, str | bytes | bytearray)
        and any(schedule_entry_has_usable_data(schedule) for schedule in schedules)
    )


def has_complete_schedule_cache(
    payload: Mapping[str, Any] | None,
    known_map_indices: Sequence[int],
) -> bool:
    """Return whether default and every known physical map are usable."""
    schedules = payload.get("schedules") if isinstance(payload, Mapping) else None
    if not isinstance(schedules, Sequence) or isinstance(
        schedules,
        str | bytes | bytearray,
    ):
        return False
    schedule_by_index = {
        schedule.get("idx"): schedule
        for schedule in schedules
        if isinstance(schedule, Mapping)
    }
    if any(
        isinstance(schedule, Mapping) and schedule.get("idx") is None
        for schedule in schedules
    ):
        return False
    return all(
        schedule_entry_has_usable_data(schedule_by_index.get(index))
        for index in {-1, *known_map_indices}
    )


def invalidate_schedule_slot(
    existing: Mapping[str, Any] | None,
    map_index: int,
) -> dict[str, Any] | None:
    """Remove one confirmed-stale writable slot and its derived metadata."""
    if not isinstance(existing, Mapping):
        return None
    schedules = existing.get("schedules")
    if not isinstance(schedules, Sequence) or isinstance(
        schedules,
        str | bytes | bytearray,
    ):
        return dict(existing)

    invalidated_versions = {
        schedule.get("version")
        for schedule in schedules
        if isinstance(schedule, Mapping)
        and schedule.get("idx") == map_index
        and isinstance(schedule.get("version"), int)
        and not isinstance(schedule.get("version"), bool)
    }
    retained = [
        schedule
        for schedule in schedules
        if not (
            isinstance(schedule, Mapping)
            and (
                schedule.get("idx") == map_index
                or (
                    schedule.get("idx") is None
                    and isinstance(schedule.get("version"), int)
                    and not isinstance(schedule.get("version"), bool)
                    and schedule.get("version") in invalidated_versions
                )
            )
        )
    ]
    normalized = dict(existing)
    normalized["schedules"] = retained
    cached_active_version = normalized.get("active_schedule_version")
    if (
        isinstance(cached_active_version, int)
        and not isinstance(cached_active_version, bool)
        and cached_active_version in invalidated_versions
    ):
        normalized.pop("active_schedule_version", None)
        current_task = normalized.get("current_task")
        if (
            isinstance(current_task, Mapping)
            and current_task.get("version") == cached_active_version
        ):
            normalized.pop("current_task", None)
    normalized["available"] = any(
        isinstance(schedule, Mapping) and bool(schedule.get("available"))
        for schedule in retained
    )
    return normalized


def merge_app_schedule_payload(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    *,
    expected_indices: Sequence[int],
) -> dict[str, Any]:
    """Merge successful action slots while retaining cached failed slots."""
    if not isinstance(existing, Mapping):
        return dict(incoming)

    normalized = dict(existing)
    incoming_schedules = incoming.get("schedules")
    existing_schedules = existing.get("schedules")
    if not isinstance(incoming_schedules, Sequence) or isinstance(
        incoming_schedules,
        str | bytes | bytearray,
    ):
        return normalized
    if not isinstance(existing_schedules, Sequence) or isinstance(
        existing_schedules,
        str | bytes | bytearray,
    ):
        return dict(incoming)

    merged = list(existing_schedules)
    positions = {
        schedule.get("idx"): index
        for index, schedule in enumerate(merged)
        if isinstance(schedule, Mapping)
    }
    for schedule in incoming_schedules:
        if not isinstance(schedule, Mapping):
            continue
        index = schedule.get("idx")
        position = positions.get(index)
        if position is None:
            positions[index] = len(merged)
            merged.append(schedule)
        elif schedule_entry_has_usable_data(schedule):
            merged[position] = schedule

    discovered_versions = {
        schedule.get("version")
        for schedule in incoming_schedules
        if schedule_entry_has_usable_data(schedule)
        and isinstance(schedule.get("idx"), int)
        and not isinstance(schedule.get("idx"), bool)
        and isinstance(schedule.get("version"), int)
        and not isinstance(schedule.get("version"), bool)
    }
    usable_incoming_by_index = {
        schedule.get("idx"): schedule
        for schedule in incoming_schedules
        if schedule_entry_has_usable_data(schedule)
        and isinstance(schedule.get("idx"), int)
        and not isinstance(schedule.get("idx"), bool)
    }
    if discovered_versions:
        merged = [
            schedule
            for schedule in merged
            if not (
                isinstance(schedule, Mapping)
                and schedule.get("idx") is None
                and schedule.get("version") in discovered_versions
            )
        ]

    cached_active_version = existing.get("active_schedule_version")
    cached_active_indices = {
        schedule.get("idx")
        for schedule in existing_schedules
        if isinstance(schedule, Mapping)
        and schedule.get("version") == cached_active_version
        and isinstance(schedule.get("idx"), int)
        and not isinstance(schedule.get("idx"), bool)
    }
    complete_refresh = set(expected_indices).issubset(usable_incoming_by_index)
    if complete_refresh:
        expected_index_set = set(expected_indices)
        merged = [
            schedule
            for schedule in merged
            if not (
                isinstance(schedule, Mapping)
                and isinstance(schedule.get("idx"), int)
                and not isinstance(schedule.get("idx"), bool)
                and schedule.get("idx") not in expected_index_set
            )
        ]
    active_version_invalidated = (
        isinstance(cached_active_version, int)
        and not isinstance(cached_active_version, bool)
        and cached_active_version not in discovered_versions
        and incoming.get("current_task") is None
        and (
            complete_refresh
            or (
                bool(cached_active_indices)
                and cached_active_indices.issubset(usable_incoming_by_index)
            )
        )
    )
    if active_version_invalidated:
        merged = [
            schedule
            for schedule in merged
            if not (
                isinstance(schedule, Mapping)
                and schedule.get("idx") is None
                and schedule.get("version") == cached_active_version
            )
        ]
        normalized.pop("active_schedule_version", None)
        current_task = normalized.get("current_task")
        if (
            isinstance(current_task, Mapping)
            and current_task.get("version") == cached_active_version
        ):
            normalized.pop("current_task", None)

    normalized.update(
        {
            key: value
            for key, value in incoming.items()
            if key not in {"schedules", "current_task", "active_schedule_version"}
        }
    )
    normalized["schedules"] = merged
    if incoming.get("current_task") is not None:
        normalized["current_task"] = incoming["current_task"]
    normalized["available"] = any(
        isinstance(schedule, Mapping) and bool(schedule.get("available"))
        for schedule in merged
    )
    return normalized


def merge_batch_schedule_payload(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
    *,
    captured_at: datetime,
    allow_unknown_slot: bool,
) -> dict[str, Any] | None:
    """Merge one effective batch schedule without guessing its writable slot."""
    schedules = incoming.get("schedules")
    errors = incoming.get("errors")
    if (
        not isinstance(schedules, Sequence)
        or isinstance(schedules, str | bytes | bytearray)
        or len(schedules) != 1
        or errors
        or not isinstance(schedules[0], Mapping)
    ):
        return None

    batch_schedule = dict(schedules[0])
    batch_version = batch_schedule.get("version")
    if not isinstance(batch_version, int) or isinstance(batch_version, bool):
        return None
    existing_schedules = existing.get("schedules")
    if not isinstance(existing_schedules, Sequence) or isinstance(
        existing_schedules,
        str | bytes | bytearray,
    ):
        return None

    matching_schedules = [
        schedule
        for schedule in existing_schedules
        if isinstance(schedule, Mapping)
        and schedule.get("version") == batch_version
    ]
    matching_schedule = next(
        (
            schedule
            for schedule in matching_schedules
            if isinstance(schedule.get("idx"), int)
            and not isinstance(schedule.get("idx"), bool)
        ),
        matching_schedules[0] if matching_schedules else None,
    )
    if matching_schedule is None and not allow_unknown_slot:
        return None
    if matching_schedule is not None:
        for key in ("idx", "label", "name"):
            if key in matching_schedule:
                batch_schedule[key] = matching_schedule[key]
    else:
        batch_schedule["idx"] = None
        batch_schedule["label"] = "active_schedule"
        batch_schedule["writable"] = False

    normalized = dict(existing)
    normalized["schedules"] = [
        (
            batch_schedule
            if isinstance(schedule, Mapping) and schedule is matching_schedule
            else schedule
        )
        for schedule in existing_schedules
        if not (
            matching_schedule is not None
            and isinstance(matching_schedule.get("idx"), int)
            and not isinstance(matching_schedule.get("idx"), bool)
            and isinstance(schedule, Mapping)
            and schedule is not matching_schedule
            and schedule.get("idx") is None
            and schedule.get("version") == batch_version
        )
    ]
    if matching_schedule is None:
        normalized["schedules"] = [
            schedule
            for schedule in normalized["schedules"]
            if not (
                isinstance(schedule, Mapping)
                and schedule.get("idx") is None
            )
        ]
        normalized["schedules"].append(batch_schedule)
    normalized["available"] = any(
        isinstance(schedule, Mapping) and bool(schedule.get("available"))
        for schedule in normalized["schedules"]
    )
    normalized["active_schedule_version"] = batch_version
    if incoming.get("current_task") is not None:
        normalized["current_task"] = incoming["current_task"]
    normalized["captured_at"] = captured_at.isoformat()
    normalized["source"] = "app_action_schedule_with_batch_refresh"
    return normalized
