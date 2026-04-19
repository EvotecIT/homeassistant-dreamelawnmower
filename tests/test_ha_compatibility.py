from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageFont

from custom_components.dreame_lawn_mower import image as image_helpers
from custom_components.dreame_lawn_mower.binary_sensor import (
    DreameBinarySensorDescription,
)
from custom_components.dreame_lawn_mower.button import (
    DreameLawnMowerCapturePreferenceProbeButton,
    DreameLawnMowerCaptureScheduleProbeButton,
    DreameLawnMowerCaptureTaskStatusProbeButton,
    DreameLawnMowerCaptureWeatherProbeButton,
    schedule_probe_payload,
)
from custom_components.dreame_lawn_mower.dreame_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.image import (
    app_maps_contact_sheet_jpeg,
    map_diagnostics_jpeg,
    map_placeholder_jpeg,
    png_bytes_to_jpeg,
)
from custom_components.dreame_lawn_mower.sensor import (
    DreameLawnMowerLastPreferenceProbeSensor,
    DreameLawnMowerLastScheduleProbeSensor,
    DreameLawnMowerLastScheduleWriteSensor,
    DreameLawnMowerLastTaskStatusProbeSensor,
    DreameLawnMowerLastWeatherProbeSensor,
    DreameSensorDescription,
    preference_probe_result_attributes,
    schedule_probe_result_attributes,
    schedule_write_result_attributes,
    weather_probe_result_attributes,
)
from custom_components.dreame_lawn_mower.task_status_probe import (
    task_status_probe_payload,
    task_status_probe_result_attributes,
)


def test_sensor_description_exposes_ha_compat_fields() -> None:
    description = DreameSensorDescription(
        key="test",
        name="Test",
        value_fn=lambda _: None,
    )

    assert description.entity_registry_enabled_default is True
    assert description.entity_registry_visible_default is True
    assert description.translation_key is None
    assert description.translation_placeholders is None
    assert description.force_update is False
    assert description.unit_of_measurement is None
    assert description.suggested_unit_of_measurement is None
    assert description.suggested_display_precision is None
    assert description.state_class is None
    assert description.last_reset is None
    assert description.options is None


def test_binary_sensor_description_exposes_ha_compat_fields() -> None:
    description = DreameBinarySensorDescription(
        key="test",
        name="Test",
        value_fn=lambda _: None,
    )

    assert description.entity_registry_enabled_default is True
    assert description.entity_registry_visible_default is True
    assert description.translation_key is None
    assert description.translation_placeholders is None
    assert description.force_update is False
    assert description.unit_of_measurement is None


def test_schedule_probe_button_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerCaptureScheduleProbeButton.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerCaptureScheduleProbeButton.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_task_status_probe_button_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerCaptureTaskStatusProbeButton.__dict__[
            "__attr_entity_category"
        ]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerCaptureTaskStatusProbeButton.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_preference_probe_button_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerCapturePreferenceProbeButton.__dict__[
            "__attr_entity_category"
        ]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerCapturePreferenceProbeButton.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_weather_probe_button_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerCaptureWeatherProbeButton.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerCaptureWeatherProbeButton.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_last_schedule_write_sensor_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerLastScheduleWriteSensor.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerLastScheduleWriteSensor.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_last_schedule_probe_sensor_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerLastScheduleProbeSensor.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerLastScheduleProbeSensor.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_last_preference_probe_sensor_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerLastPreferenceProbeSensor.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerLastPreferenceProbeSensor.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_last_task_status_probe_sensor_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerLastTaskStatusProbeSensor.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerLastTaskStatusProbeSensor.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_last_weather_probe_sensor_is_diagnostic_disabled_by_default() -> None:
    assert (
        DreameLawnMowerLastWeatherProbeSensor.__dict__["__attr_entity_category"]
        == "diagnostic"
    )
    assert (
        DreameLawnMowerLastWeatherProbeSensor.__dict__[
            "__attr_entity_registry_enabled_default"
        ]
        is False
    )


def test_task_status_probe_payload_keeps_compact_app_state() -> None:
    scan = {
        "entries": [
            {
                "key": "2.1",
                "value": "6",
                "decoded_label": "Charging",
                "state_key": "charging",
            },
            {
                "key": "2.2",
                "value": "54",
                "decoded_label": "Edge",
                "decoded_label_source": "bundled_mower_errors",
            },
            {
                "key": "2.50",
                "task_status": {
                    "type": "TASK",
                    "executing": True,
                    "status": True,
                    "operation": 6,
                },
            },
            {"key": "2.51", "value": {"time": "1776587727", "tz": "Europe/Warsaw"}},
            {"key": "3.1", "value": "77"},
            {"key": "5.104", "value": "3"},
            {"key": "5.105", "value": "1"},
            {"key": "5.106", "value": "1"},
            {"key": "5.107", "value": "90"},
        ],
        "summary": {"unknown_non_empty_keys": ["5.104", "5.105", "5.106", "5.107"]},
    }

    payload = task_status_probe_payload(
        scan,
        captured_at="2026-04-19T15:00:00+00:00",
    )

    assert payload["source"] == "cloud_property_task_status"
    assert payload["available"] is True
    assert payload["entry_count"] == 9
    assert payload["summary"] == {
        "state": {"value": "6", "label": "Charging", "state_key": "charging"},
        "task_status": {
            "type": "TASK",
            "executing": True,
            "status": True,
            "operation": 6,
        },
        "error": {
            "value": "54",
            "label": "Edge",
            "label_source": "bundled_mower_errors",
            "active": True,
        },
        "error_active": True,
        "battery_level": "77",
        "device_time": {"time": "1776587727", "tz": "Europe/Warsaw"},
        "service_5_latest": {
            "5.104": "3",
            "5.105": "1",
            "5.106": "1",
            "5.107": "90",
        },
        "unknown_non_empty_keys": ["5.104", "5.105", "5.106", "5.107"],
    }
    assert task_status_probe_result_attributes(payload) == {
        "captured_at": "2026-04-19T15:00:00+00:00",
        "source": "cloud_property_task_status",
        "available": True,
        "keys": [
            "2.1",
            "2.2",
            "2.50",
            "2.51",
            "3.1",
            "5.104",
            "5.105",
            "5.106",
            "5.107",
        ],
        "entry_count": 9,
        "state": {"value": "6", "label": "Charging", "state_key": "charging"},
        "task_status": {
            "type": "TASK",
            "executing": True,
            "status": True,
            "operation": 6,
        },
        "error": {
            "value": "54",
            "label": "Edge",
            "label_source": "bundled_mower_errors",
            "active": True,
        },
        "error_active": True,
        "battery_level": "77",
        "device_time": {"time": "1776587727", "tz": "Europe/Warsaw"},
        "service_5_latest": {
            "5.104": "3",
            "5.105": "1",
            "5.106": "1",
            "5.107": "90",
        },
        "unknown_non_empty_keys": ["5.104", "5.105", "5.106", "5.107"],
        "error_count": 0,
    }


def test_weather_probe_result_attributes_are_compact() -> None:
    result = {
        "captured_at": "2026-04-19T14:30:00+02:00",
        "source": "app_action_weather_protection",
        "available": True,
        "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
        "present_config_keys": ["WRP"],
        "weather_switch_enabled": True,
        "rain_protection_enabled": True,
        "rain_protection_duration_hours": 8,
        "rain_sensor_sensitivity": 0,
        "rain_protect_end_time": 1776600300,
        "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
        "rain_protect_end_time_present": True,
        "rain_protection_active": True,
        "rain_protection_raw": [1, 8, 0],
        "config_keys": ["WRF", "WRP"],
        "raw_config": {"secret-ish": "not included"},
        "errors": [],
        "warnings": [{"stage": "rain_end_time", "warning": "not protecting"}],
    }

    assert weather_probe_result_attributes(result) == {
        "captured_at": "2026-04-19T14:30:00+02:00",
        "source": "app_action_weather_protection",
        "available": True,
        "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
        "present_config_keys": ["WRP"],
        "weather_switch_enabled": True,
        "rain_protection_enabled": True,
        "rain_protection_duration_hours": 8,
        "rain_sensor_sensitivity": 0,
        "rain_protect_end_time": 1776600300,
        "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
        "rain_protect_end_time_present": True,
        "rain_protection_active": True,
        "rain_protection_raw": [1, 8, 0],
        "error_count": 0,
        "warning_count": 1,
        "warnings": [{"stage": "rain_end_time", "warning": "not protecting"}],
    }


def test_preference_probe_result_attributes_are_compact() -> None:
    result = {
        "captured_at": "2026-04-19T10:00:00+00:00",
        "source": "app_action_mowing_preferences",
        "available": True,
        "property_hint": "2.52",
        "maps": [
            {
                "idx": 0,
                "label": "map_0",
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [
                    {
                        "area_id": 11,
                        "reported_version": 8,
                        "version": 8,
                        "efficient_mode_name": "efficient",
                        "mowing_height_cm": 4.0,
                        "mowing_direction_mode_name": "checkerboard",
                        "mowing_direction_degrees": 90,
                        "edge_mowing_auto": True,
                        "edge_mowing_safe": True,
                        "obstacle_avoidance_enabled": True,
                        "obstacle_avoidance_height_cm": 15,
                        "obstacle_avoidance_distance_cm": 20,
                        "obstacle_avoidance_ai_classes": [
                            "people",
                            "animals",
                            "objects",
                        ],
                        "raw_payload": [8, 0, 11],
                    }
                ],
            }
        ],
        "errors": [],
    }

    assert preference_probe_result_attributes(result) == {
        "captured_at": "2026-04-19T10:00:00+00:00",
        "source": "app_action_mowing_preferences",
        "available": True,
        "property_hint": "2.52",
        "map_count": 1,
        "maps": [
            {
                "idx": 0,
                "label": "map_0",
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preference_count": 1,
                "preferences": [
                    {
                        "area_id": 11,
                        "reported_version": 8,
                        "version": 8,
                        "efficient_mode_name": "efficient",
                        "mowing_height_cm": 4.0,
                        "mowing_direction_mode_name": "checkerboard",
                        "mowing_direction_degrees": 90,
                        "edge_mowing_auto": True,
                        "edge_mowing_safe": True,
                        "obstacle_avoidance_enabled": True,
                        "obstacle_avoidance_height_cm": 15,
                        "obstacle_avoidance_distance_cm": 20,
                        "obstacle_avoidance_ai_classes": [
                            "people",
                            "animals",
                            "objects",
                        ],
                    }
                ],
            }
        ],
        "error_count": 0,
    }


def test_schedule_probe_result_attributes_are_compact() -> None:
    result = {
        "captured_at": "2026-04-19T10:00:00+00:00",
        "source": "app_action_schedule",
        "available": True,
        "current_task": {"start_time": "10:58", "version": 19383},
        "schedule_selection": {
            "active_version": 19383,
            "included_schedule_count": 1,
            "hidden_schedule_count": 1,
        },
        "schedules": [
            {
                "idx": 0,
                "label": "map_0",
                "available": True,
                "version": 19383,
                "plan_count": 2,
                "enabled_plan_count": 1,
                "plans": [{"plan_id": 0}],
            },
            {
                "idx": 1,
                "label": "map_1",
                "available": False,
                "version": 4760,
                "error": "hash mismatch",
            },
        ],
        "errors": [],
    }

    assert schedule_probe_result_attributes(result) == {
        "captured_at": "2026-04-19T10:00:00+00:00",
        "source": "app_action_schedule",
        "available": True,
        "current_task": {"start_time": "10:58", "version": 19383},
        "schedule_selection": {
            "active_version": 19383,
            "included_schedule_count": 1,
            "hidden_schedule_count": 1,
        },
        "schedule_count": 2,
        "schedules": [
            {
                "idx": 0,
                "label": "map_0",
                "available": True,
                "version": 19383,
                "plan_count": 2,
                "enabled_plan_count": 1,
            },
            {
                "idx": 1,
                "label": "map_1",
                "available": False,
                "version": 4760,
                "error": "hash mismatch",
            },
        ],
        "error_count": 0,
    }


def test_schedule_write_result_attributes_are_compact() -> None:
    result = {
        "source": "app_action_schedule_write",
        "action": "set_schedule_plan_enabled",
        "dry_run": True,
        "executed": False,
        "changed": True,
        "map_index": 0,
        "plan_id": 0,
        "previous_enabled": True,
        "enabled": False,
        "version": 19383,
        "request": {"m": "s", "t": "SCHDSV2", "d": {"i": 0, "v": 19383}},
        "schedule": {"label": "map_0", "version": 19383},
        "target_plan": {
            "plan_id": 0,
            "enabled": False,
            "first_start_time": "10:58",
            "first_end_time": "20:57",
        },
        "response_data": None,
        "ignored": None,
    }

    assert schedule_write_result_attributes(result) == {
        "source": "app_action_schedule_write",
        "action": "set_schedule_plan_enabled",
        "dry_run": True,
        "executed": False,
        "changed": True,
        "map_index": 0,
        "plan_id": 0,
        "previous_enabled": True,
        "enabled": False,
        "version": 19383,
        "request": {"m": "s", "t": "SCHDSV2", "d": {"i": 0, "v": 19383}},
        "schedule": {"label": "map_0", "version": 19383},
        "target_plan": {
            "plan_id": 0,
            "enabled": False,
            "first_start_time": "10:58",
            "first_end_time": "20:57",
        },
    }


def test_schedule_write_result_attributes_keep_response_data() -> None:
    assert schedule_write_result_attributes(
        {
            "executed": True,
            "response_data": {"r": 0, "v": 19383},
            "request": {"t": "SCHDSV2"},
        }
    ) == {
        "executed": True,
        "request": {"t": "SCHDSV2"},
        "response_data": {"r": 0, "v": 19383},
    }


def test_schedule_probe_payload_includes_calendar_selection() -> None:
    payload = {
        "current_task": {"version": 19383},
        "schedules": [
            {"idx": -1, "version": 31345, "enabled_plan_count": 1},
            {"idx": 0, "version": 19383, "enabled_plan_count": 1},
        ],
    }

    enriched = schedule_probe_payload(payload)

    assert enriched["schedule_selection"] == {
        "mode": "active_schedule",
        "active_version": 19383,
        "active_version_filter_applied": True,
        "included_schedule_count": 1,
        "hidden_schedule_count": 1,
        "included_schedules": [
            {
                "idx": 0,
                "label": "map 0",
                "version": 19383,
                "enabled_plan_count": 1,
            }
        ],
        "hidden_schedules": [
            {
                "idx": -1,
                "label": "default schedule",
                "version": 31345,
                "enabled_plan_count": 1,
            }
        ],
    }
    assert payload.get("schedule_selection") is None


def test_client_device_property_defaults_to_none() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._device = None

    assert client.device is None


def test_map_fonts_use_bundled_bytes(monkeypatch) -> None:
    image_helpers._font.cache_clear()
    image_helpers._font_bytes.cache_clear()
    original = ImageFont.truetype
    calls: list[Any] = []

    def spy_truetype(font, *args, **kwargs):
        calls.append(font)
        return original(font, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", spy_truetype)

    image_helpers._font(18)

    assert calls
    assert all(hasattr(font, "read") for font in calls)


def test_png_bytes_to_jpeg_returns_jpeg_bytes() -> None:
    source = BytesIO()
    Image.new("RGBA", (8, 8), (0, 128, 0, 255)).save(source, format="PNG")

    converted = png_bytes_to_jpeg(source.getvalue())

    assert converted.startswith(b"\xff\xd8")


def test_map_placeholder_jpeg_returns_valid_jpeg_bytes() -> None:
    converted = map_placeholder_jpeg(detail="test")

    assert converted.startswith(b"\xff\xd8")
    with Image.open(BytesIO(converted)) as image:
        assert image.format == "JPEG"
        assert image.size == (1024, 768)


def test_map_diagnostics_jpeg_returns_valid_jpeg_bytes() -> None:
    converted = map_diagnostics_jpeg(lines=["Source: legacy_current_map"])

    assert converted.startswith(b"\xff\xd8")
    with Image.open(BytesIO(converted)) as image:
        assert image.format == "JPEG"
        assert image.size == (1280, 720)


def test_app_maps_contact_sheet_jpeg_returns_valid_jpeg_bytes() -> None:
    map_image = Image.new("RGB", (120, 160), (240, 250, 245))
    buffer = BytesIO()
    map_image.save(buffer, format="PNG")

    converted = app_maps_contact_sheet_jpeg(
        maps=[
            {
                "idx": 0,
                "current": True,
                "image_png": buffer.getvalue(),
                "summary": {
                    "map_area_count": 1,
                    "spot_count": 0,
                    "trajectory_point_count": 2,
                },
            }
        ],
        map_count=1,
        current_map_index=0,
    )

    assert converted.startswith(b"\xff\xd8")
    with Image.open(BytesIO(converted)) as image:
        assert image.format == "JPEG"
        assert image.size[0] == 1280
        assert image.size[1] >= 720
