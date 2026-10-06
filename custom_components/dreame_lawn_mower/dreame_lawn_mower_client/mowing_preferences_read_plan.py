"""Shared mowing preference discovery and per-area read validation."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from typing import Any

from .app_read_transport import AppReadRequest
from .client_map_helpers import _app_map_entries_are_valid, _normalize_app_map_entries
from .client_settings_helpers import _as_optional_int, _dedupe_ints
from .client_shared_helpers import _app_action_data, _positive_int
from .exceptions import DreameLawnMowerConnectionError
from .mowing_preferences import (
    MOWING_PREFERENCE_MODE_NAMES,
    MOWING_PREFERENCE_PROPERTY_KEY,
    _mowing_preference_versions_match,
    decode_mowing_preference_payload,
    summarize_mowing_preference_info,
)
from .payload_utils import _json_safe


def _preference_request(
    action: Mapping[str, Any], *, deadline: float | None,
) -> AppReadRequest:
    return AppReadRequest(action, retry_count=2, timeout=20.0, deadline=deadline)


def read_preference_map_indices(
    map_indices: Sequence[int] | None, *, deadline: float | None = None,
) -> Generator[AppReadRequest, Any, list[int]]:
    if map_indices is not None:
        return [idx for idx in _dedupe_ints(map_indices) if idx >= 0]
    try:
        map_list_result = yield AppReadRequest(
            {"m": "g", "t": "MAPL"},
            retry_count=0 if deadline is not None else 2,
            timeout=5.0 if deadline is not None else 20.0,
            deadline=deadline,
        )
        map_entries = _normalize_app_map_entries(map_list_result)
        if not _app_map_entries_are_valid(map_list_result, map_entries):
            return [0, 1]
        detected = [entry["idx"] for entry in map_entries]
    except Exception:  # noqa: BLE001 - fall back to the two likely map slots
        detected = [0, 1]
    return _dedupe_ints(detected)


def read_mowing_preferences(
    include_raw: bool = False, map_indices: Sequence[int] | None = None,
    *, deadline: float | None = None,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    result: dict[str, Any] = {
        "source": "app_action_mowing_preferences",
        "available": False,
        "property_hint": MOWING_PREFERENCE_PROPERTY_KEY,
        "maps": [],
        "errors": [],
    }

    indices = yield from read_preference_map_indices(map_indices, deadline=deadline)
    for map_index in indices:
        entry: dict[str, Any] = {
            "idx": map_index,
            "label": f"map_{map_index}",
            "available": False,
            "preferences": [],
        }
        try:
            info_result = yield _preference_request(
                {"m": "g", "t": "PREI", "d": {"idx": map_index}},
                deadline=deadline,
            )
            if include_raw:
                entry["raw_info"] = _json_safe(info_result, max_depth=4)
            info = _app_action_data(info_result)
            info_summary = summarize_mowing_preference_info(info)
            entry["mode"] = info_summary.get("mode")
            entry["mode_name"] = info_summary.get("mode_name")
            advertised_area_inventory_valid = bool(
                info_summary.get("area_inventory_valid")
            )
            if advertised_area_inventory_valid:
                entry["area_count"] = info_summary.get("area_count")

            areas = info_summary.get("areas")
            if not isinstance(areas, Sequence) or isinstance(
                areas,
                str | bytes | bytearray,
            ):
                areas = []

            preferences: list[dict[str, Any]] = []
            area_errors: list[dict[str, Any]] = []
            mode = info_summary.get("mode")
            mode_name = info_summary.get("mode_name")
            mode_supported = bool(
                isinstance(mode, int)
                and not isinstance(mode, bool)
                and mode in MOWING_PREFERENCE_MODE_NAMES
                and mode_name == MOWING_PREFERENCE_MODE_NAMES[mode]
            )
            if not mode_supported:
                mode_error = {
                    "idx": map_index,
                    "stage": "preference_info",
                    "error": (
                        "PREI returned unsupported preference mode "
                        f"{mode!r} ({mode_name!r})."
                    ),
                }
                area_errors.append(mode_error)
                result["errors"].append(mode_error)
            advertised_area_ids: set[int] = set()
            for area_position, area in enumerate(areas):
                if not isinstance(area, Mapping):
                    advertised_area_inventory_valid = False
                    continue
                area_id = _positive_int(area.get("area_id"))
                if area_id is None:
                    advertised_area_inventory_valid = False
                    area_error = {
                        "idx": map_index,
                        "area_position": area_position,
                        "stage": "preference_info",
                        "error": "PREI returned an invalid area identity.",
                    }
                    area_errors.append(area_error)
                    result["errors"].append(area_error)
                    continue
                if area_id in advertised_area_ids:
                    advertised_area_inventory_valid = False
                    area_error = {
                        "idx": map_index,
                        "area_id": area_id,
                        "area_position": area_position,
                        "stage": "preference_info",
                        "error": "PREI returned a duplicate area identity.",
                    }
                    area_errors.append(area_error)
                    result["errors"].append(area_error)
                    continue
                advertised_area_ids.add(area_id)
                try:
                    preference_result = yield _preference_request(
                        {
                            "m": "g",
                            "t": "PRE",
                            "d": {"idx": map_index, "region": area_id},
                        },
                        deadline=deadline,
                    )
                    preference_data = _app_action_data(preference_result)
                    if not isinstance(preference_data, Sequence) or isinstance(
                        preference_data,
                        str | bytes | bytearray,
                    ):
                        raise DreameLawnMowerConnectionError(
                            "PRE returned invalid preference data for map "
                            f"{map_index} area {area_id}."
                        )
                    preference = decode_mowing_preference_payload(preference_data)
                    if len(preference_data) < 17:
                        raise DreameLawnMowerConnectionError(
                            "PRE returned a truncated preference payload for map "
                            f"{map_index} area {area_id}: expected at least 17 "
                            f"positions, received {len(preference_data)}."
                        )
                    if any(
                        _as_optional_int(value) is None
                        for value in preference_data[3:17]
                    ):
                        raise DreameLawnMowerConnectionError(
                            "PRE returned an unreadable mandatory preference "
                            f"value for map {map_index} area {area_id}."
                        )
                    reported_map_index = _positive_int(
                        preference.get("map_index")
                    )
                    reported_area_id = _positive_int(preference.get("area_id"))
                    if (
                        reported_map_index != map_index
                        or reported_area_id != area_id
                    ):
                        raise DreameLawnMowerConnectionError(
                            "PRE returned mismatched preference identity for "
                            f"requested map {map_index} area {area_id}: payload "
                            f"map {reported_map_index} area {reported_area_id}."
                        )
                    preference["reported_version"] = area.get("version")
                    version = _positive_int(preference.get("version"))
                    reported_version = _positive_int(area.get("version"))
                    if version is None or reported_version is None:
                        raise DreameLawnMowerConnectionError(
                            "PRE/PREI returned missing preference version evidence "
                            f"for map {map_index} area {area_id}."
                        )
                    if not _mowing_preference_versions_match(
                        version, reported_version
                    ):
                        raise DreameLawnMowerConnectionError(
                            "PRE returned preference version "
                            f"{version} for map {map_index} area {area_id}, but "
                            f"PREI advertised version {reported_version}."
                        )
                    if include_raw:
                        preference["raw_response"] = _json_safe(
                            preference_result,
                            max_depth=4,
                        )
                        preference["raw_payload"] = _json_safe(
                            list(preference_data),
                            max_depth=2,
                        )
                    preferences.append(preference)
                except Exception as err:  # noqa: BLE001 - isolate each area
                    area_error = {
                        "idx": map_index,
                        "area_id": area_id,
                        "stage": "preference",
                        "error": str(err),
                    }
                    area_errors.append(area_error)
                    result["errors"].append(area_error)

            if not advertised_area_inventory_valid and not area_errors:
                inventory_error = {
                    "idx": map_index,
                    "stage": "preference_info",
                    "error": (
                        "PREI returned a missing or malformed area version "
                        "inventory."
                    ),
                }
                area_errors.append(inventory_error)
                result["errors"].append(inventory_error)

            if not advertised_area_inventory_valid:
                entry.pop("area_count", None)
            area_count = _positive_int(entry.get("area_count"))
            if (
                advertised_area_inventory_valid
                and area_count is not None
                and len(advertised_area_ids) == area_count
            ):
                entry["advertised_area_ids"] = sorted(advertised_area_ids)
            entry["preferences"] = preferences
            entry["available"] = mode_supported
            if area_errors:
                entry["errors"] = area_errors
                if not preferences:
                    entry["error"] = area_errors[0]["error"]
            if entry["available"]:
                result["available"] = True
        except Exception as err:  # noqa: BLE001 - keep probing other maps
            entry["error"] = str(err)
            result["errors"].append(
                {"idx": map_index, "stage": "preferences", "error": str(err)}
            )
        result["maps"].append(entry)

    return result
