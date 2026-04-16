from __future__ import annotations

from io import BytesIO

from PIL import Image

from custom_components.dreame_lawn_mower.binary_sensor import (
    DreameBinarySensorDescription,
)
from custom_components.dreame_lawn_mower.dreame_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.image import png_bytes_to_jpeg
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


def test_client_device_property_defaults_to_none() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._device = None

    assert client.device is None


def test_png_bytes_to_jpeg_returns_jpeg_bytes() -> None:
    source = BytesIO()
    Image.new("RGBA", (8, 8), (0, 128, 0, 255)).save(source, format="PNG")

    converted = png_bytes_to_jpeg(source.getvalue())

    assert converted.startswith(b"\xff\xd8")
