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
from .client_map_helpers import (
    _normalize_app_map_entries,
)
from .client_settings_helpers import (
    _batch_ota_keys,
    _batch_settings_keys,
    _debug_ota_model_name,
)
from .client_shared_helpers import (
    _positive_int,
)
from .debug_ota_catalog import (
    build_debug_ota_catalog_url,
    normalize_debug_ota_catalog_payload,
)
from .device_settings_read_plan import read_maintenance, read_voice
from .exceptions import (
    DreameLawnMowerConnectionError,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .maintenance_reset_plan import plan_maintenance_reset, run_maintenance_reset
from .mowing_preferences_read_plan import (
    read_mowing_preferences,
    read_preference_map_indices,
)
from .payload_utils import (
    _as_optional_text,
)
from .preference_write_plan import plan_preference_update
from .preference_write_transport import run_preference_write
from .voice_write_plan import (
    run_voice_write,
    write_voice_language,
    write_voice_prompts,
    write_voice_volume,
)
from .work_log import (
    WORK_LOG_TOTALS_REQUEST,
    DreameLawnMowerWorkLogTotals,
    work_log_totals_from_app_data,
)


class _DreameLawnMowerClientSettingsMixin:
    def _sync_get_work_log_totals(self) -> DreameLawnMowerWorkLogTotals:
        """Fetch mower-owned lifetime totals through the MIHIS app action."""
        response = self._sync_call_app_action(
            WORK_LOG_TOTALS_REQUEST,
            redact_response=True,
        )
        return work_log_totals_from_app_data(response)





    def _sync_plan_app_mowing_preference_update(
        self, map_index: int, area_id: int | None, changes: Mapping[str, Any],
        execute: bool = False, confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Drive shared preference policy through legacy synchronous operations."""
        return run_preference_write(
            plan_preference_update(
                self.descriptor.model, self.descriptor.display_model,
                map_index, area_id, changes, execute, confirm_write,
            ),
            lambda index: self._sync_get_mowing_preferences(map_indices=[index]),
            self._sync_call_app_action,
            time.sleep,
        )


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
        return run_maintenance_reset(
            plan_maintenance_reset(item, execute, confirm_write),
            lambda: self._sync_get_maintenance_status(include_raw=False),
            self._sync_call_app_action,
        )

    def _sync_get_voice_settings(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only voice and language settings from CFG."""
        return run_app_read(read_voice(include_raw), self._sync_call_app_action)

    def _sync_set_voice_language(self, voice_language: int) -> dict[str, Any]:
        return run_voice_write(
            write_voice_language(voice_language), self._sync_call_app_action
        )

    def _sync_set_voice_volume(self, volume: int) -> dict[str, Any]:
        return run_voice_write(write_voice_volume(volume), self._sync_call_app_action)

    def _sync_set_voice_prompts(self, prompts: Sequence[int]) -> dict[str, Any]:
        return run_voice_write(write_voice_prompts(prompts), self._sync_call_app_action)



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
