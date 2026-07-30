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
    *,
    schedule_version: int | None = None,
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
    if isinstance(schedule_version, int) and not isinstance(schedule_version, bool):
        invalidated_versions.add(schedule_version)
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
    cached_active_index = normalized.get("active_schedule_index")
    active_index_is_valid = isinstance(cached_active_index, int) and not isinstance(
        cached_active_index,
        bool,
    )
    active_slot_invalidated = (
        cached_active_index == map_index
        if active_index_is_valid
        else (
            isinstance(cached_active_version, int)
            and not isinstance(cached_active_version, bool)
            and cached_active_version in invalidated_versions
        )
    )
    if active_slot_invalidated:
        normalized.pop("active_schedule_version", None)
        normalized.pop("active_schedule_index", None)
        normalized["active_selection_available"] = False
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

    usable_incoming_by_index = {
        schedule.get("idx"): schedule
        for schedule in incoming_schedules
        if schedule_entry_has_usable_data(schedule)
        and isinstance(schedule.get("idx"), int)
        and not isinstance(schedule.get("idx"), bool)
    }
    discovered_versions = {
        schedule.get("version")
        for schedule in usable_incoming_by_index.values()
        if isinstance(schedule.get("version"), int)
        and not isinstance(schedule.get("version"), bool)
    }

    cached_active_version = existing.get("active_schedule_version")
    cached_active_index = existing.get("active_schedule_index")
    cached_active_index_is_valid = isinstance(
        cached_active_index,
        int,
    ) and not isinstance(cached_active_index, bool)
    expected_index_set = set(expected_indices)
    complete_refresh = expected_index_set.issubset(usable_incoming_by_index)
    incoming_indices_by_version = {
        version: {
            index
            for index, schedule in usable_incoming_by_index.items()
            if schedule.get("version") == version
        }
        for version in discovered_versions
    }
    resolved_fallback_versions = {
        version
        for version, indices in incoming_indices_by_version.items()
        if (
            cached_active_index_is_valid
            and cached_active_index in indices
        )
        or (complete_refresh and len(indices) == 1)
    }
    active_version_indices = incoming_indices_by_version.get(
        cached_active_version,
        set(),
    )
    if cached_active_index_is_valid:
        resolved_active_index = (
            cached_active_index
            if cached_active_index in active_version_indices
            else None
        )
    else:
        resolved_active_index = (
            next(iter(active_version_indices))
            if complete_refresh and len(active_version_indices) == 1
            else None
        )
    if resolved_fallback_versions:
        merged = [
            schedule
            for schedule in merged
            if not (
                isinstance(schedule, Mapping)
                and schedule.get("idx") is None
                and schedule.get("version") in resolved_fallback_versions
            )
        ]

    cached_active_indices = {
        schedule.get("idx")
        for schedule in existing_schedules
        if isinstance(schedule, Mapping)
        and schedule.get("version") == cached_active_version
        and isinstance(schedule.get("idx"), int)
        and not isinstance(schedule.get("idx"), bool)
    }
    ambiguous_active_fallback = (
        not cached_active_index_is_valid
        and cached_active_version in discovered_versions
        and cached_active_version not in resolved_fallback_versions
        and any(
            isinstance(schedule, Mapping)
            and schedule.get("idx") is None
            and schedule.get("version") == cached_active_version
            for schedule in existing_schedules
        )
    )
    if complete_refresh:
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
    if cached_active_index_is_valid:
        refreshed_active_slot = usable_incoming_by_index.get(cached_active_index)
        active_version_invalidated = incoming.get("current_task") is None and (
            (
                refreshed_active_slot is not None
                and refreshed_active_slot.get("version") != cached_active_version
            )
            or (
                complete_refresh
                and cached_active_index not in expected_index_set
            )
        )
    else:
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
        normalized.pop("active_schedule_index", None)
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
        normalized["active_selection_available"] = True
    elif resolved_active_index is not None:
        normalized["active_schedule_index"] = resolved_active_index
        normalized["active_selection_available"] = True
    elif ambiguous_active_fallback:
        normalized["active_selection_available"] = False
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
    allowed_hint_indices: Sequence[int] = (),
    preserve_indices: Sequence[int] = (),
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
    hinted_index = batch_schedule.get("idx")
    hinted_index_is_valid = isinstance(hinted_index, int) and not isinstance(
        hinted_index,
        bool,
    )
    hinted_index_is_allowed = (
        hinted_index_is_valid and hinted_index in set(allowed_hint_indices)
    )
    numeric_matching_schedules = [
        schedule
        for schedule in matching_schedules
        if isinstance(schedule.get("idx"), int)
        and not isinstance(schedule.get("idx"), bool)
    ]
    if hinted_index_is_valid:
        matching_schedule = next(
            (
                schedule
                for schedule in numeric_matching_schedules
                if schedule.get("idx") == hinted_index
            ),
            None,
        )
        if matching_schedule is None and len(numeric_matching_schedules) == 1:
            matching_schedule = numeric_matching_schedules[0]
        if matching_schedule is None and hinted_index_is_allowed:
            matching_schedule = next(
                (
                    schedule
                    for schedule in existing_schedules
                    if isinstance(schedule, Mapping)
                    and schedule.get("idx") == hinted_index
                ),
                None,
            )
    else:
        matching_schedule = (
            numeric_matching_schedules[0]
            if len(numeric_matching_schedules) == 1
            else next(
                (
                    schedule
                    for schedule in matching_schedules
                    if schedule.get("idx") is None
                ),
                None,
            )
        )
    if (
        matching_schedule is None
        and not allow_unknown_slot
        and not hinted_index_is_allowed
    ):
        return None
    if matching_schedule is not None:
        for key in ("idx", "label", "name"):
            if key in matching_schedule:
                batch_schedule[key] = matching_schedule[key]
    elif not hinted_index_is_allowed:
        batch_schedule["idx"] = None
        batch_schedule["label"] = "active_schedule"
        batch_schedule["writable"] = False

    preserved_indices = set(preserve_indices)
    replacement_schedule = batch_schedule
    if (
        matching_schedule is not None
        and matching_schedule.get("idx") in preserved_indices
        and matching_schedule.get("version") == batch_version
        and schedule_entry_has_usable_data(matching_schedule)
    ):
        # A successful action read from this refresh is the authoritative plan
        # content; use batch data only to select the active version.
        replacement_schedule = matching_schedule

    normalized = dict(existing)
    normalized["schedules"] = [
        (
            replacement_schedule
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
    active_index = batch_schedule.get("idx")
    if isinstance(active_index, int) and not isinstance(active_index, bool):
        normalized["active_schedule_index"] = active_index
    else:
        # Keep an explicit unknown-slot selection so calendar consumers can
        # filter to the authoritative idx=None fallback instead of treating a
        # missing numeric index as "include every slot with this version".
        normalized["active_schedule_index"] = None
    normalized["active_selection_available"] = True
    if incoming.get("current_task") is not None:
        normalized["current_task"] = incoming["current_task"]
    else:
        cached_current_task = normalized.get("current_task")
        if (
            isinstance(cached_current_task, Mapping)
            and cached_current_task.get("version") != batch_version
        ):
            normalized.pop("current_task", None)
    normalized["captured_at"] = captured_at.isoformat()
    normalized["source"] = "app_action_schedule_with_batch_refresh"
    return normalized
