"""Shared mowing preference planning, guarded writes and exact readback."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any

from .client_settings_helpers import (
    _mowing_preference_map_overview,
    _mowing_preference_overview,
)
from .client_shared_helpers import _ensure_app_write_succeeded, _positive_int
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
    mark_write_attempted,
)
from .mowing_height_capabilities import mowing_height_adjustment_supported
from .mowing_preferences import (
    MOWING_PREFERENCE_MODE_FIELD,
    apply_mowing_preference_changes,
    encode_mowing_preference_payload,
    mowing_preference_mode_name,
    normalize_mowing_preference_mode,
)
from .payload_utils import _json_safe

_MOWING_PREFERENCE_READBACK_DELAYS_SECONDS = (0.0, 1.0, 2.0)


@dataclass(frozen=True)
class ReadPreferences:
    map_index: int


@dataclass(frozen=True)
class PreferenceCommand:
    action: Mapping[str, Any]


@dataclass(frozen=True)
class PreferenceDelay:
    seconds: float


type PreferenceWriteRequest = ReadPreferences | PreferenceCommand | PreferenceDelay


def plan_preference_update(
    model: str,
    display_model: str,
    map_index: int,
    area_id: int | None,
    changes: Mapping[str, Any],
    execute: bool = False,
    confirm_write: bool = False,
) -> Generator[PreferenceWriteRequest, Any, dict[str, Any]]:
    """Build or execute an app-action payload for mower preference changes."""
    if execute and not confirm_write:
        raise ValueError(
            "Preference writes require confirm_write=True when execute=True."
        )
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("At least one mowing preference change is required.")
    if "mowing_height_cm" in changes and not mowing_height_adjustment_supported(
        model, display_model
    ):
        raise ValueError("Mowing height is adjusted manually on this mower.")

    preferences = yield ReadPreferences(map_index)
    maps = preferences.get("maps")
    if not isinstance(maps, list) or not maps:
        raise DreameLawnMowerConnectionError(
            f"No mowing preference metadata returned for map index {map_index}."
        )

    preference_map = maps[0]
    raw_preferences = preference_map.get("preferences")
    if not isinstance(raw_preferences, list):
        raise DreameLawnMowerConnectionError(
            f"No decoded mowing preferences returned for map index {map_index}."
        )

    mode = _positive_int(preference_map.get("mode"))
    requested_mode = None
    if MOWING_PREFERENCE_MODE_FIELD in changes:
        requested_mode = normalize_mowing_preference_mode(
            changes[MOWING_PREFERENCE_MODE_FIELD]
        )
    mode_changed = requested_mode is not None and requested_mode != mode

    setting_changes = {
        key: value
        for key, value in changes.items()
        if key != MOWING_PREFERENCE_MODE_FIELD
    }
    if (
        requested_mode is not None
        and requested_mode == 0
        and setting_changes
        and mode_changed
    ):
        raise ValueError(
            "preference_mode=global cannot be combined with per-area setting "
            "changes in the same request."
        )

    current_preference: Mapping[str, Any] | None = None
    updated_preference: Mapping[str, Any] | None = None
    changed_fields: list[str] = []
    payload: list[int] | None = None
    settings_request: dict[str, Any] | None = None

    if setting_changes:
        if not isinstance(area_id, int):
            raise ValueError(
                "area_id is required when planning per-area mowing preference "
                "setting changes."
            )
        for item in raw_preferences:
            if not isinstance(item, Mapping):
                continue
            if _positive_int(item.get("area_id")) == area_id:
                current_preference = item
                break
        if current_preference is None:
            available_area_ids = [
                _positive_int(item.get("area_id"))
                for item in raw_preferences
                if isinstance(item, Mapping)
            ]
            raise ValueError(
                f"Mowing preference area {area_id} was not found for map index "
                f"{map_index}. Available areas: {available_area_ids}"
            )

        updated_preference, changed_fields = apply_mowing_preference_changes(
            current_preference,
            setting_changes,
            model=model,
        )
        payload = encode_mowing_preference_payload(updated_preference)
        settings_request = {
            "m": "s",
            "t": "PRE",
            "d": payload,
        }

    mode_request = None
    if requested_mode is not None and (mode_changed or not setting_changes):
        mode_request = {
            "m": "s",
            "t": "PREP",
            "d": {
                "idx": map_index,
                "value": requested_mode,
            },
        }

    request_sequence = [
        request
        for request in [mode_request, settings_request]
        if isinstance(request, dict)
    ]
    if not request_sequence:
        request_sequence = [settings_request] if settings_request else []

    combined_changed_fields = (
        [MOWING_PREFERENCE_MODE_FIELD] if mode_changed else []
    ) + changed_fields
    primary_request = (
        request_sequence[0]
        if len(request_sequence) == 1
        else {"sequence": request_sequence}
        if request_sequence
        else None
    )
    result: dict[str, Any] = {
        "source": "app_action_mowing_preference_write",
        "action": "plan_mowing_preference_update",
        "dry_run": not execute,
        "executed": False,
        "execute_supported": True,
        "request_verified": False,
        "write_commands": {
            "settings": "PRE",
            "mode": "PREP",
        },
        "map_index": map_index,
        "area_id": area_id,
        "mode": mode,
        "mode_name": preference_map.get("mode_name"),
        "target_mode": requested_mode,
        "target_mode_name": mowing_preference_mode_name(requested_mode),
        "mode_changed": mode_changed,
        "changed": bool(combined_changed_fields),
        "changed_fields": combined_changed_fields,
        "changes": {
            key: mowing_preference_mode_name(requested_mode)
            if key == MOWING_PREFERENCE_MODE_FIELD
            else updated_preference.get(key)
            if updated_preference is not None
            else None
            for key in changes
        },
        "map": _mowing_preference_map_overview(preference_map),
        "previous_preference": _mowing_preference_overview(current_preference)
        if current_preference is not None
        else None,
        "updated_preference": _mowing_preference_overview(updated_preference)
        if updated_preference is not None
        else None,
        "payload": payload,
        "request_candidate": primary_request,
        "request_candidates": request_sequence,
        "notes": (
            [
                "Preference write prepared but not executed.",
                "Send the candidate PRE/PREP request only with execute=true and an "
                "explicit confirmation gate.",
            ]
            if not execute
            else [
                "Preference write executed through the PRE/PREP request "
                "sequence after "
                "explicit confirmation.",
            ]
        ),
    }
    if execute:
        possibly_applied_fields: list[str] = []
        responses: list[Any] = []
        response_payloads: list[Any] = []
        for request in request_sequence:
            if request.get("t") == "PRE" and mode_request is not None:
                try:
                    # PREP can advance the PRE revision and change its values.
                    # Rebuild the settings write from that new snapshot.
                    refreshed = yield from refresh_after_mode_write(
                        model=model,
                        display_model=display_model,
                        map_index=map_index,
                        area_id=area_id,
                        setting_changes=setting_changes,
                        requested_mode=requested_mode,
                    )
                    request["d"] = refreshed["payload"]
                    for key in ("payload", "previous_preference", "updated_preference"):
                        result[key] = refreshed[key]
                    result["changed_fields"] = list(
                        dict.fromkeys(
                            [*result["changed_fields"], *refreshed["changed_fields"]]
                        )
                    )
                    result["changed"] = bool(result["changed_fields"])
                    result["changes"].update(refreshed["changes"])
                except Exception as err:
                    mark_write_attempted(err, fields=possibly_applied_fields)
                    raise
            request_fields = (
                [MOWING_PREFERENCE_MODE_FIELD]
                if request.get("t") == "PREP"
                else list(updated_preference)
                if isinstance(updated_preference, Mapping)
                else list(setting_changes)
            )
            try:
                response = yield PreferenceCommand(request)
                is_preference_write = request.get("t") in {"PRE", "PREP"}
                response_data = _ensure_app_write_succeeded(
                    response,
                    operation="Preference write",
                    allow_missing_data=is_preference_write,
                )
            except DreameLawnMowerCommandRejectedError as err:
                if possibly_applied_fields:
                    mark_write_attempted(err, fields=possibly_applied_fields)
                raise
            except Exception as err:
                mark_write_attempted(
                    err,
                    fields=(*possibly_applied_fields, *request_fields),
                )
                raise
            possibly_applied_fields.extend(request_fields)
            try:
                responses.append(_json_safe(response, max_depth=4))
                response_payloads.append(_json_safe(response_data, max_depth=4))
            except Exception as err:
                mark_write_attempted(err, fields=possibly_applied_fields)
                raise
        try:
            result["readback"] = yield from verify_preference_readback(
                model=model,
                map_index=map_index,
                area_id=area_id,
                setting_changes=setting_changes,
                requested_mode=requested_mode,
            )
            result["verification_source"] = "preference_readback"
            result["notes"].append(
                "The requested values were confirmed through exact mower "
                "preference readback."
            )
            result["executed"] = True
            result["request_verified"] = True
            if len(responses) == 1:
                result["response"] = responses[0]
                result["response_data"] = response_payloads[0]
            else:
                result["responses"] = responses
                result["response_data"] = response_payloads
        except Exception as err:
            if possibly_applied_fields:
                mark_write_attempted(err, fields=possibly_applied_fields)
            raise
    return result


def refresh_after_mode_write(
    *,
    model: str,
    display_model: str,
    map_index: int,
    area_id: int | None,
    setting_changes: Mapping[str, Any],
    requested_mode: int | None,
) -> Generator[PreferenceWriteRequest, Any, dict[str, Any]]:
    """Wait for an acknowledged PREP before rebuilding the settings write."""
    last_error: Exception | None = None
    for delay in _MOWING_PREFERENCE_READBACK_DELAYS_SECONDS:
        if delay:
            yield PreferenceDelay(delay)
        try:
            refreshed = yield from plan_preference_update(
                model=model,
                display_model=display_model,
                map_index=map_index,
                area_id=area_id,
                changes=setting_changes,
            )
            if refreshed.get("mode") != requested_mode:
                raise DreameLawnMowerConnectionError(
                    "The mower acknowledged the preference mode write "
                    "but did not return the requested mode before the "
                    "settings write. Refresh the mower before retrying."
                )
            return refreshed
        except (
            DreameLawnMowerCommandRejectedError,
            DreameLawnMowerConnectionError,
            ValueError,
        ) as err:
            # Initial planning already validated the requested changes. A
            # missing fresh area (including PRE/PREI mismatch) may be delayed.
            last_error = err
    if last_error is not None:
        raise last_error
    raise DreameLawnMowerConnectionError(
        "The mower preference snapshot could not be refreshed after mode write."
    )


def verify_preference_readback(
    *,
    model: str,
    map_index: int,
    area_id: int | None,
    setting_changes: Mapping[str, Any],
    requested_mode: int | None,
) -> Generator[PreferenceWriteRequest, Any, dict[str, Any]]:
    """Require bounded exact PRE/PREI readback after acknowledgement."""
    last_error: (
        DreameLawnMowerCommandRejectedError | DreameLawnMowerConnectionError | None
    ) = None
    for delay in _MOWING_PREFERENCE_READBACK_DELAYS_SECONDS:
        if delay:
            yield PreferenceDelay(delay)
        try:
            return (
                yield from read_preference_confirmation(
                    model=model,
                    map_index=map_index,
                    area_id=area_id,
                    setting_changes=setting_changes,
                    requested_mode=requested_mode,
                )
            )
        except (
            DreameLawnMowerCommandRejectedError,
            DreameLawnMowerConnectionError,
        ) as err:
            last_error = err

    if last_error is not None:
        raise last_error
    raise DreameLawnMowerConnectionError(
        "The mower preference readback could not be attempted."
    )


def read_preference_confirmation(
    *,
    model: str,
    map_index: int,
    area_id: int | None,
    setting_changes: Mapping[str, Any],
    requested_mode: int | None,
) -> Generator[PreferenceWriteRequest, Any, dict[str, Any]]:
    """Perform one exact mower preference readback attempt."""
    preferences = yield ReadPreferences(map_index)
    maps = preferences.get("maps")
    preference_map = (
        next(
            (
                item
                for item in maps
                if isinstance(item, Mapping) and item.get("idx") == map_index
            ),
            None,
        )
        if isinstance(maps, list)
        else None
    )
    if not isinstance(preference_map, Mapping):
        raise DreameLawnMowerConnectionError(
            "The mower acknowledged the preference write but did not return the "
            "target map for readback. Refresh the mower before trying again."
        )

    unconfirmed_fields: list[str] = []
    if (
        requested_mode is not None
        and _positive_int(preference_map.get("mode")) != requested_mode
    ):
        unconfirmed_fields.append(MOWING_PREFERENCE_MODE_FIELD)

    readback_preference: Mapping[str, Any] | None = None
    if setting_changes:
        raw_preferences = preference_map.get("preferences")
        if isinstance(raw_preferences, list):
            readback_preference = next(
                (
                    item
                    for item in raw_preferences
                    if isinstance(item, Mapping)
                    and _positive_int(item.get("area_id")) == area_id
                ),
                None,
            )
        if readback_preference is None:
            raise DreameLawnMowerConnectionError(
                "The mower acknowledged the preference write but did not return "
                "the target area for readback. Refresh the mower before trying "
                "again."
            )
        _, remaining_changes = apply_mowing_preference_changes(
            readback_preference,
            setting_changes,
            model=model,
        )
        unconfirmed_fields.extend(remaining_changes)

    if unconfirmed_fields:
        fields = ", ".join(dict.fromkeys(unconfirmed_fields))
        raise DreameLawnMowerCommandRejectedError(
            "The mower acknowledged the preference write, but readback did not "
            f"confirm the requested fields: {fields}."
        )

    return {
        "source": "app_action_mowing_preference_readback",
        "map": _mowing_preference_map_overview(preference_map),
        "preference": (
            _mowing_preference_overview(readback_preference)
            if readback_preference is not None
            else None
        ),
    }
