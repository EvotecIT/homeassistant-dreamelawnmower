from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageFont

from custom_components.dreame_lawn_mower import image as image_helpers
from custom_components.dreame_lawn_mower.binary_sensor import (
    DreameBinarySensorDescription,
)
from custom_components.dreame_lawn_mower.button import (
    DreameLawnMowerCaptureScheduleProbeButton,
    schedule_probe_payload,
)
from custom_components.dreame_lawn_mower.dreame_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.image import (
    map_diagnostics_jpeg,
    map_placeholder_jpeg,
    png_bytes_to_jpeg,
)
from custom_components.dreame_lawn_mower.sensor import DreameSensorDescription


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
