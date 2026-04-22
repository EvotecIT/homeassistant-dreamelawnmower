"""Binary sensors for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    DOMAIN,
)
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .manual_control import remote_control_state_safe
from .sensor import (
    _current_vector_map_summary,
    batch_ota_attributes,
    current_vector_map_attributes,
    weather_probe_result_attributes,
)


@dataclass(frozen=True, slots=True)
class DreameBinarySensorDescription:
    """Simple metadata for a mower binary sensor."""

    key: str
    name: str
    value_fn: Callable[[Any], bool | None]
    exists_fn: Callable[[Any], bool] = lambda _: True
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    force_update: bool = False
    device_class: BinarySensorDeviceClass | None = None
    unit_of_measurement: str | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None


def _raw_flag(snapshot: Any, key: str) -> bool | None:
    """Return a raw boolean flag from the mower attributes."""
    value = snapshot.raw_attributes.get(key)
    if value is None:
        return None
    return bool(value)


def _raw_docked(snapshot: Any) -> bool | None:
    """Return the vendor-reported dock flag when known."""
    return getattr(snapshot, "raw_docked", None)


def _raw_charging(snapshot: Any) -> bool | None:
    """Return the vendor-reported charging flag when known."""
    return getattr(snapshot, "raw_charging", None)


def _raw_started(snapshot: Any) -> bool | None:
    """Return the vendor-reported started flag when known."""
    return getattr(snapshot, "raw_started", None)


def _raw_returning(snapshot: Any) -> bool | None:
    """Return the vendor-reported returning flag when known."""
    value = getattr(snapshot, "raw_returning", None)
    return _raw_flag(snapshot, "returning") if value is None else value


BINARY_SENSORS = [
    DreameBinarySensorDescription(
        key="error_active",
        name="Error Active",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_ERROR,
        icon="mdi:alert-circle-outline",
    ),
    DreameBinarySensorDescription(
        key="online",
        name="Online",
        value_fn=lambda snapshot: snapshot.online
        if snapshot.online is not None
        else snapshot.available,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="device_connected",
        name="Device Connected",
        value_fn=lambda snapshot: getattr(snapshot, "device_connected", None),
        exists_fn=lambda snapshot: (
            getattr(snapshot, "device_connected", None) is not None
        ),
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="cloud_connected",
        name="Cloud Connected",
        value_fn=lambda snapshot: getattr(snapshot, "cloud_connected", None),
        exists_fn=lambda snapshot: getattr(snapshot, "cloud_connected", None)
        is not None,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="charging",
        name="Charging",
        value_fn=lambda snapshot: snapshot.charging,
        icon="mdi:battery-charging",
    ),
    DreameBinarySensorDescription(
        key="raw_charging",
        name="Raw Charging Flag",
        value_fn=_raw_charging,
        exists_fn=lambda snapshot: _raw_charging(snapshot) is not None,
        icon="mdi:battery-charging",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="docked",
        name="Docked",
        value_fn=lambda snapshot: snapshot.docked,
        icon="mdi:home-map-marker",
    ),
    DreameBinarySensorDescription(
        key="raw_docked",
        name="Raw Docked Flag",
        value_fn=_raw_docked,
        exists_fn=lambda snapshot: _raw_docked(snapshot) is not None,
        icon="mdi:home-map-marker",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="paused",
        name="Paused",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_PAUSED,
        icon="mdi:pause-circle-outline",
    ),
    DreameBinarySensorDescription(
        key="mowing",
        name="Mowing",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_MOWING,
        icon="mdi:robot-mower-outline",
    ),
    DreameBinarySensorDescription(
        key="returning",
        name="Returning",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_RETURNING,
        icon="mdi:home-import-outline",
    ),
    DreameBinarySensorDescription(
        key="task_active",
        name="Task Active",
        value_fn=lambda snapshot: snapshot.started,
        icon="mdi:play-circle-outline",
    ),
    DreameBinarySensorDescription(
        key="raw_started",
        name="Raw Started Flag",
        value_fn=_raw_started,
        exists_fn=lambda snapshot: _raw_started(snapshot) is not None,
        icon="mdi:play-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="mapping_available",
        name="Mapping Available",
        value_fn=lambda snapshot: snapshot.mapping_available,
        exists_fn=lambda snapshot: snapshot.mapping_available
        or "map" in snapshot.capabilities,
        icon="mdi:map-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="scheduled_task",
        name="Scheduled Task",
        value_fn=lambda snapshot: snapshot.scheduled_clean,
        icon="mdi:calendar-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="shortcut_task",
        name="Shortcut Task",
        value_fn=lambda snapshot: snapshot.shortcut_task,
        exists_fn=lambda snapshot: snapshot.shortcut_task
        or "shortcuts" in snapshot.capabilities,
        icon="mdi:flash-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="manual_drive_safe",
        name="Manual Drive Safe",
        value_fn=remote_control_state_safe,
        icon="mdi:shield-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="child_lock",
        name="Child Lock",
        value_fn=lambda snapshot: getattr(snapshot, "child_lock", None),
        exists_fn=lambda snapshot: getattr(snapshot, "child_lock", None) is not None,
        icon="mdi:lock-outline",
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="raw_paused",
        name="Raw Paused Flag",
        value_fn=lambda snapshot: _raw_flag(snapshot, "paused"),
        exists_fn=lambda snapshot: "paused" in snapshot.raw_attributes,
        icon="mdi:pause-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="raw_running",
        name="Raw Running Flag",
        value_fn=lambda snapshot: _raw_flag(snapshot, "running"),
        exists_fn=lambda snapshot: "running" in snapshot.raw_attributes,
        icon="mdi:play-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="raw_returning",
        name="Raw Returning Flag",
        value_fn=_raw_returning,
        exists_fn=lambda snapshot: _raw_returning(snapshot) is not None,
        icon="mdi:home-import-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower binary sensors."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerBinarySensor(coordinator, description)
            for description in BINARY_SENSORS
        ]
        + [DreameLawnMowerBluetoothConnectedBinarySensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapLivePathBinarySensor(coordinator)]
        + [DreameLawnMowerFirmwareUpdateAvailableBinarySensor(coordinator)]
        + [DreameLawnMowerAutomaticFirmwareUpdatesBinarySensor(coordinator)]
        + [DreameLawnMowerRainProtectionEnabledBinarySensor(coordinator)]
        + [DreameLawnMowerRainDelayActiveBinarySensor(coordinator)]
    )


class DreameLawnMowerBinarySensor(DreameLawnMowerEntity, BinarySensorEntity):
    """Coordinator-backed binary sensor."""

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: DreameBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._descriptor.unique_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor value."""
        if not self.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return whether the binary sensor currently has meaningful mower data."""
        snapshot = self.coordinator.data
        return snapshot is not None and self.entity_description.exists_fn(snapshot)


class DreameLawnMowerFirmwareUpdateAvailableBinarySensor(
    DreameLawnMowerEntity,
    BinarySensorEntity,
):
    """Expose whether cached batch OTA data reports an available update."""

    _attr_name = "Firmware Update Available"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_firmware_update_available"
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether an update is currently reported as available."""
        ota = _batch_ota_section(self.coordinator.batch_device_data)
        if ota is None:
            return None
        value = ota.get("update_available")
        return value if isinstance(value, bool) else None

    @property
    def available(self) -> bool:
        """Return whether cached OTA data exists."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached OTA attributes."""
        return batch_ota_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerBluetoothConnectedBinarySensor(
    DreameLawnMowerEntity,
    BinarySensorEntity,
):
    """Expose whether the cloud currently reports an active Bluetooth session."""

    _attr_name = "Bluetooth Connected"
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_bluetooth_connected"

    @property
    def is_on(self) -> bool | None:
        """Return whether Bluetooth is currently reported as connected."""
        return getattr(self.coordinator, "bluetooth_connected", None)

    @property
    def available(self) -> bool:
        """Return whether cached Bluetooth state exists."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe Bluetooth diagnostic attributes."""
        return {"property_key": "1.53", "source": "cloud_property_scan"}


class DreameLawnMowerCurrentAppMapLivePathBinarySensor(
    DreameLawnMowerEntity,
    BinarySensorEntity,
):
    """Expose whether the current vector map includes a live mow path."""

    _attr_name = "Current App Map Live Path"
    _attr_icon = "mdi:map-marker-path"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_live_path"
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether the current vector map has a live path."""
        summary = _current_vector_map_summary(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if not isinstance(summary, dict):
            return None
        value = summary.get("has_live_path")
        return value if isinstance(value, bool) else None

    @property
    def available(self) -> bool:
        """Return whether current-map vector map metadata exists."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerAutomaticFirmwareUpdatesBinarySensor(
    DreameLawnMowerEntity,
    BinarySensorEntity,
):
    """Expose the cached auto-upgrade switch state."""

    _attr_name = "Automatic Firmware Updates"
    _attr_icon = "mdi:update-auto"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_automatic_firmware_updates"
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether automatic firmware updates are enabled."""
        ota = _batch_ota_section(self.coordinator.batch_device_data)
        if ota is None:
            return None
        value = ota.get("auto_upgrade_enabled")
        return value if isinstance(value, bool) else None

    @property
    def available(self) -> bool:
        """Return whether cached OTA data exists."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached OTA attributes."""
        return batch_ota_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerRainProtectionEnabledBinarySensor(
    DreameLawnMowerEntity,
    BinarySensorEntity,
):
    """Expose whether rain protection is configured as enabled."""

    _attr_name = "Rain Protection Enabled"
    _attr_icon = "mdi:weather-rainy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_rain_protection_enabled"
        )

    @property
    def is_on(self) -> bool | None:
        """Return whether rain protection is configured as enabled."""
        value = _weather_flag(
            self.coordinator.weather_protection,
            "rain_protection_enabled",
        )
        return value

    @property
    def available(self) -> bool:
        """Return whether cached weather state exists."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerRainDelayActiveBinarySensor(
    DreameLawnMowerEntity,
    BinarySensorEntity,
):
    """Expose whether an active rain-delay window is currently reported."""

    _attr_name = "Rain Delay Active"
    _attr_icon = "mdi:weather-pouring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_delay_active"

    @property
    def is_on(self) -> bool | None:
        """Return whether an active rain-delay window is currently reported."""
        return _weather_flag(
            self.coordinator.weather_protection,
            "rain_protection_active",
        )

    @property
    def available(self) -> bool:
        """Return whether cached weather state exists."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


def _weather_flag(result: dict[str, Any] | None, key: str) -> bool | None:
    if not isinstance(result, dict):
        return None
    value = result.get(key)
    return value if isinstance(value, bool) else None


def _batch_ota_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("batch_ota_info") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None
