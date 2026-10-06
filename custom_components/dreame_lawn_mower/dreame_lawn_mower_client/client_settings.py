"""Reusable schedules, preferences, maintenance, weather, and voice operations."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from .app_read_transport import run_app_read
from .batch_device_data import (
    decode_batch_mowing_preferences,
    decode_batch_ota_info,
)
from .client_constants import (
    VOICE_LANGUAGE_INDEX_TO_CODE,
    VOICE_LANGUAGE_INDEX_TO_LABEL,
    VOICE_PROMPT_FIELDS,
)
from .client_map_helpers import (
    _normalize_app_map_entries,
)
from .client_settings_helpers import (
    _as_optional_int,
    _batch_ota_keys,
    _batch_settings_keys,
    _debug_ota_model_name,
    _mowing_preference_map_overview,
    _mowing_preference_overview,
    _normalize_voice_prompt_flags,
)
from .client_shared_helpers import (
    _ensure_app_write_succeeded,
    _positive_int,
)
from .debug_ota_catalog import (
    build_debug_ota_catalog_url,
    normalize_debug_ota_catalog_payload,
)
from .device_settings_read_plan import read_maintenance, read_voice
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
    mark_write_attempted,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .maintenance import (
    build_cms_set_request,
    maintenance_item_status,
    maintenance_status_from_app_data,
    reset_cms_counter,
)
from .mowing_height_capabilities import mowing_height_adjustment_supported
from .mowing_preferences import (
    MOWING_PREFERENCE_MODE_FIELD,
    apply_mowing_preference_changes,
    encode_mowing_preference_payload,
    mowing_preference_mode_name,
    normalize_mowing_preference_mode,
)
from .mowing_preferences_read_plan import (
    read_mowing_preferences,
    read_preference_map_indices,
)
from .payload_utils import (
    _as_optional_text,
    _json_safe,
)
from .work_log import (
    WORK_LOG_TOTALS_REQUEST,
    DreameLawnMowerWorkLogTotals,
    work_log_totals_from_app_data,
)

_MOWING_PREFERENCE_READBACK_DELAYS_SECONDS = (0.0, 1.0, 2.0)


class _DreameLawnMowerClientSettingsMixin:
    def _sync_get_work_log_totals(self) -> DreameLawnMowerWorkLogTotals:
        """Fetch mower-owned lifetime totals through the MIHIS app action."""
        response = self._sync_call_app_action(
            WORK_LOG_TOTALS_REQUEST,
            redact_response=True,
        )
        return work_log_totals_from_app_data(response)





    def _sync_plan_app_mowing_preference_update(
        self,
        map_index: int,
        area_id: int | None,
        changes: Mapping[str, Any],
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute an app-action payload for mower preference changes."""
        if execute and not confirm_write:
            raise ValueError(
                "Preference writes require confirm_write=True when execute=True."
            )
        if not isinstance(changes, Mapping) or not changes:
            raise ValueError("At least one mowing preference change is required.")
        if "mowing_height_cm" in changes and not mowing_height_adjustment_supported(
            self.descriptor.model, self.descriptor.display_model
        ):
            raise ValueError("Mowing height is adjusted manually on this mower.")

        preferences = self._sync_get_mowing_preferences(map_indices=[map_index])
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
                model=getattr(self.descriptor, "model", None),
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
                else updated_preference.get("obstacle_avoidance_ai_classes")
                if key == "obstacle_avoidance_ai_classes"
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
                        refreshed = self._sync_refresh_preference_after_mode_write(
                            map_index=map_index,
                            area_id=area_id,
                            setting_changes=setting_changes,
                            requested_mode=requested_mode,
                        )
                        request["d"] = refreshed["payload"]
                        for key in (
                            "payload", "previous_preference", "updated_preference"
                        ):
                            result[key] = refreshed[key]
                        result["changed_fields"] = list(dict.fromkeys([
                            *result["changed_fields"], *refreshed["changed_fields"]
                        ]))
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
                    response = self._sync_call_app_action(request)
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
                result["readback"] = self._sync_verify_mowing_preference_readback(
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

    def _sync_refresh_preference_after_mode_write(
        self,
        *,
        map_index: int,
        area_id: int,
        setting_changes: Mapping[str, Any],
        requested_mode: int,
    ) -> dict[str, Any]:
        """Wait for an acknowledged PREP before rebuilding the settings write."""
        last_error: Exception | None = None
        for delay in _MOWING_PREFERENCE_READBACK_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                refreshed = self._sync_plan_app_mowing_preference_update(
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

    def _sync_verify_mowing_preference_readback(
        self,
        *,
        map_index: int,
        area_id: int | None,
        setting_changes: Mapping[str, Any],
        requested_mode: int | None,
    ) -> dict[str, Any]:
        """Require bounded exact PRE/PREI readback after acknowledgement."""
        last_error: (
            DreameLawnMowerCommandRejectedError | DreameLawnMowerConnectionError | None
        ) = None
        for delay in _MOWING_PREFERENCE_READBACK_DELAYS_SECONDS:
            if delay:
                time.sleep(delay)
            try:
                return self._sync_read_mowing_preference_confirmation(
                    map_index=map_index,
                    area_id=area_id,
                    setting_changes=setting_changes,
                    requested_mode=requested_mode,
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

    def _sync_read_mowing_preference_confirmation(
        self,
        *,
        map_index: int,
        area_id: int | None,
        setting_changes: Mapping[str, Any],
        requested_mode: int | None,
    ) -> dict[str, Any]:
        """Perform one exact mower preference readback attempt."""
        preferences = self._sync_get_mowing_preferences(map_indices=[map_index])
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
                model=getattr(self.descriptor, "model", None),
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


    def _sync_get_current_app_map_index(
        self,
        *,
        deadline: float | None = None,
    ) -> int | None:
        try:
            request_options: dict[str, Any] = {}
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                request_options = {
                    "retry_count": 0,
                    "timeout": remaining,
                    "deadline": deadline,
                }
            map_list_result = self._sync_call_app_action(
                {"m": "g", "t": "MAPL"},
                **request_options,
            )
            for entry in _normalize_app_map_entries(map_list_result):
                if entry.get("current"):
                    return _positive_int(entry.get("idx"))
        except Exception:  # noqa: BLE001 - best-effort hint only
            return None
        return None

    def _sync_get_mowing_preferences(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode read-only mower preference settings."""
        return run_app_read(
            read_mowing_preferences(include_raw, map_indices),
            self._sync_call_app_action,
        )

    def _sync_get_batch_mowing_preferences(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        map_index_hints: Sequence[int] | None = None,
        map_slot_index_hints: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode mower preferences from batch device data."""
        batch_data = self._sync_get_batch_device_data(_batch_settings_keys())
        return decode_batch_mowing_preferences(
            batch_data,
            include_raw=include_raw,
            map_indices=map_indices,
            map_index_hints=map_index_hints,
            map_slot_index_hints=map_slot_index_hints,
        )

    def _sync_get_batch_ota_info(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch and decode OTA state from batch device data."""
        batch_data = self._sync_get_batch_device_data(_batch_ota_keys())
        return decode_batch_ota_info(batch_data, include_raw=include_raw)

    def _sync_get_debug_ota_catalog(
        self,
        model_name: str | None = None,
        current_version: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch the public debug/manual OTA catalog for the mower model."""
        short_model = _debug_ota_model_name(model_name or self._descriptor.model)
        if not short_model:
            raise DreameLawnMowerConnectionError(
                "Could not determine a short model name for the debug OTA catalog."
            )

        resolved_current_version = current_version
        if resolved_current_version is None:
            try:
                device = self._sync_update_device()
            except DreameLawnMowerConnectionError:
                device = None
            if device is not None:
                resolved_current_version = _as_optional_text(
                    getattr(getattr(device, "info", None), "firmware_version", None)
                )

        url = build_debug_ota_catalog_url(short_model)
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.load(response)
        except Exception as err:  # noqa: BLE001 - network/protocol errors vary here
            raise DreameLawnMowerConnectionError(
                f"Debug OTA catalog fetch failed: {err}"
            ) from err

        result = normalize_debug_ota_catalog_payload(
            payload,
            model_name=short_model,
            current_version=resolved_current_version,
            include_raw=include_raw,
        )
        result["url"] = url
        return result

    def _sync_get_maintenance_status(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only CMS maintenance counter state."""
        return run_app_read(read_maintenance(include_raw), self._sync_call_app_action)

    def _sync_plan_maintenance_reset(
        self,
        item: str,
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute a guarded CMS maintenance counter reset request."""
        if execute and not confirm_write:
            raise ValueError(
                "Maintenance resets require confirm_write=True when execute=True."
            )

        status = self._sync_get_maintenance_status(include_raw=False)
        values = status.get("raw_cms")
        if not isinstance(values, Sequence) or isinstance(
            values,
            str | bytes | bytearray,
        ):
            raise DreameLawnMowerConnectionError(
                "Could not read CMS maintenance counters before planning reset."
            )

        updated_values = reset_cms_counter(values, item)
        request = build_cms_set_request(updated_values)
        before = maintenance_item_status(status, item)
        planned_status = maintenance_status_from_app_data(
            {"value": updated_values},
            source="planned_maintenance_reset",
        )
        after = maintenance_item_status(planned_status, item)
        result: dict[str, Any] = {
            "source": "app_action_maintenance_cms",
            "action": "reset_maintenance_counter",
            "item": after.get("key") if isinstance(after, Mapping) else item,
            "item_name": after.get("name") if isinstance(after, Mapping) else item,
            "dry_run": not execute,
            "executed": False,
            "changed": list(values) != updated_values,
            "previous_cms": list(values),
            "updated_cms": updated_values,
            "previous_item": before,
            "updated_item": after,
            "request": request,
        }

        if not execute:
            return result

        response = self._sync_call_app_action(request)
        response_data = _ensure_app_write_succeeded(
            response,
            operation="Maintenance reset",
        )
        result["dry_run"] = False
        result["executed"] = True
        result["response"] = _json_safe(response, max_depth=4)
        result["response_data"] = _json_safe(response_data, max_depth=4)
        try:
            refreshed = self._sync_get_maintenance_status(include_raw=False)
            result["refreshed_cms"] = refreshed.get("raw_cms")
            result["refreshed_item"] = maintenance_item_status(refreshed, item)
        except Exception as err:  # noqa: BLE001 - write result is still useful
            result["refresh_error"] = str(err)
        return result

    def _sync_get_voice_settings(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only voice and language settings from CFG."""
        return run_app_read(read_voice(include_raw), self._sync_call_app_action)

    def _sync_set_voice_language(self, voice_language: int) -> dict[str, Any]:
        """Set the mower voice language and return the confirmed response."""
        request = {
            "m": "s",
            "t": "LANG",
            "d": {
                "type": "voice",
                "value": int(voice_language),
            },
        }
        response = self._sync_call_app_action(request)
        data = _ensure_app_write_succeeded(
            response,
            operation="Voice language write",
        )
        if not isinstance(data, Mapping):
            raise DreameLawnMowerConnectionError(
                f"LANG voice write returned invalid data: {response}"
            )
        confirmed_voice_language = _as_optional_int(data.get("voice"))
        confirmed_text_language = _as_optional_int(data.get("text"))
        return {
            "source": "app_action_voice_settings_write",
            "action": "set_voice_language",
            "request": _json_safe(request, max_depth=4),
            "response_data": _json_safe(response, max_depth=4),
            "text_language_index": confirmed_text_language,
            "voice_language_index": confirmed_voice_language,
            "voice_language_name": VOICE_LANGUAGE_INDEX_TO_LABEL.get(
                confirmed_voice_language
            ),
            "voice_language_code": VOICE_LANGUAGE_INDEX_TO_CODE.get(
                confirmed_voice_language
            ),
        }

    def _sync_set_voice_volume(self, volume: int) -> dict[str, Any]:
        """Set the mower voice volume and return the confirmed response."""
        if volume < 0 or volume > 100:
            raise ValueError("volume must be between 0 and 100")
        request = {
            "m": "s",
            "t": "VOL",
            "d": {
                "value": int(volume),
            },
        }
        response = self._sync_call_app_action(request)
        data = _ensure_app_write_succeeded(
            response,
            operation="Voice volume write",
        )
        if not isinstance(data, Mapping):
            raise DreameLawnMowerConnectionError(
                f"VOL write returned invalid data: {response}"
            )
        return {
            "source": "app_action_voice_settings_write",
            "action": "set_voice_volume",
            "request": _json_safe(request, max_depth=4),
            "response_data": _json_safe(response, max_depth=4),
            "volume": _as_optional_int(data.get("value")),
        }

    def _sync_set_voice_prompts(self, prompts: Sequence[int]) -> dict[str, Any]:
        """Set the mower voice prompt flags and return the confirmed response."""
        normalized = _normalize_voice_prompt_flags(prompts)
        request = {
            "m": "s",
            "t": "VOICE",
            "d": {
                "value": normalized,
            },
        }
        response = self._sync_call_app_action(request)
        data = _ensure_app_write_succeeded(
            response,
            operation="Voice prompt write",
        )
        if not isinstance(data, Mapping):
            raise DreameLawnMowerConnectionError(
                f"VOICE write returned invalid data: {response}"
            )
        confirmed = _normalize_voice_prompt_flags(data.get("value"))
        result = {
            "source": "app_action_voice_settings_write",
            "action": "set_voice_prompts",
            "request": _json_safe(request, max_depth=4),
            "response_data": _json_safe(response, max_depth=4),
            "voice_prompts": confirmed,
        }
        for field_name, enabled in zip(VOICE_PROMPT_FIELDS, confirmed, strict=True):
            result[field_name] = bool(enabled)
        return result



    def _app_map_indices(
        self,
        map_indices: Sequence[int] | None,
        *,
        deadline: float | None = None,
    ) -> list[int]:
        return run_app_read(
            read_preference_map_indices(map_indices, deadline=deadline),
            self._sync_call_app_action,
        )
