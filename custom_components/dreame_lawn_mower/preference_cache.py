"""Reconcile exact mower preference write readback with batch snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

CONFIRMED_PREFERENCE_RETENTION = timedelta(minutes=2)
PREFERENCE_MODE_FIELD = "preference_mode"

_PREFERENCE_FIELD_COMPANIONS: dict[str, tuple[str, ...]] = {
    "efficient_mode": ("efficient_mode_name",),
    "mowing_direction_mode": (
        "mowing_direction_mode_name",
        "mowing_direction_method_name",
    ),
    "edge_mowing_walk_mode": (
        "edge_mowing_walk_mode_name",
        "turning_method_name",
    ),
    "cutter_position": ("cutter_position_name",),
    "obstacle_avoidance_ai": ("obstacle_avoidance_ai_classes",),
    "obstacle_avoidance_ai_classes": ("obstacle_avoidance_ai",),
}


@dataclass(frozen=True, slots=True)
class PendingPreferenceConfirmation:
    """One exactly confirmed field awaiting agreement from batch readback."""

    confirmed_at: datetime
    map_index: int
    area_id: int | None
    field: str
    values: Mapping[str, Any]


def retain_confirmed_preference_write(
    pending: Sequence[PendingPreferenceConfirmation],
    write_result: Mapping[str, Any],
    *,
    confirmed_at: datetime,
) -> list[PendingPreferenceConfirmation]:
    """Retain the exact fields confirmed by a successful mower readback."""
    confirmations = _confirmations_from_write(write_result, confirmed_at=confirmed_at)
    if not confirmations:
        return list(pending)

    replaced_keys = {_confirmation_key(item) for item in confirmations}
    retained = [
        item
        for item in pending
        if confirmed_at - item.confirmed_at <= CONFIRMED_PREFERENCE_RETENTION
        and _confirmation_key(item) not in replaced_keys
        and not _confirmation_contradicted_by_write(item, write_result)
    ]
    retained.extend(confirmations)
    return retained


def invalidate_preference_confirmations(
    pending: Sequence[PendingPreferenceConfirmation],
    *,
    map_index: int,
    area_id: int | None,
    fields: Sequence[str],
) -> list[PendingPreferenceConfirmation]:
    """Drop older values whose attempted rewrite has an uncertain outcome."""
    invalidated_keys = {
        (
            map_index,
            None if field == PREFERENCE_MODE_FIELD else area_id,
            _canonical_field(field),
        )
        for field in fields
    }
    return [
        item for item in pending if _confirmation_key(item) not in invalidated_keys
    ]


def reconcile_pending_preference_readbacks(
    batch_preferences: Mapping[str, Any],
    pending: Sequence[PendingPreferenceConfirmation],
    *,
    now: datetime,
    allow_convergence: bool = True,
) -> tuple[dict[str, Any], list[PendingPreferenceConfirmation]]:
    """Overlay recent exact confirmations until batch state converges."""
    active = [
        item
        for item in pending
        if now - item.confirmed_at <= CONFIRMED_PREFERENCE_RETENTION
    ]
    if not active:
        return _as_dict(batch_preferences), []

    incoming_valid = _valid_batch_preferences(batch_preferences)
    unresolved = (
        [
            item
            for item in active
            if not incoming_valid or not _confirmation_matches(batch_preferences, item)
        ]
        if allow_convergence
        else active
    )
    if not unresolved:
        return _as_dict(batch_preferences), []

    reconciled = dict(batch_preferences)
    for confirmation in unresolved:
        applied = _apply_confirmation(reconciled, confirmation)
        if applied is None:
            with_restored_target = _restore_confirmation_target(
                reconciled,
                confirmation,
            )
            if with_restored_target is not None:
                applied = _apply_confirmation(with_restored_target, confirmation)
        if applied is not None:
            reconciled = applied
    return reconciled, unresolved


def merge_confirmed_preference_readback(
    batch_device_data: Mapping[str, Any] | None,
    write_result: Mapping[str, Any],
    *,
    confirmed_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Merge exact changed fields into a valid cached batch snapshot."""
    if not isinstance(batch_device_data, Mapping):
        return None
    batch_preferences = batch_device_data.get("batch_mowing_preferences")
    if not _valid_batch_preferences(batch_preferences):
        return None

    timestamp = confirmed_at or datetime.now().astimezone()
    confirmations = _confirmations_from_write(write_result, confirmed_at=timestamp)
    if not confirmations:
        return None

    reconciled_preferences = dict(batch_preferences)
    for confirmation in confirmations:
        applied = _apply_confirmation(
            reconciled_preferences,
            confirmation,
        )
        if applied is None:
            return None
        reconciled_preferences = applied
    updated_batch_data = dict(batch_device_data)
    updated_batch_data["batch_mowing_preferences"] = reconciled_preferences
    return updated_batch_data


def _confirmations_from_write(
    write_result: Mapping[str, Any],
    *,
    confirmed_at: datetime,
) -> list[PendingPreferenceConfirmation]:
    if not _is_confirmed_write(write_result):
        return []
    map_index = _plain_int(write_result.get("map_index"))
    readback = write_result.get("readback")
    changed_fields = write_result.get("changed_fields")
    if (
        map_index is None
        or not isinstance(readback, Mapping)
        or not _field_sequence(changed_fields)
    ):
        return []

    readback_map = readback.get("map")
    if (
        not isinstance(readback_map, Mapping)
        or _plain_int(readback_map.get("idx")) != map_index
    ):
        return []

    confirmations: list[PendingPreferenceConfirmation] = []
    if PREFERENCE_MODE_FIELD in changed_fields:
        mode_values = {
            key: readback_map[key]
            for key in ("mode", "mode_name")
            if readback_map.get(key) is not None
        }
        if "mode" in mode_values:
            confirmations.append(
                PendingPreferenceConfirmation(
                    confirmed_at=confirmed_at,
                    map_index=map_index,
                    area_id=None,
                    field=PREFERENCE_MODE_FIELD,
                    values=mode_values,
                )
            )

    setting_fields = [
        field for field in changed_fields if field != PREFERENCE_MODE_FIELD
    ]
    if not setting_fields:
        return confirmations

    area_id = _plain_int(write_result.get("area_id"))
    readback_preference = readback.get("preference")
    if (
        area_id is None
        or not isinstance(readback_preference, Mapping)
        or _plain_int(readback_preference.get("area_id")) != area_id
    ):
        return confirmations

    for field in setting_fields:
        canonical_field = _canonical_field(field)
        related_fields = (field, *_PREFERENCE_FIELD_COMPANIONS.get(field, ()))
        values = {
            key: readback_preference[key]
            for key in related_fields
            if key in readback_preference and readback_preference[key] is not None
        }
        if field not in values and canonical_field not in values:
            continue
        confirmations.append(
            PendingPreferenceConfirmation(
                confirmed_at=confirmed_at,
                map_index=map_index,
                area_id=area_id,
                field=canonical_field,
                values=values,
            )
        )
    return confirmations


def _confirmation_matches(
    batch_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    target = _confirmation_target(batch_preferences, confirmation)
    return isinstance(target, Mapping) and all(
        target.get(key) == value for key, value in confirmation.values.items()
    )


def _apply_confirmation(
    batch_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> dict[str, Any] | None:
    maps = batch_preferences.get("maps")
    if not _mapping_sequence(maps):
        return None
    map_position = _mapping_position(maps, "idx", confirmation.map_index)
    if map_position is None:
        return None

    updated_map = dict(maps[map_position])
    if confirmation.area_id is None:
        updated_map.update(confirmation.values)
    else:
        preferences = updated_map.get("preferences")
        if not _mapping_sequence(preferences):
            return None
        preference_position = _mapping_position(
            preferences,
            "area_id",
            confirmation.area_id,
        )
        if preference_position is None:
            return None
        updated_preference = dict(preferences[preference_position])
        updated_preference.update(confirmation.values)
        updated_preferences = list(preferences)
        updated_preferences[preference_position] = updated_preference
        updated_map["preferences"] = updated_preferences

    updated_maps = list(maps)
    updated_maps[map_position] = updated_map
    updated_batch_preferences = dict(batch_preferences)
    updated_batch_preferences["maps"] = updated_maps
    return updated_batch_preferences


def _restore_confirmation_target(
    batch_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> dict[str, Any] | None:
    """Restore only target identity and exactly confirmed values."""
    batch_maps = batch_preferences.get("maps")
    usable_batch_maps = list(batch_maps) if _mapping_sequence(batch_maps) else []
    batch_map_position = _mapping_position(
        usable_batch_maps,
        "idx",
        confirmation.map_index,
    )
    if batch_map_position is None:
        updated_maps = usable_batch_maps
        restored_map: dict[str, Any] = {
            "idx": confirmation.map_index,
            "available": confirmation.area_id is not None,
            "area_count": 1 if confirmation.area_id is not None else 0,
            "preferences": [],
        }
        if confirmation.area_id is None:
            restored_map.update(confirmation.values)
        else:
            restored_map["preferences"] = [
                {
                    "map_index": confirmation.map_index,
                    "area_id": confirmation.area_id,
                    **confirmation.values,
                }
            ]
        updated_maps.append(restored_map)
        updated = dict(batch_preferences)
        updated["maps"] = updated_maps
        return updated
    if confirmation.area_id is None:
        return dict(batch_preferences)

    batch_map = usable_batch_maps[batch_map_position]
    batch_areas = batch_map.get("preferences")
    if not _mapping_sequence(batch_areas):
        updated_areas: list[Mapping[str, Any]] = []
    elif _mapping_position(batch_areas, "area_id", confirmation.area_id) is not None:
        return dict(batch_preferences)
    else:
        updated_areas = list(batch_areas)
    updated_areas.append(
        {
            "map_index": confirmation.map_index,
            "area_id": confirmation.area_id,
            **confirmation.values,
        }
    )
    updated_map = dict(batch_map)
    updated_map["preferences"] = updated_areas
    updated_map["area_count"] = len(updated_areas)
    updated_map["available"] = bool(updated_areas)
    updated_maps = usable_batch_maps
    updated_maps[batch_map_position] = updated_map
    updated = dict(batch_preferences)
    updated["maps"] = updated_maps
    return updated


def _confirmation_target(
    batch_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> Mapping[str, Any] | None:
    maps = batch_preferences.get("maps")
    if not _mapping_sequence(maps):
        return None
    map_position = _mapping_position(maps, "idx", confirmation.map_index)
    if map_position is None:
        return None
    target_map = maps[map_position]
    if confirmation.area_id is None:
        return target_map
    preferences = target_map.get("preferences")
    if not _mapping_sequence(preferences):
        return None
    preference_position = _mapping_position(
        preferences,
        "area_id",
        confirmation.area_id,
    )
    return None if preference_position is None else preferences[preference_position]


def _valid_batch_preferences(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("available") is True
        and not value.get("errors")
        and _mapping_sequence(value.get("maps"))
    )


def _is_confirmed_write(write_result: Mapping[str, Any]) -> bool:
    return bool(
        write_result.get("executed") is True
        and write_result.get("request_verified") is True
        and write_result.get("verification_source") == "preference_readback"
    )


def _confirmation_key(
    confirmation: PendingPreferenceConfirmation,
) -> tuple[int, int | None, str]:
    return confirmation.map_index, confirmation.area_id, confirmation.field


def _confirmation_contradicted_by_write(
    confirmation: PendingPreferenceConfirmation,
    write_result: Mapping[str, Any],
) -> bool:
    """Return whether later exact readback disproves an older confirmation."""
    if _plain_int(write_result.get("map_index")) != confirmation.map_index:
        return False
    readback = write_result.get("readback")
    if not isinstance(readback, Mapping):
        return False
    target = (
        readback.get("map")
        if confirmation.area_id is None
        else readback.get("preference")
        if _plain_int(write_result.get("area_id")) == confirmation.area_id
        else None
    )
    return isinstance(target, Mapping) and any(
        key in target and target.get(key) != value
        for key, value in confirmation.values.items()
    )


def _canonical_field(field: str) -> str:
    return (
        "obstacle_avoidance_ai"
        if field == "obstacle_avoidance_ai_classes"
        else field
    )


def _mapping_position(
    values: Sequence[Any],
    key: str,
    expected: int,
) -> int | None:
    return next(
        (
            position
            for position, entry in enumerate(values)
            if isinstance(entry, Mapping) and _plain_int(entry.get(key)) == expected
        ),
        None,
    )


def _plain_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve normal decoded dict identity when no reconciliation is needed."""
    return value if isinstance(value, dict) else dict(value)


def _field_sequence(value: Any) -> bool:
    return bool(
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes | bytearray)
        and all(isinstance(item, str) for item in value)
    )


def _mapping_sequence(value: Any) -> bool:
    return bool(
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes | bytearray)
        and all(isinstance(item, Mapping) for item in value)
    )
