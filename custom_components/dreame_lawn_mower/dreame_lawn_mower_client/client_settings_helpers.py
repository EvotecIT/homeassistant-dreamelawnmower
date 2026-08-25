"""Schedule, preference, weather, and voice helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .client_constants import (
    VOICE_LANGUAGE_INDEX_TO_CODE,
    VOICE_LANGUAGE_INDEX_TO_LABEL,
    VOICE_PROMPT_FIELDS,
)
from .client_shared_helpers import _epoch_to_iso, _positive_int, _setting_bool
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .payload_utils import (
    _as_optional_text,
    _json_safe,
)


def _dedupe_ints(values: Sequence[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        parsed = _positive_int(value)
        if parsed is None and value != -1:
            continue
        parsed = -1 if value == -1 else parsed
        if parsed not in result:
            result.append(parsed)
    return result


def _schedule_entry_overview(schedule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": schedule.get("idx"),
        "label": schedule.get("label"),
        "available": schedule.get("available"),
        "version": schedule.get("version"),
        "plan_count": schedule.get("plan_count"),
        "enabled_plan_count": schedule.get("enabled_plan_count"),
    }


def _schedule_plan_overview(
    plans: Sequence[Mapping[str, Any]],
    *,
    plan_id: int,
    previous_enabled: bool | None,
    enabled: bool,
) -> dict[str, Any]:
    for plan in plans:
        if _positive_int(plan.get("plan_id")) != plan_id:
            continue

        weeks = plan.get("weeks")
        week_items: list[Mapping[str, Any]] = []
        if isinstance(weeks, Sequence) and not isinstance(
            weeks,
            str | bytes | bytearray,
        ):
            week_items = [week for week in weeks if isinstance(week, Mapping)]
        tasks = [task for week in week_items for task in _schedule_week_tasks(week)]
        type_names = sorted(
            {
                str(task["type_name"])
                for task in tasks
                if task.get("type_name") is not None
            }
        )
        first_task = tasks[0] if tasks else {}
        return {
            "plan_id": plan_id,
            "name": plan.get("name"),
            "previous_enabled": previous_enabled,
            "enabled": enabled,
            "week_count": len(week_items),
            "task_count": len(tasks),
            "first_start_time": first_task.get("start_time"),
            "first_end_time": first_task.get("end_time"),
            "type_names": type_names,
        }

    return {
        "plan_id": plan_id,
        "previous_enabled": previous_enabled,
        "enabled": enabled,
    }


def _schedule_upload_overview(
    plans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    plan_ids: list[int] = []
    week_count = 0
    task_count = 0
    enabled_plan_count = 0
    for plan in plans:
        plan_id = _positive_int(plan.get("plan_id"))
        if plan_id is not None:
            plan_ids.append(plan_id)
        if plan.get("enabled"):
            enabled_plan_count += 1
        weeks = plan.get("weeks")
        if not isinstance(weeks, Sequence) or isinstance(
            weeks,
            str | bytes | bytearray,
        ):
            continue
        week_count += len(weeks)
        for week in weeks:
            if not isinstance(week, Mapping):
                continue
            tasks = week.get("tasks")
            if isinstance(tasks, Sequence) and not isinstance(
                tasks,
                str | bytes | bytearray,
            ):
                task_count += len(tasks)
    return {
        "plan_count": len(plans),
        "enabled_plan_count": enabled_plan_count,
        "week_count": week_count,
        "task_count": task_count,
        "plan_ids": plan_ids,
    }


def _mowing_preference_map_overview(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": entry.get("idx"),
        "label": entry.get("label"),
        "available": entry.get("available"),
        "mode": entry.get("mode"),
        "mode_name": entry.get("mode_name"),
        "area_count": entry.get("area_count"),
        "preference_count": len(
            [item for item in entry.get("preferences", []) if isinstance(item, Mapping)]
        ),
    }


def _mowing_preference_overview(preference: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reported_version": preference.get("reported_version"),
        "version": preference.get("version"),
        "map_index": preference.get("map_index"),
        "area_id": preference.get("area_id"),
        "efficient_mode": preference.get("efficient_mode"),
        "efficient_mode_name": preference.get("efficient_mode_name"),
        "mowing_height_cm": preference.get("mowing_height_cm"),
        "mowing_direction_mode": preference.get("mowing_direction_mode"),
        "mowing_direction_mode_name": preference.get("mowing_direction_mode_name"),
        "mowing_direction_method_name": preference.get(
            "mowing_direction_method_name"
        ),
        "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
        "edge_mowing_auto": preference.get("edge_mowing_auto"),
        "edge_mowing_walk_mode": preference.get("edge_mowing_walk_mode"),
        "edge_mowing_walk_mode_name": preference.get("edge_mowing_walk_mode_name"),
        "turning_method_name": preference.get("turning_method_name"),
        "edge_mowing_obstacle_avoidance": preference.get(
            "edge_mowing_obstacle_avoidance"
        ),
        "cutter_position": preference.get("cutter_position"),
        "cutter_position_name": preference.get("cutter_position_name"),
        "edge_mowing_num": preference.get("edge_mowing_num"),
        "obstacle_avoidance_enabled": preference.get("obstacle_avoidance_enabled"),
        "obstacle_avoidance_height_cm": preference.get("obstacle_avoidance_height_cm"),
        "obstacle_avoidance_distance_cm": preference.get(
            "obstacle_avoidance_distance_cm"
        ),
        "obstacle_avoidance_ai": preference.get("obstacle_avoidance_ai"),
        "obstacle_avoidance_ai_classes": preference.get(
            "obstacle_avoidance_ai_classes"
        ),
        "edge_mowing_safe": preference.get("edge_mowing_safe"),
        "obstacle_avoidance_sensitivity": preference.get(
            "obstacle_avoidance_sensitivity"
        ),
        "edge_cutting_attachment": preference.get("edge_cutting_attachment"),
        "steering_mode": preference.get("steering_mode"),
        "cutter_position_height": preference.get("cutter_position_height"),
    }


def _schedule_week_tasks(week: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tasks = week.get("tasks")
    if not isinstance(tasks, Sequence) or isinstance(
        tasks,
        str | bytes | bytearray,
    ):
        return []
    return [task for task in tasks if isinstance(task, Mapping)]


def _batch_schedule_keys() -> list[str]:
    return [*(f"SCHEDULE.{index}" for index in range(10)), "SCHEDULE.info"]


def _batch_settings_keys() -> list[str]:
    return [*(f"SETTINGS.{index}" for index in range(10)), "SETTINGS.info"]


def _batch_ota_keys() -> list[str]:
    return [
        *(f"OTA_INFO.{index}" for index in range(4)),
        "OTA_INFO.info",
        "prop.s_auto_upgrade",
    ]


def _debug_ota_model_name(model_name: Any) -> str | None:
    text = _as_optional_text(model_name)
    if not text:
        return None
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _weather_protection_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    weather_switch = _setting_bool(config.get("WRF"))
    if weather_switch is not None:
        summary["weather_switch_enabled"] = weather_switch

    wrp = config.get("WRP")
    if isinstance(wrp, Sequence) and not isinstance(wrp, str | bytes | bytearray):
        values = list(wrp)
        if len(values) == 2:
            values.append(0)
        summary["rain_protection_raw"] = _json_safe(values, max_depth=2)
        if values:
            summary["rain_protection_enabled"] = _setting_bool(values[0])
        if len(values) > 1:
            summary["rain_protection_duration_hours"] = _positive_int(values[1])
        if len(values) > 2:
            summary["rain_sensor_sensitivity"] = _positive_int(values[2])

    end_time = config.get("rainProtectEndTime")
    if end_time is not None:
        summary["rain_protect_end_time"] = end_time
        summary["rain_protect_end_time_present"] = True
    return {key: value for key, value in summary.items() if value is not None}


def _weather_protection_active_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    end_time = _rain_protect_end_time_timestamp(result.get("rain_protect_end_time"))
    end_time_present = bool(result.get("rain_protect_end_time_present"))

    if end_time is not None:
        summary["rain_protection_active"] = True
        end_time_iso = _epoch_to_iso(end_time)
        if end_time_iso is not None:
            summary["rain_protect_end_time_iso"] = end_time_iso
    elif end_time_present or result.get("available"):
        summary["rain_protection_active"] = False

    return summary


def _rain_protect_end_time_timestamp(value: Any) -> int | None:
    """Return a future rain-protection end timestamp, ignoring empty sentinels."""
    parsed = _positive_int(value)
    if parsed is None or parsed <= 0:
        return None
    timestamp = parsed / 1000 if parsed > 10_000_000_000 else parsed
    return parsed if timestamp > datetime.now(UTC).timestamp() else None


def _voice_settings_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    language = config.get("LANG")
    if isinstance(language, Sequence) and not isinstance(
        language,
        str | bytes | bytearray,
    ):
        values = list(language)
        text_language_index = _as_optional_int(values[0]) if len(values) > 0 else None
        voice_language_index = _as_optional_int(values[1]) if len(values) > 1 else None
        summary["text_language_index"] = text_language_index
        summary["voice_language_index"] = voice_language_index
        summary["voice_language_name"] = VOICE_LANGUAGE_INDEX_TO_LABEL.get(
            voice_language_index
        )
        summary["voice_language_code"] = VOICE_LANGUAGE_INDEX_TO_CODE.get(
            voice_language_index
        )

    volume = _as_optional_int(config.get("VOL"))
    if volume is not None:
        summary["volume"] = volume

    if "VOICE" in config:
        voice_prompts = _normalize_voice_prompt_flags(config.get("VOICE"))
        summary["voice_prompts"] = voice_prompts
        for field_name, enabled in zip(VOICE_PROMPT_FIELDS, voice_prompts, strict=True):
            summary[field_name] = bool(enabled)

    return summary


def _as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_voice_prompt_flags(value: Any) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return [0, 0, 0, 0]
    normalized: list[int] = []
    for item in list(value)[:4]:
        normalized.append(1 if bool(item) else 0)
    if len(normalized) < 4:
        normalized.extend([0] * (4 - len(normalized)))
    return normalized
