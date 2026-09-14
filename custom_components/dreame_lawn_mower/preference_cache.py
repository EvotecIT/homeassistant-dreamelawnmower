"""Reconcile exact mower preference write readback with batch snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .dreame_lawn_mower_client.mowing_preferences import (
    MOWING_PREFERENCE_OPTIONAL_PAYLOAD_FIELDS,
)

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
    version_values: Mapping[str, int]


def retain_confirmed_preference_write(
    pending: Sequence[PendingPreferenceConfirmation],
    write_result: Mapping[str, Any],
    *,
    confirmed_at: datetime,
) -> list[PendingPreferenceConfirmation]:
    """Retain the exact fields confirmed by a successful mower readback."""
    confirmations = _confirmations_from_write(write_result, confirmed_at=confirmed_at)
    retained = [
        item
        for item in pending
        if not _is_confirmed_write(write_result)
        or not _confirmation_contradicted_by_write(item, write_result)
    ]
    if not confirmations:
        return retained

    replaced_keys = {_confirmation_key(item) for item in confirmations}
    retained = [
        item for item in retained if _confirmation_key(item) not in replaced_keys
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
    return [item for item in pending if _confirmation_key(item) not in invalidated_keys]


def retain_preference_confirmations_for_maps(
    pending: Sequence[PendingPreferenceConfirmation],
    map_indices: Sequence[int],
    *,
    observed_after: datetime | None = None,
) -> list[PendingPreferenceConfirmation]:
    """Drop confirmations for maps absent from authoritative inventory."""
    known_map_indices = set(map_indices)
    return [
        item
        for item in pending
        if item.map_index in known_map_indices
        or (observed_after is not None and item.confirmed_at > observed_after)
    ]


def reconcile_pending_preference_readbacks(
    batch_preferences: Mapping[str, Any],
    pending: Sequence[PendingPreferenceConfirmation],
    *,
    allow_convergence: bool = True,
    authoritative: bool = False,
    observed_after: datetime | None = None,
) -> tuple[dict[str, Any], list[PendingPreferenceConfirmation]]:
    """Overlay exact confirmations until batch or direct state supersedes them."""
    if not pending:
        return _as_dict(batch_preferences), []

    if authoritative:
        unresolved = []
        for item in pending:
            if observed_after is not None and item.confirmed_at > observed_after:
                unresolved.append(item)
                continue
            if _authoritative_confirmation_is_current_or_unknown(
                batch_preferences,
                item,
            ):
                unresolved.append(
                    _with_authoritative_version_values(batch_preferences, item)
                )
    elif allow_convergence:
        unresolved = [
            item
            for item in pending
            if (observed_after is not None and item.confirmed_at > observed_after)
            or not _confirmation_target_read_succeeded(batch_preferences, item)
            or not (
                _confirmation_matches(batch_preferences, item)
                or _confirmation_superseded_by_newer_version(
                    batch_preferences,
                    item,
                )
            )
        ]
    else:
        unresolved = list(pending)
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


def mowing_preference_map_read_complete(
    value: Any,
    *,
    require_version_evidence: bool = False,
) -> bool:
    """Return whether a map contains every advertised unique area record."""
    if not isinstance(value, Mapping):
        return False
    area_count = _plain_int(value.get("area_count"))
    preferences = value.get("preferences")
    if area_count is None or area_count < 0 or not _mapping_sequence(preferences):
        return False
    area_ids = [_plain_int(preference.get("area_id")) for preference in preferences]
    return bool(
        len(area_ids) == area_count
        and all(area_id is not None and area_id >= 0 for area_id in area_ids)
        and len(set(area_ids)) == len(area_ids)
        and all(
            _preference_version_is_current(
                preference,
                require_evidence=require_version_evidence,
            )
            for preference in preferences
        )
    )


def _preference_version_is_current(
    value: Mapping[str, Any],
    *,
    require_evidence: bool = False,
) -> bool:
    """Return whether PRE payload version agrees with its PREI advertisement."""
    version = _plain_int(value.get("version"))
    reported_version = _plain_int(value.get("reported_version"))
    if not require_evidence and (version is None or reported_version is None):
        return True
    return (
        version is not None
        and reported_version is not None
        and version == reported_version
    )


def _advertised_preference_area_ids(value: Mapping[str, Any]) -> set[int] | None:
    """Return a validated authoritative PREI area inventory when available."""
    raw_area_ids = value.get("advertised_area_ids")
    area_count = _plain_int(value.get("area_count"))
    if area_count is None or area_count < 0 or not isinstance(raw_area_ids, Sequence):
        return None
    if isinstance(raw_area_ids, str | bytes | bytearray):
        return None
    area_ids = [_plain_int(area_id) for area_id in raw_area_ids]
    if (
        len(area_ids) != area_count
        or any(area_id is None or area_id < 0 for area_id in area_ids)
        or len(set(area_ids)) != len(area_ids)
    ):
        return None
    return {area_id for area_id in area_ids if area_id is not None}


def _preference_map_needs_optional_fallback(value: Mapping[str, Any]) -> bool:
    """Return whether PRE records omitted unreadable optional trailing values."""
    for preference in _mapping_values(value.get("preferences")):
        raw_payload = preference.get("_raw_payload")
        if isinstance(raw_payload, Sequence) and not isinstance(
            raw_payload,
            str | bytes | bytearray,
        ):
            if any(
                len(raw_payload) <= index or preference.get(field) is None
                for index, field in MOWING_PREFERENCE_OPTIONAL_PAYLOAD_FIELDS
            ):
                return True
    return False


def mowing_preferences_need_optional_fallback(value: Any) -> bool:
    """Return whether any direct PRE record omitted optional trailing positions."""
    if not isinstance(value, Mapping):
        return False
    return any(
        _preference_map_needs_optional_fallback(preference_map)
        for preference_map in _mapping_values(value.get("maps"))
    )


def mowing_preference_optional_fallback_complete(
    direct_preferences: Mapping[str, Any] | None,
    batch_preferences: Mapping[str, Any] | None,
    *,
    map_index: int,
) -> bool:
    """Return whether current batch data fills unread direct optional fields."""
    direct_map = _preference_map(direct_preferences, map_index)
    if direct_map is None or not _preference_map_needs_optional_fallback(direct_map):
        return True
    batch_map = _preference_map(batch_preferences, map_index)
    if batch_map is None or batch_map.get("error") or batch_map.get("errors"):
        return False

    batch_areas = _mapping_values(batch_map.get("preferences"))
    for direct_area in _mapping_values(direct_map.get("preferences")):
        raw_payload = direct_area.get("_raw_payload")
        if not isinstance(raw_payload, Sequence) or isinstance(
            raw_payload,
            str | bytes | bytearray,
        ):
            continue
        missing_fields = [
            field
            for index, field in MOWING_PREFERENCE_OPTIONAL_PAYLOAD_FIELDS
            if len(raw_payload) <= index or direct_area.get(field) is None
        ]
        if not missing_fields:
            continue
        area_id = _plain_int(direct_area.get("area_id"))
        batch_area_position = (
            None
            if area_id is None
            else _mapping_position(batch_areas, "area_id", area_id)
        )
        if batch_area_position is None or any(
            batch_areas[batch_area_position].get(field) is None
            for field in missing_fields
        ):
            return False
    return True


def merge_mowing_preference_readbacks(
    direct_preferences: Mapping[str, Any],
    batch_preferences: Mapping[str, Any],
    *,
    source: str = "app_action_mowing_preferences_with_batch_fallback",
) -> dict[str, Any]:
    """Overlay successful direct maps and areas onto the batch fallback."""
    direct_maps = _mapping_values(direct_preferences.get("maps"))
    batch_maps = _mapping_values(batch_preferences.get("maps"))
    direct_errors = _mapping_values(direct_preferences.get("errors"))
    merged_maps = [dict(entry) for entry in batch_maps]

    for direct_map in direct_maps:
        map_index = _plain_int(direct_map.get("idx"))
        if map_index is None:
            continue
        map_position = _mapping_position(merged_maps, "idx", map_index)
        if map_position is None:
            merged_map = dict(direct_map)
            if not _valid_preference_mode_pair(direct_map):
                merged_map.pop("mode", None)
                merged_map.pop("mode_name", None)
            merged_maps.append(merged_map)
            continue
        map_has_error = bool(
            direct_map.get("error") or direct_map.get("errors")
        ) or any(_plain_int(error.get("idx")) == map_index for error in direct_errors)
        direct_areas = [
            area
            for area in _mapping_values(direct_map.get("preferences"))
            if _preference_version_is_current(area)
        ]
        batch_areas = _mapping_values(merged_maps[map_position].get("preferences"))
        if (
            not map_has_error
            and mowing_preference_map_read_complete(direct_map)
            and not _preference_map_needs_optional_fallback(direct_map)
            and (
                direct_areas
                or not batch_areas
                or (
                    _valid_preference_mode_pair(direct_map)
                    and _plain_int(direct_map.get("mode")) == 1
                    and _plain_int(direct_map.get("area_count")) == 0
                    and _advertised_preference_area_ids(direct_map) == set()
                )
            )
        ):
            merged_map = dict(merged_maps[map_position])
            merged_map.update(
                {
                    key: value
                    for key, value in direct_map.items()
                    if key not in {"preferences", "mode", "mode_name"}
                    and value is not None
                }
            )
            _overlay_valid_preference_mode(merged_map, direct_map)
            merged_map.pop("error", None)
            merged_map.pop("errors", None)
            merged_map["preferences"] = [dict(entry) for entry in direct_areas]
            merged_maps[map_position] = merged_map
            continue

        merged_map = dict(merged_maps[map_position])
        for key in (
            "idx",
            "label",
            "available",
        ):
            if key in direct_map and direct_map[key] is not None:
                merged_map[key] = direct_map[key]
        _overlay_valid_preference_mode(merged_map, direct_map)

        merged_areas = [
            dict(entry) for entry in _mapping_values(merged_map.get("preferences"))
        ]
        advertised_area_ids = _advertised_preference_area_ids(direct_map)
        direct_mode = (
            _plain_int(direct_map.get("mode"))
            if _valid_preference_mode_pair(direct_map)
            else None
        )
        authoritative_custom_area_ids = (
            advertised_area_ids if direct_mode == 1 else None
        )
        authoritative_global_area_ids = (
            {0} if direct_mode == 0 and advertised_area_ids == set() else None
        )
        if (
            direct_map.get("area_count") is not None
            and (
                direct_areas
                or not merged_areas
                or authoritative_custom_area_ids is not None
            )
        ):
            merged_map["area_count"] = direct_map["area_count"]
        authoritative_area_ids = (
            authoritative_custom_area_ids
            if authoritative_custom_area_ids is not None
            else authoritative_global_area_ids
        )
        if authoritative_area_ids is not None:
            merged_areas = [
                entry
                for entry in merged_areas
                if _plain_int(entry.get("area_id")) in authoritative_area_ids
            ]
        for direct_area in direct_areas:
            area_id = _plain_int(direct_area.get("area_id"))
            if area_id is None or (
                authoritative_area_ids is not None
                and area_id not in authoritative_area_ids
            ):
                continue
            area_position = _mapping_position(merged_areas, "area_id", area_id)
            if area_position is None:
                merged_areas.append(dict(direct_area))
            else:
                merged_area = dict(merged_areas[area_position])
                merged_area.update(
                    {
                        key: value
                        for key, value in direct_area.items()
                        if value is not None
                    }
                )
                merged_areas[area_position] = merged_area
        if authoritative_global_area_ids is not None:
            merged_map["area_count"] = len(merged_areas)
        merged_map["preferences"] = merged_areas
        merged_map["available"] = bool(merged_areas) or bool(
            merged_map.get("available")
        )
        merged_maps[map_position] = merged_map

    merged = dict(batch_preferences)
    merged.update(
        {
            key: value
            for key, value in direct_preferences.items()
            if key not in {"available", "maps"}
        }
    )
    merged["source"] = source
    merged["available"] = bool(
        direct_preferences.get("available") or batch_preferences.get("available")
    )
    merged["maps"] = merged_maps
    return merged


def _valid_preference_mode_pair(value: Mapping[str, Any]) -> bool:
    mode = _plain_int(value.get("mode"))
    return (
        mode in {0, 1}
        and value.get("mode_name")
        == {
            0: "global",
            1: "custom",
        }[mode]
    )


def _overlay_valid_preference_mode(
    target: dict[str, Any],
    source: Mapping[str, Any],
) -> None:
    if not _valid_preference_mode_pair(source):
        return
    target["mode"] = source["mode"]
    target["mode_name"] = source["mode_name"]


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
                    version_values={},
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
                version_values=_preference_version_values(readback_preference),
            )
        )
    return confirmations


def _confirmation_matches(
    batch_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    """Return whether values and retained version evidence have converged."""
    target = _confirmation_target(batch_preferences, confirmation)
    if not isinstance(target, Mapping):
        return False
    return _confirmation_values_match(batch_preferences, confirmation) and all(
        target.get(key) == value
        for key, value in confirmation.version_values.items()
    )


def _confirmation_superseded_by_newer_version(
    batch_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    """Return whether every retained area version advanced in current batch data."""
    if not confirmation.version_values:
        return False
    target = _confirmation_target(batch_preferences, confirmation)
    if not isinstance(target, Mapping):
        return False
    current_versions = _preference_version_values(target)
    return all(
        key in current_versions and current_versions[key] > value
        for key, value in confirmation.version_values.items()
    )


def _confirmation_values_match(
    preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    """Return whether only the user-visible confirmed values match."""
    target = _confirmation_target(preferences, confirmation)
    if not isinstance(target, Mapping):
        return False
    return all(target.get(key) == value for key, value in confirmation.values.items())


def _confirmation_target_read_succeeded(
    preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    """Return whether this target is usable despite unrelated read errors."""
    if preferences.get("available") is not True or _direct_target_has_error(
        preferences,
        confirmation,
    ):
        return False
    target = _confirmation_target(preferences, confirmation)
    return isinstance(target, Mapping) and all(
        key in target
        and (target.get(key) is not None or expected_value is None)
        for key, expected_value in confirmation.values.items()
    )


def _authoritative_confirmation_is_current_or_unknown(
    direct_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    """Retain matching or unread confirmations; direct contradictions supersede."""
    if _direct_target_has_error(direct_preferences, confirmation):
        return True
    target = _confirmation_target(direct_preferences, confirmation)
    if isinstance(target, Mapping):
        if any(
            key not in target
            or (target.get(key) is None and expected_value is not None)
            for key, expected_value in confirmation.values.items()
        ):
            return True
        return _confirmation_values_match(direct_preferences, confirmation)
    if confirmation.area_id is not None and _direct_map_uses_batch_global_area_zero(
        direct_preferences,
        confirmation.map_index,
        confirmation.area_id,
    ):
        return True
    return _direct_target_has_error(direct_preferences, confirmation)


def _with_authoritative_version_values(
    direct_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> PendingPreferenceConfirmation:
    """Use version evidence from the current direct record when it was readable."""
    if _direct_target_has_error(direct_preferences, confirmation):
        return confirmation
    target = _confirmation_target(direct_preferences, confirmation)
    if not isinstance(target, Mapping):
        return confirmation
    current_versions = _preference_version_values(target)
    if not current_versions:
        return confirmation
    if current_versions == confirmation.version_values:
        return confirmation
    return replace(confirmation, version_values=current_versions)


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
        updated_preference.update(confirmation.version_values)
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
                    **confirmation.version_values,
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
            **confirmation.version_values,
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


def _preference_map(
    preferences: Mapping[str, Any] | None,
    map_index: int,
) -> Mapping[str, Any] | None:
    if preferences is None:
        return None
    maps = preferences.get("maps")
    if not _mapping_sequence(maps):
        return None
    map_position = _mapping_position(maps, "idx", map_index)
    return None if map_position is None else maps[map_position]


def _valid_batch_preferences(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("available") is True
        and not value.get("errors")
        and _mapping_sequence(value.get("maps"))
    )


def _direct_target_has_error(
    direct_preferences: Mapping[str, Any],
    confirmation: PendingPreferenceConfirmation,
) -> bool:
    raw_errors = direct_preferences.get("errors")
    if raw_errors and not _mapping_sequence(raw_errors):
        return True
    errors = _mapping_values(raw_errors)
    reported_error = any(
        _plain_int(error.get("idx")) in {None, confirmation.map_index}
        and (
            _plain_int(error.get("area_id")) is None
            if confirmation.area_id is None
            else _plain_int(error.get("area_id")) in {None, confirmation.area_id}
        )
        for error in errors
    )
    if reported_error:
        return True
    maps = _mapping_values(direct_preferences.get("maps"))
    map_position = _mapping_position(maps, "idx", confirmation.map_index)
    if map_position is None:
        return False
    target_map = maps[map_position]
    if (
        target_map.get("error")
        and _confirmation_target(
            direct_preferences,
            confirmation,
        )
        is None
    ):
        return True
    if confirmation.area_id is None:
        return False
    raw_map_errors = target_map.get("errors")
    if raw_map_errors and not _mapping_sequence(raw_map_errors):
        return True
    return any(
        _plain_int(error.get("area_id")) in {None, confirmation.area_id}
        for error in _mapping_values(raw_map_errors)
    )


def _direct_map_uses_batch_global_area_zero(
    direct_preferences: Mapping[str, Any],
    map_index: int,
    area_id: int,
) -> bool:
    """Return whether global area zero can legitimately exist only in batch."""
    maps = _mapping_values(direct_preferences.get("maps"))
    map_position = _mapping_position(maps, "idx", map_index)
    if map_position is None:
        return False
    target_map = maps[map_position]
    return bool(
        area_id == 0
        and _plain_int(target_map.get("mode")) == 0
        and target_map.get("mode_name") == "global"
        and not _mapping_values(target_map.get("preferences"))
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
        "obstacle_avoidance_ai" if field == "obstacle_avoidance_ai_classes" else field
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


def _preference_version_values(preference: Mapping[str, Any]) -> dict[str, int]:
    """Keep exact app-action versions alongside confirmed preference fields."""
    return {
        key: value
        for key in ("reported_version", "version")
        if (value := _plain_int(preference.get(key))) is not None
    }


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


def _mapping_values(value: Any) -> list[Mapping[str, Any]]:
    return list(value) if _mapping_sequence(value) else []
