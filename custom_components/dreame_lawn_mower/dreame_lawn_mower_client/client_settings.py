"""Reusable schedules, preferences, maintenance, weather, and voice operations."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from .batch_device_data import (
    decode_batch_mowing_preferences,
    decode_batch_ota_info,
    decode_batch_schedule_payload,
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
    _batch_schedule_keys,
    _batch_settings_keys,
    _debug_ota_model_name,
    _dedupe_ints,
    _mowing_preference_map_overview,
    _mowing_preference_overview,
    _normalize_voice_prompt_flags,
    _schedule_entry_overview,
    _schedule_plan_overview,
    _schedule_upload_overview,
    _voice_settings_summary,
    _weather_protection_active_summary,
    _weather_protection_summary,
)
from .client_shared_helpers import (
    _app_action_data,
    _ensure_app_write_succeeded,
    _positive_int,
)
from .debug_ota_catalog import (
    build_debug_ota_catalog_url,
    normalize_debug_ota_catalog_payload,
)
from .exceptions import (
    DreameLawnMowerConnectionError,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .maintenance import (
    CMS_GET_REQUEST,
    build_cms_set_request,
    maintenance_item_status,
    maintenance_status_from_app_data,
    reset_cms_counter,
)
from .mowing_preferences import (
    MOWING_PREFERENCE_MODE_FIELD,
    MOWING_PREFERENCE_PROPERTY_KEY,
    apply_mowing_preference_changes,
    decode_mowing_preference_payload,
    encode_mowing_preference_payload,
    mowing_preference_mode_name,
    normalize_mowing_preference_mode,
    summarize_mowing_preference_info,
)
from .payload_utils import (
    _as_optional_text,
    _json_safe,
)
from .schedule import (
    EMPTY_SCHEDULE_VERSION,
    SCHEDULE_CHUNK_SIZE,
    build_schedule_enable_status_request,
    build_schedule_upload_requests,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
    schedule_task_summary,
)

SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS = 5.0
SCHEDULE_READ_DEADLINE_SECONDS = 10.0
SCHEDULE_READ_TIMEOUT_SECONDS = 5.0


class _DreameLawnMowerClientSettingsMixin:
    def _sync_get_app_schedules(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
        include_current_task: bool = True,
    ) -> dict[str, Any]:
        """Fetch and decode mower schedules through read-only app actions."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")

        result: dict[str, Any] = {
            "source": "app_action_schedule",
            "available": False,
            "current_task": None,
            "schedules": [],
            "errors": [],
        }

        if include_current_task:
            try:
                current_task_deadline = (
                    time.monotonic() + SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS
                )
                task_result = self._sync_call_app_action(
                    {"m": "g", "t": "SCHDT", "d": {"t": 0}},
                    retry_count=0,
                    timeout=SCHEDULE_CURRENT_TASK_TIMEOUT_SECONDS,
                    deadline=current_task_deadline,
                )
                result["raw_current_task"] = _json_safe(task_result, max_depth=4)
                task_data = _app_action_data(task_result)
                result["current_task"] = schedule_task_summary(task_data)
            except Exception as err:  # noqa: BLE001 - optional diagnostic
                result["errors"].append(
                    {"stage": "current_task", "error": str(err)}
                )

        schedule_started_at = time.monotonic()
        schedule_deadline = schedule_started_at + SCHEDULE_READ_DEADLINE_SECONDS
        map_discovery_deadline = schedule_deadline
        if map_indices is None:
            # Treat MAPL as another fair-budget participant so a nonresponsive
            # discovery probe cannot consume the schedule slots' whole window.
            map_discovery_deadline = min(
                schedule_deadline,
                schedule_started_at + SCHEDULE_READ_DEADLINE_SECONDS / 4,
            )
        schedule_indices = self._app_schedule_map_indices(
            map_indices,
            deadline=map_discovery_deadline,
        )
        for position, map_index in enumerate(schedule_indices):
            now = time.monotonic()
            remaining = max(0.0, schedule_deadline - now)
            remaining_slots = len(schedule_indices) - position
            slot_deadline = min(
                schedule_deadline,
                now + remaining / remaining_slots,
            )
            schedule_result: dict[str, Any] = {
                "idx": map_index,
                "label": "default" if map_index == -1 else f"map_{map_index}",
                "available": False,
            }
            try:
                info_result = self._sync_call_app_action(
                    {"m": "g", "t": "SCHDIV2", "d": {"i": map_index}},
                    retry_count=0,
                    timeout=SCHEDULE_READ_TIMEOUT_SECONDS,
                    deadline=slot_deadline,
                )
                schedule_result["raw_info"] = _json_safe(info_result, max_depth=4)
                info = _app_action_data(info_result)
                if not isinstance(info, Mapping):
                    raise DreameLawnMowerConnectionError(
                        "SCHDIV2 returned invalid schedule metadata."
                    )
                size = _positive_int(info.get("l"))
                version = _positive_int(info.get("v"))
                schedule_result["size"] = size
                schedule_result["version"] = version
                if not size or version is None or version == EMPTY_SCHEDULE_VERSION:
                    schedule_result["plans"] = []
                    result["schedules"].append(schedule_result)
                    continue

                payload_text, chunk_count, offset = self._sync_get_app_schedule_text(
                    size=size,
                    version=version,
                    chunk_size=chunk_size,
                    deadline=slot_deadline,
                )
                plans = decode_schedule_payload_text(payload_text)
                schedule_result.update(
                    {
                        "available": bool(plans),
                        "chunk_count": chunk_count,
                        "downloaded_size": offset,
                        "plan_count": len(plans),
                        "enabled_plan_count": sum(
                            1 for plan in plans if plan.get("enabled")
                        ),
                        "plans": plans,
                    }
                )
                if include_raw:
                    schedule_result["raw_text"] = payload_text
                if plans:
                    result["available"] = True
            except Exception as err:  # noqa: BLE001 - keep probing other maps
                schedule_result["error"] = str(err)
                result["errors"].append(
                    {"idx": map_index, "stage": "schedule", "error": str(err)}
                )
            result["schedules"].append(schedule_result)

        return result

    def _sync_set_app_schedule_plan_enabled(
        self,
        map_index: int,
        plan_id: int,
        enabled: bool,
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute a schedule enable-status app action request."""
        if execute and not confirm_write:
            raise ValueError(
                "Schedule writes require confirm_write=True when execute=True."
            )

        schedules = self._sync_get_app_schedules(map_indices=[map_index])
        if not schedules.get("schedules"):
            raise DreameLawnMowerConnectionError(
                f"No schedule metadata returned for map index {map_index}."
            )
        schedule = schedules["schedules"][0]
        version = _positive_int(schedule.get("version"))
        if version is None or version == EMPTY_SCHEDULE_VERSION:
            raise DreameLawnMowerConnectionError(
                f"No writable schedule version returned for map index {map_index}."
            )
        plans = schedule.get("plans")
        if not isinstance(plans, list):
            raise DreameLawnMowerConnectionError(
                f"No decoded schedule plans returned for map index {map_index}."
            )

        updated_plans: list[dict[str, Any]] = []
        previous_enabled: bool | None = None
        found = False
        for plan in plans:
            if not isinstance(plan, Mapping):
                continue
            updated_plan = dict(plan)
            if _positive_int(updated_plan.get("plan_id")) == plan_id:
                previous_enabled = bool(updated_plan.get("enabled"))
                updated_plan["enabled"] = bool(enabled)
                found = True
            updated_plans.append(updated_plan)
        if not found:
            raise ValueError(
                f"Schedule plan {plan_id} was not found for map index {map_index}."
            )

        request = build_schedule_enable_status_request(
            map_index=map_index,
            version=version,
            plans=updated_plans,
        )
        target_enabled = bool(enabled)
        result: dict[str, Any] = {
            "source": "app_action_schedule_write",
            "action": "set_schedule_plan_enabled",
            "dry_run": not execute,
            "executed": False,
            "map_index": map_index,
            "plan_id": plan_id,
            "previous_enabled": previous_enabled,
            "enabled": target_enabled,
            "changed": (
                previous_enabled is not None and previous_enabled != target_enabled
            ),
            "schedule": _schedule_entry_overview(schedule),
            "target_plan": _schedule_plan_overview(
                updated_plans,
                plan_id=plan_id,
                previous_enabled=previous_enabled,
                enabled=target_enabled,
            ),
            "version": version,
            "request": request,
        }
        if execute:
            response = self._sync_call_app_action(request)
            response_data = _ensure_app_write_succeeded(
                response,
                operation="Schedule write",
            )
            result["executed"] = True
            result["response"] = _json_safe(response, max_depth=4)
            result["response_data"] = _json_safe(response_data, max_depth=4)
        return result

    def _sync_plan_app_schedule_upload(
        self,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        execute: bool = False,
        confirm_write: bool = False,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Build or execute a full schedule upload request sequence."""
        if execute and not confirm_write:
            raise ValueError(
                "Schedule writes require confirm_write=True when execute=True."
            )
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")
        if not isinstance(plans, Sequence) or isinstance(plans, str | bytes):
            raise ValueError("plans must be a sequence of schedule plan mappings.")

        schedules = self._sync_get_app_schedules(map_indices=[map_index])
        if not schedules.get("schedules"):
            raise DreameLawnMowerConnectionError(
                f"No schedule metadata returned for map index {map_index}."
            )
        schedule = schedules["schedules"][0]
        version = _positive_int(schedule.get("version"))
        if version is None or version == EMPTY_SCHEDULE_VERSION:
            raise DreameLawnMowerConnectionError(
                f"No writable schedule version returned for map index {map_index}."
            )
        current_plans = schedule.get("plans")
        if not isinstance(current_plans, list):
            raise DreameLawnMowerConnectionError(
                f"No decoded schedule plans returned for map index {map_index}."
            )

        try:
            payload_text = encode_schedule_payload_text(list(plans))
            normalized_plans = decode_schedule_payload_text(payload_text)
        except Exception as err:  # noqa: BLE001 - caller gets readable validator text
            raise ValueError(f"Invalid schedule plans: {err}") from err

        current_payload_text = encode_schedule_payload_text(current_plans)
        requests = build_schedule_upload_requests(
            map_index=map_index,
            payload_text=payload_text,
            version=version,
            chunk_size=chunk_size,
        )
        request_candidate: dict[str, Any] | None = (
            requests[0]
            if len(requests) == 1
            else {"sequence": requests}
            if requests
            else None
        )
        result: dict[str, Any] = {
            "source": "app_action_schedule_write",
            "action": "upload_schedule_plans",
            "dry_run": not execute,
            "executed": False,
            "map_index": map_index,
            "changed": current_payload_text != payload_text,
            "version": version,
            "chunk_size": chunk_size,
            "chunk_count": max(len(requests) - 1, 0),
            "payload_size": len(payload_text.encode("utf-8")),
            "schedule": _schedule_entry_overview(schedule),
            "target_schedule": _schedule_upload_overview(normalized_plans),
            "request": request_candidate,
        }
        if execute:
            responses: list[Any] = []
            response_data_items: list[Any] = []
            for request in requests:
                response = self._sync_call_app_action(request)
                response_data = _ensure_app_write_succeeded(
                    response,
                    operation="Schedule upload",
                )
                responses.append(_json_safe(response, max_depth=4))
                response_data_items.append(_json_safe(response_data, max_depth=4))
            result["executed"] = True
            result["response"] = responses
            result["response_data"] = response_data_items
        return result

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
            responses: list[Any] = []
            response_payloads: list[Any] = []
            for request in request_sequence:
                response = self._sync_call_app_action(request)
                response_data = _ensure_app_write_succeeded(
                    response,
                    operation="Preference write",
                )
                responses.append(_json_safe(response, max_depth=4))
                response_payloads.append(_json_safe(response_data, max_depth=4))
            result["executed"] = True
            result["request_verified"] = True
            if len(responses) == 1:
                result["response"] = responses[0]
                result["response_data"] = response_payloads[0]
            else:
                result["responses"] = responses
                result["response_data"] = response_payloads
        return result

    def _sync_get_batch_schedules(
        self,
        include_raw: bool = False,
        map_index_hint: int | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode schedule data from batch device data."""
        if map_index_hint is None:
            map_index_hint = self._sync_get_current_app_map_index()
        batch_data = self._sync_get_batch_device_data(_batch_schedule_keys())
        if batch_data is None:
            return {
                "source": "batch_device_data_schedule",
                "available": False,
                "current_task": None,
                "schedules": [],
                "errors": [
                    {
                        "stage": "schedule",
                        "error": "Batch device data returned no schedule payload.",
                    }
                ],
            }
        return decode_batch_schedule_payload(
            batch_data,
            include_raw=include_raw,
            map_index_hint=map_index_hint,
        )

    def _sync_get_current_app_map_index(self) -> int | None:
        try:
            map_list_result = self._sync_call_app_action({"m": "g", "t": "MAPL"})
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
        result: dict[str, Any] = {
            "source": "app_action_mowing_preferences",
            "available": False,
            "property_hint": MOWING_PREFERENCE_PROPERTY_KEY,
            "maps": [],
            "errors": [],
        }

        for map_index in self._app_map_indices(map_indices):
            entry: dict[str, Any] = {
                "idx": map_index,
                "label": f"map_{map_index}",
                "available": False,
                "preferences": [],
            }
            try:
                info_result = self._sync_call_app_action(
                    {"m": "g", "t": "PREI", "d": {"idx": map_index}}
                )
                if include_raw:
                    entry["raw_info"] = _json_safe(info_result, max_depth=4)
                info = _app_action_data(info_result)
                info_summary = summarize_mowing_preference_info(info)
                entry["mode"] = info_summary.get("mode")
                entry["mode_name"] = info_summary.get("mode_name")
                entry["area_count"] = info_summary.get("area_count")

                areas = info_summary.get("areas")
                if not isinstance(areas, Sequence) or isinstance(
                    areas,
                    str | bytes | bytearray,
                ):
                    areas = []

                preferences: list[dict[str, Any]] = []
                for area in areas:
                    if not isinstance(area, Mapping):
                        continue
                    area_id = _positive_int(area.get("area_id"))
                    if area_id is None:
                        continue
                    preference_result = self._sync_call_app_action(
                        {
                            "m": "g",
                            "t": "PRE",
                            "d": {"idx": map_index, "region": area_id},
                        }
                    )
                    preference_data = _app_action_data(preference_result)
                    if not isinstance(preference_data, Sequence) or isinstance(
                        preference_data,
                        str | bytes | bytearray,
                    ):
                        raise DreameLawnMowerConnectionError(
                            f"PRE returned invalid preference data for map {map_index} "
                            f"area {area_id}."
                        )
                    preference = decode_mowing_preference_payload(preference_data)
                    preference["area_id"] = area_id
                    preference["reported_version"] = area.get("version")
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

                entry["preferences"] = preferences
                entry["available"] = bool(preferences)
                if preferences:
                    result["available"] = True
            except Exception as err:  # noqa: BLE001 - keep probing other maps
                entry["error"] = str(err)
                result["errors"].append(
                    {"idx": map_index, "stage": "preferences", "error": str(err)}
                )
            result["maps"].append(entry)

        return result

    def _sync_get_batch_mowing_preferences(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        map_index_hints: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode mower preferences from batch device data."""
        batch_data = self._sync_get_batch_device_data(_batch_settings_keys())
        if batch_data is None:
            return {
                "source": "batch_device_data_mowing_preferences",
                "available": False,
                "property_hint": MOWING_PREFERENCE_PROPERTY_KEY,
                "maps": [],
                "errors": [
                    {
                        "stage": "settings",
                        "error": "Batch device data returned no settings payload.",
                    }
                ],
            }
        return decode_batch_mowing_preferences(
            batch_data,
            include_raw=include_raw,
            map_indices=map_indices,
            map_index_hints=map_index_hints,
        )

    def _sync_get_batch_ota_info(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch and decode OTA state from batch device data."""
        batch_data = self._sync_get_batch_device_data(_batch_ota_keys())
        if batch_data is None:
            return {
                "source": "batch_device_data_ota_info",
                "available": False,
                "ota_info": None,
                "update_available": None,
                "auto_upgrade_enabled": None,
                "errors": [
                    {
                        "stage": "ota",
                        "error": "Batch device data returned no OTA payload.",
                    }
                ],
            }
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
        result: dict[str, Any] = {
            "source": "app_action_maintenance_cms",
            "available": False,
            "items": [],
            "raw_cms": None,
            "errors": [],
        }

        try:
            cms_result = self._sync_call_app_action(CMS_GET_REQUEST)
            if include_raw:
                result["raw_cms_response"] = _json_safe(cms_result, max_depth=4)
            cms_data = _app_action_data(cms_result)
            result.update(
                maintenance_status_from_app_data(
                    cms_data,
                    source="app_action_maintenance_cms",
                )
            )
            if result.get("available"):
                return result
        except Exception as err:  # noqa: BLE001 - fallback to CFG may still work
            result["errors"].append({"stage": "cms", "error": str(err)})

        try:
            config_result = self._sync_call_app_action({"m": "g", "t": "CFG"})
            if include_raw:
                result["raw_config_response"] = _json_safe(config_result, max_depth=4)
            config = _app_action_data(config_result)
            result.update(
                maintenance_status_from_app_data(
                    config,
                    source="app_action_config_cms",
                )
            )
        except Exception as err:  # noqa: BLE001 - diagnostic probe returns evidence
            result["errors"].append({"stage": "config", "error": str(err)})
        return result

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

    def _sync_get_weather_protection(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only weather and rain-protection settings."""
        result: dict[str, Any] = {
            "source": "app_action_weather_protection",
            "available": False,
            "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
            "config_keys": ["WRF", "WRP"],
            "rain_end_time_command": "RPET",
            "errors": [],
            "warnings": [],
        }

        try:
            config_result = self._sync_call_app_action({"m": "g", "t": "CFG"})
            if include_raw:
                result["raw_config"] = _json_safe(config_result, max_depth=4)
            config = _app_action_data(config_result)
            if not isinstance(config, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"CFG returned invalid weather config: {config_result}"
                )
            result["present_config_keys"] = [
                key for key in result["config_keys"] if key in config
            ]
            result.update(_weather_protection_summary(config))
            result["available"] = True
        except Exception as err:  # noqa: BLE001 - diagnostic probe should return evidence
            result["errors"].append({"stage": "config", "error": str(err)})

        try:
            rain_end_result = self._sync_call_app_action({"m": "g", "t": "RPET"})
            if include_raw:
                result["raw_rain_end_time"] = _json_safe(
                    rain_end_result,
                    max_depth=4,
                )
            rain_end_data = _app_action_data(rain_end_result)
            if isinstance(rain_end_data, Mapping):
                end_time = rain_end_data.get("endTime")
                if end_time is None:
                    end_time = rain_end_data.get("end_time")
                if end_time is not None:
                    result["rain_protect_end_time"] = end_time
                    result["rain_protect_end_time_present"] = True
                    result["available"] = True
                else:
                    result["rain_protect_end_time_present"] = False
            elif rain_end_data is None:
                result["rain_protect_end_time_present"] = False
            elif rain_end_data is not None:
                result["warnings"].append(
                    {
                        "stage": "rain_end_time",
                        "warning": f"RPET returned unexpected data: {rain_end_data}",
                    }
                )
        except Exception as err:  # noqa: BLE001 - RPET may only answer while protection is active
            result["warnings"].append({"stage": "rain_end_time", "warning": str(err)})

        result.update(_weather_protection_active_summary(result))
        return result

    def _sync_get_voice_settings(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only voice and language settings from CFG."""
        result: dict[str, Any] = {
            "source": "app_action_voice_settings",
            "available": False,
            "config_keys": ["LANG", "VOL", "VOICE"],
            "errors": [],
            "warnings": [],
        }

        try:
            config_result = self._sync_call_app_action({"m": "g", "t": "CFG"})
            config = _app_action_data(config_result)
            if not isinstance(config, Mapping):
                weather_result = self._sync_get_weather_protection(include_raw=True)
                weather_raw_config = (
                    weather_result.get("raw_config")
                    if isinstance(weather_result, Mapping)
                    else None
                )
                weather_data = _app_action_data(weather_raw_config)
                if isinstance(weather_data, Mapping):
                    config_result = weather_raw_config
                    config = weather_data
            if include_raw:
                result["raw_config"] = _json_safe(config_result, max_depth=4)
            if not isinstance(config, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"CFG returned invalid voice config: {config_result}"
                )
            result["present_config_keys"] = [
                key for key in result["config_keys"] if key in config
            ]
            result.update(_voice_settings_summary(config))
            result["available"] = bool(result["present_config_keys"])
        except Exception as err:  # noqa: BLE001 - diagnostic probe should return evidence
            result["errors"].append({"stage": "config", "error": str(err)})

        return result

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

    def _sync_get_app_schedule_text(
        self,
        *,
        size: int,
        version: int,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
        deadline: float | None = None,
    ) -> tuple[str, int, int]:
        chunks = bytearray()
        offset = 0
        chunk_count = 0
        while offset < size:
            request_size = min(chunk_size, size - offset)
            chunk_result = self._sync_call_app_action(
                {
                    "m": "g",
                    "t": "SCHDDV2",
                    "d": {"s": offset, "l": request_size, "v": version},
                },
                retry_count=0,
                timeout=SCHEDULE_READ_TIMEOUT_SECONDS,
                deadline=deadline,
            )
            data = _app_action_data(chunk_result)
            if not isinstance(data, Mapping) or "d" not in data:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV2 returned invalid chunk at offset {offset}."
                )
            text = str(data.get("d") or "")
            encoded = text.encode("utf-8")
            returned_size = _positive_int(data.get("l"))
            if not encoded:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV2 returned empty data at offset {offset}."
                )
            if len(chunks) + len(encoded) > size:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV2 returned too much data at offset {offset}."
                )
            chunks.extend(encoded)
            offset += returned_size if returned_size else len(encoded)
            chunk_count += 1
        return chunks.decode("utf-8"), chunk_count, offset

    def _app_schedule_map_indices(
        self,
        map_indices: Sequence[int] | None,
        *,
        deadline: float | None = None,
    ) -> list[int]:
        if map_indices is not None:
            return _dedupe_ints(map_indices)
        return _dedupe_ints(
            [-1, *self._app_map_indices(None, deadline=deadline)]
        )

    def _app_map_indices(
        self,
        map_indices: Sequence[int] | None,
        *,
        deadline: float | None = None,
    ) -> list[int]:
        if map_indices is not None:
            return [idx for idx in _dedupe_ints(map_indices) if idx >= 0]
        try:
            request_options: dict[str, Any] = {}
            if deadline is not None:
                request_options = {
                    "retry_count": 0,
                    "timeout": SCHEDULE_READ_TIMEOUT_SECONDS,
                    "deadline": deadline,
                }
            map_list_result = self._sync_call_app_action(
                {"m": "g", "t": "MAPL"},
                **request_options,
            )
            detected = [
                entry["idx"] for entry in _normalize_app_map_entries(map_list_result)
            ]
        except Exception:  # noqa: BLE001 - fall back to the two likely map slots
            detected = [0, 1]
        return _dedupe_ints(detected)
