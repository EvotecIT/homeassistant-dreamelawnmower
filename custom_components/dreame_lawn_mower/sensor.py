"""Sensors for Dreame lawn mower."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control_options import (
    MOWING_ACTION_EDGE,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    contour_label,
    current_contour_entries,
    current_spot_entries,
    current_zone_entries,
    map_entries,
    mowing_action_label,
    spot_label,
    zone_label,
)
from .control_options import (
    current_map_index as selected_current_map_index,
)
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .manual_control import remote_control_block_reason
from .task_status_probe import (
    task_status_probe_result_attributes,
    task_status_probe_state,
)


@dataclass(frozen=True, slots=True)
class DreameSensorDescription:
    key: str
    name: str
    value_fn: Callable[[Any], Any]
    exists_fn: Callable[[Any], bool] = lambda _: True
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    force_update: bool = False
    device_class: SensorDeviceClass | None = None
    unit_of_measurement: str | None = None
    native_unit_of_measurement: str | None = None
    suggested_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    state_class: SensorStateClass | str | None = None
    last_reset: datetime | None = None
    options: list[str] | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None


def _raw_attribute(snapshot: Any, key: str) -> Any:
    """Return a raw mower attribute when available."""
    return snapshot.raw_attributes.get(key)


def _current_zone_label(snapshot: Any) -> str | None:
    """Return a friendly current-zone label for live mower state."""
    zone_name = getattr(snapshot, "current_zone_name", None)
    if isinstance(zone_name, str) and zone_name.strip():
        return zone_name.strip()

    zone_id = getattr(snapshot, "current_zone_id", None)
    if isinstance(zone_id, int):
        return f"Zone {zone_id}"
    return None


SENSORS = [
    DreameSensorDescription(
        key="activity",
        name="Activity",
        value_fn=lambda snapshot: snapshot.activity,
        icon="mdi:robot-mower",
    ),
    DreameSensorDescription(
        key="state_name",
        name="State Name",
        value_fn=lambda snapshot: snapshot.state_name,
        icon="mdi:state-machine",
    ),
    DreameSensorDescription(
        key="task_status",
        name="Task Status",
        value_fn=lambda snapshot: snapshot.task_status_name or "unknown",
        icon="mdi:clipboard-text-clock-outline",
    ),
    DreameSensorDescription(
        key="battery",
        name="Battery",
        value_fn=lambda snapshot: snapshot.battery_level,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
    ),
    DreameSensorDescription(
        key="error",
        name="Error",
        value_fn=lambda snapshot: snapshot.error_display or "none",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="error_code",
        name="Error Code",
        value_fn=lambda snapshot: (
            "none" if snapshot.error_code in (None, -1, 0) else snapshot.error_code
        ),
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="raw_error",
        name="Raw Error",
        value_fn=lambda snapshot: getattr(snapshot, "error_text", None) or "none",
        icon="mdi:text-box-search-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="firmware_version",
        name="Firmware Version",
        value_fn=lambda snapshot: snapshot.firmware_version,
        icon="mdi:package-up",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="hardware_version",
        name="Hardware Version",
        value_fn=lambda snapshot: snapshot.hardware_version,
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="serial_number",
        name="Serial Number",
        value_fn=lambda snapshot: snapshot.serial_number,
        exists_fn=lambda snapshot: bool(snapshot.serial_number),
        icon="mdi:barcode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="cloud_update_time",
        name="Cloud Update Time",
        value_fn=lambda snapshot: snapshot.cloud_update_time,
        exists_fn=lambda snapshot: bool(snapshot.cloud_update_time),
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="unknown_property_count",
        name="Unknown Property Count",
        value_fn=lambda snapshot: getattr(snapshot, "unknown_property_count", 0),
        icon="mdi:help-box-multiple-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="realtime_property_count",
        name="Realtime Property Count",
        value_fn=lambda snapshot: getattr(snapshot, "realtime_property_count", 0),
        icon="mdi:transit-connection-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="last_realtime_method",
        name="Last Realtime Method",
        value_fn=lambda snapshot: getattr(snapshot, "last_realtime_method", None),
        icon="mdi:message-badge-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="manual_drive_block_reason",
        name="Manual Drive Block Reason",
        value_fn=lambda snapshot: remote_control_block_reason(snapshot) or "none",
        icon="mdi:shield-alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="cleaning_mode",
        name="Cleaning Mode",
        value_fn=lambda snapshot: snapshot.cleaning_mode_name,
        exists_fn=lambda snapshot: (
            bool(snapshot.cleaning_mode_name)
            and snapshot.cleaning_mode_name != "unknown"
        ),
        icon="mdi:grass",
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="current_cleaned_area",
        name="Current Cleaned Area",
        value_fn=lambda snapshot: getattr(snapshot, "cleaned_area", None),
        exists_fn=lambda snapshot: getattr(snapshot, "cleaned_area", None) is not None,
        icon="mdi:texture-box",
        native_unit_of_measurement="m²",
    ),
    DreameSensorDescription(
        key="current_cleaning_time",
        name="Current Cleaning Time",
        value_fn=lambda snapshot: getattr(snapshot, "cleaning_time", None),
        exists_fn=lambda snapshot: getattr(snapshot, "cleaning_time", None) is not None,
        icon="mdi:timer-sand",
        native_unit_of_measurement="min",
    ),
    DreameSensorDescription(
        key="active_segment_count",
        name="Active Segment Count",
        value_fn=lambda snapshot: getattr(snapshot, "active_segment_count", None),
        exists_fn=lambda snapshot: (
            getattr(snapshot, "active_segment_count", None) is not None
        ),
        icon="mdi:vector-square",
    ),
    DreameSensorDescription(
        key="current_zone",
        name="Current Zone",
        value_fn=_current_zone_label,
        exists_fn=lambda snapshot: bool(_current_zone_label(snapshot)),
        icon="mdi:map-marker-outline",
    ),
    DreameSensorDescription(
        key="mower_state",
        name="Mower State",
        value_fn=lambda snapshot: _raw_attribute(snapshot, "mower_state"),
        exists_fn=lambda snapshot: bool(_raw_attribute(snapshot, "mower_state")),
        icon="mdi:robot-mower-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower sensors."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [DreameLawnMowerSensor(coordinator, description) for description in SENSORS]
        + [DreameLawnMowerAppMapCountSensor(coordinator)]
        + [DreameLawnMowerAvailableVectorMapCountSensor(coordinator)]
        + [DreameLawnMowerSelectedMowingActionSensor(coordinator)]
        + [DreameLawnMowerSelectedMapSensor(coordinator)]
        + [DreameLawnMowerSelectedMapPreferenceModeSensor(coordinator)]
        + [DreameLawnMowerSelectedMapPreferenceAreaCountSensor(coordinator)]
        + [DreameLawnMowerSelectedMapPreferenceCountSensor(coordinator)]
        + [DreameLawnMowerSelectedTargetSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneMowingHeightSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneEfficiencyModeSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneDirectionModeSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleAvoidanceSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleDistanceSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleHeightSensor(coordinator)]
        + [DreameLawnMowerSelectedZoneObstacleClassSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapIndexSensor(coordinator)]
        + [DreameLawnMowerCurrentVectorMapNameSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapAreaSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapZoneCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapSpotCountSensor(coordinator)]
        + [DreameLawnMowerCurrentVectorMapIdSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapEdgeCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapTrajectoryLengthSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapCutRelationCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapMowPathPointCountSensor(coordinator)]
        + [DreameLawnMowerCurrentAppMapMowPathLengthSensor(coordinator)]
        + [DreameLawnMowerRuntimeMissionProgressSensor(coordinator)]
        + [DreameLawnMowerRuntimeCurrentAreaSensor(coordinator)]
        + [DreameLawnMowerRuntimeTotalAreaSensor(coordinator)]
        + [DreameLawnMowerRuntimePositionXSensor(coordinator)]
        + [DreameLawnMowerRuntimePositionYSensor(coordinator)]
        + [DreameLawnMowerRuntimeHeadingSensor(coordinator)]
        + [DreameLawnMowerRuntimeTrackPointCountSensor(coordinator)]
        + [DreameLawnMowerRuntimeTrackLengthSensor(coordinator)]
        + [DreameLawnMowerRuntimeTrackSegmentCountSensor(coordinator)]
        + [DreameLawnMowerMowingProgressSensor(coordinator)]
        + [DreameLawnMowerAppMapObjectCountSensor(coordinator)]
        + [DreameLawnMowerFirmwareUpdateStatusSensor(coordinator)]
        + [DreameLawnMowerConfiguredScheduleCountSensor(coordinator)]
        + [DreameLawnMowerPreferenceMapCountSensor(coordinator)]
        + [DreameLawnMowerWeatherProtectionStatusSensor(coordinator)]
        + [DreameLawnMowerRainProtectionDurationSensor(coordinator)]
        + [DreameLawnMowerRainDelayEndTimeSensor(coordinator)]
        + [DreameLawnMowerLastPreferenceWriteSensor(coordinator)]
        + [DreameLawnMowerLastScheduleWriteSensor(coordinator)]
        + [DreameLawnMowerLastBatchDeviceDataProbeSensor(coordinator)]
        + [DreameLawnMowerLastScheduleProbeSensor(coordinator)]
        + [DreameLawnMowerLastTaskStatusProbeSensor(coordinator)]
        + [DreameLawnMowerLastPreferenceProbeSensor(coordinator)]
        + [DreameLawnMowerLastWeatherProbeSensor(coordinator)]
    )


class DreameLawnMowerSensor(DreameLawnMowerEntity, SensorEntity):
    """Simple coordinator-backed mower sensor."""

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: DreameSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._descriptor.unique_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if not self.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return whether the sensor currently has meaningful mower data."""
        snapshot = self.coordinator.data
        return snapshot is not None and self.entity_description.exists_fn(snapshot)


class DreameLawnMowerLastScheduleWriteSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last guarded schedule write or dry-run result."""

    _attr_name = "Last Schedule Write"
    _attr_icon = "mdi:calendar-edit"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_schedule_write"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last schedule write result."""
        return _schedule_write_state(self.coordinator.last_schedule_write_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last schedule write result."""
        return schedule_write_result_attributes(
            self.coordinator.last_schedule_write_result
        )


def _schedule_write_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    return "executed" if result.get("executed") else "dry_run"


def schedule_write_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a schedule write result."""
    if not result:
        return {}

    target_plan = result.get("target_plan")
    schedule = result.get("schedule")
    target_schedule = result.get("target_schedule")
    attributes: dict[str, Any] = {
        "source": result.get("source"),
        "action": result.get("action"),
        "dry_run": result.get("dry_run"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "map_index": result.get("map_index"),
        "plan_id": result.get("plan_id"),
        "previous_enabled": result.get("previous_enabled"),
        "enabled": result.get("enabled"),
        "version": result.get("version"),
        "chunk_size": result.get("chunk_size"),
        "chunk_count": result.get("chunk_count"),
        "payload_size": result.get("payload_size"),
        "request": result.get("request"),
    }
    if isinstance(schedule, dict):
        attributes["schedule"] = schedule
    if isinstance(target_plan, dict):
        attributes["target_plan"] = target_plan
    if isinstance(target_schedule, dict):
        attributes["target_schedule"] = target_schedule
    if result.get("response_data") is not None:
        attributes["response_data"] = result.get("response_data")
    return {key: value for key, value in attributes.items() if value is not None}


class DreameLawnMowerLastPreferenceWriteSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last mowing preference plan or executed write."""

    _attr_name = "Last Preference Write"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_preference_write"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last preference write result."""
        return _preference_write_state(self.coordinator.last_preference_write_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last preference write plan."""
        return preference_write_result_attributes(
            self.coordinator.last_preference_write_result
        )


def _preference_write_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    if result.get("executed"):
        return "executed"
    return "planned"


def preference_write_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a preference write plan."""
    if not result:
        return {}

    attributes: dict[str, Any] = {
        "source": result.get("source"),
        "action": result.get("action"),
        "dry_run": result.get("dry_run"),
        "executed": result.get("executed"),
        "execute_supported": result.get("execute_supported"),
        "request_verified": result.get("request_verified"),
        "map_index": result.get("map_index"),
        "area_id": result.get("area_id"),
        "mode": result.get("mode"),
        "mode_name": result.get("mode_name"),
        "target_mode": result.get("target_mode"),
        "target_mode_name": result.get("target_mode_name"),
        "mode_changed": result.get("mode_changed"),
        "changed": result.get("changed"),
        "changed_fields": result.get("changed_fields"),
        "changes": result.get("changes"),
        "payload": result.get("payload"),
        "request_candidate": result.get("request_candidate"),
        "request_candidates": result.get("request_candidates"),
        "write_commands": result.get("write_commands"),
        "notes": result.get("notes"),
    }
    if isinstance(result.get("map"), dict):
        attributes["map"] = result.get("map")
    if isinstance(result.get("previous_preference"), dict):
        attributes["previous_preference"] = result.get("previous_preference")
    if isinstance(result.get("updated_preference"), dict):
        attributes["updated_preference"] = result.get("updated_preference")
    if isinstance(result.get("selection_scope"), dict):
        attributes["selection_scope"] = result.get("selection_scope")
    if result.get("response_data") is not None:
        attributes["response_data"] = result.get("response_data")
    return {key: value for key, value in attributes.items() if value is not None}


class DreameLawnMowerFirmwareUpdateStatusSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose cached firmware update status from batch device data."""

    _attr_name = "Firmware Update Status"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_firmware_update_status"

    @property
    def native_value(self) -> str | None:
        """Return a compact firmware update state."""
        return _batch_ota_status_name(self.coordinator.batch_device_data)

    @property
    def available(self) -> bool:
        """Return whether cached OTA state is available."""
        return (
            self.coordinator.data is not None
            and _batch_ota_section(self.coordinator.batch_device_data) is not None
            and self.native_value is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached OTA attributes."""
        return batch_ota_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerWeatherProtectionStatusSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose cached read-only weather/rain protection state."""

    _attr_name = "Weather Protection Status"
    _attr_icon = "mdi:weather-pouring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_weather_protection_status"

    @property
    def native_value(self) -> str | None:
        """Return a compact weather/rain protection state."""
        result = _weather_section(self.coordinator.weather_protection)
        if result is None:
            return None
        state = _weather_probe_state(result)
        return None if state == "none" else state

    @property
    def available(self) -> bool:
        """Return whether cached weather state is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerRainProtectionDurationSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the configured rain protection duration."""

    _attr_name = "Rain Protection Duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "h"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_protection_duration"

    @property
    def native_value(self) -> int | None:
        """Return the configured rain protection duration in hours."""
        result = _weather_section(self.coordinator.weather_protection)
        if result is None:
            return None
        value = result.get("rain_protection_duration_hours")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        """Return whether a configured rain protection duration exists."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerRainDelayEndTimeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the active rain-delay end time when the mower reports one."""

    _attr_name = "Rain Delay End Time"
    _attr_icon = "mdi:clock-end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_delay_end_time"

    @property
    def native_value(self) -> datetime | None:
        """Return the active rain-delay end time when available."""
        result = _weather_section(self.coordinator.weather_protection)
        if result is None:
            return None
        value = result.get("rain_protect_end_time_iso")
        if not isinstance(value, str) or value == "":
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @property
    def available(self) -> bool:
        """Return whether an active rain-delay end time exists."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerAppMapObjectCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose cached 3D app-map object metadata."""

    _attr_name = "3D Map Object Count"
    _attr_icon = "mdi:cube-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_app_map_object_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of cached 3D map objects."""
        return _app_map_object_count(self.coordinator.app_map_objects)

    @property
    def available(self) -> bool:
        """Return whether cached 3D map object data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached 3D map object attributes."""
        return app_map_object_attributes(self.coordinator.app_map_objects)


class DreameLawnMowerAppMapCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the number of cached app maps."""

    _attr_name = "App Map Count"
    _attr_icon = "mdi:map-marker-multiple"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_app_map_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of cached app maps."""
        return _app_map_count(self.coordinator.app_maps)

    @property
    def available(self) -> bool:
        """Return whether cached app-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached app-map attributes."""
        return app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerAvailableVectorMapCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the number of cached vector maps."""

    _attr_name = "Available Vector Map Count"
    _attr_icon = "mdi:map-marker-multiple-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_available_vector_map_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the number of cached vector maps."""
        return _available_vector_map_count(self.coordinator.vector_map_details)

    @property
    def available(self) -> bool:
        """Return whether cached vector-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached vector-map attributes."""
        return vector_map_attributes(self.coordinator.vector_map_details)


class DreameLawnMowerCurrentAppMapIndexSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current cached app map index."""

    _attr_name = "Current App Map Index"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_app_map_index"

    @property
    def native_value(self) -> int | None:
        """Return the current app map index."""
        return _current_app_map_index(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether cached app-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerSelectedMowingActionSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the currently selected mowing action label."""

    _attr_name = "Selected Mowing Action"
    _attr_icon = "mdi:play-box-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_mowing_action"

    @property
    def native_value(self) -> str | None:
        """Return the selected mowing action label."""
        return _selected_mowing_action_label(self.coordinator)

    @property
    def available(self) -> bool:
        """Return whether selected action metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the selected-run metadata used for this sensor."""
        return _selected_run_scope_attributes(self.coordinator)


class DreameLawnMowerSelectedMapSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the currently selected app map label."""

    _attr_name = "Selected Map"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_map"

    @property
    def native_value(self) -> str | None:
        """Return the selected app map label."""
        return _selected_map_label(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            getattr(self.coordinator, "selected_map_index", None),
        )

    @property
    def available(self) -> bool:
        """Return whether selected map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the selected-run metadata used for this sensor."""
        return _selected_run_scope_attributes(self.coordinator)


class DreameLawnMowerSelectedMapPreferenceModeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current map preference mode."""

    _attr_name = "Selected Map Preference Mode"
    _attr_icon = "mdi:tune-variant"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_mode"
        )

    @property
    def native_value(self) -> str | None:
        """Return the selected/current map preference mode label."""
        value = _selected_map_preference_value(self.coordinator, "mode_name")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        """Return whether selected/current map preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current map preference summary."""
        return _selected_map_preference_summary(self.coordinator)


class DreameLawnMowerSelectedMapPreferenceAreaCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current map preference area count."""

    _attr_name = "Selected Map Preference Area Count"
    _attr_icon = "mdi:map-marker-multiple-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_area_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the selected/current map preference area count."""
        value = _selected_map_preference_value(self.coordinator, "area_count")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current map preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current map preference summary."""
        return _selected_map_preference_summary(self.coordinator)


class DreameLawnMowerSelectedMapPreferenceCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current map decoded preference count."""

    _attr_name = "Selected Map Preference Count"
    _attr_icon = "mdi:format-list-numbered"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the selected/current map decoded preference count."""
        value = _selected_map_preference_value(self.coordinator, "preference_count")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current map preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current map preference summary."""
        return _selected_map_preference_summary(self.coordinator)


class DreameLawnMowerSelectedTargetSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the currently selected scoped mowing target label."""

    _attr_name = "Selected Target"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_target"

    @property
    def native_value(self) -> str | None:
        """Return the selected zone, spot, or edge label."""
        return _selected_target_label(self.coordinator)

    @property
    def available(self) -> bool:
        """Return whether a scoped target is selected."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the selected-run metadata used for this sensor."""
        return _selected_run_scope_attributes(self.coordinator)


class DreameLawnMowerSelectedZoneMowingHeightSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current zone mowing height."""

    _attr_name = "Selected Zone Mowing Height"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_mowing_height"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the selected/current zone mowing height in centimeters."""
        value = _selected_zone_preference_value(self.coordinator, "mowing_height_cm")
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneEfficiencyModeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current zone efficiency mode."""

    _attr_name = "Selected Zone Efficiency Mode"
    _attr_icon = "mdi:run-fast"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_efficiency_mode"
        )

    @property
    def native_value(self) -> str | None:
        """Return the selected/current zone efficiency mode label."""
        value = _selected_zone_preference_value(self.coordinator, "efficient_mode_name")
        return value if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneDirectionModeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the selected/current zone mowing direction mode."""

    _attr_name = "Selected Zone Direction Mode"
    _attr_icon = "mdi:compass-rose"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_direction_mode"
        )

    @property
    def native_value(self) -> str | None:
        """Return the selected/current zone mowing direction mode label."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "mowing_direction_mode_name",
        )
        return value if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleAvoidanceSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose whether obstacle avoidance is enabled for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Avoidance"
    _attr_icon = "mdi:shield-check-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_avoidance"
        )

    @property
    def native_value(self) -> str | None:
        """Return whether obstacle avoidance is enabled for the zone."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_enabled",
        )
        if isinstance(value, bool):
            return "enabled" if value else "disabled"
        return None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleDistanceSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose obstacle avoidance distance for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Distance"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_distance"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return obstacle avoidance distance in centimeters."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_distance_cm",
        )
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleHeightSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose obstacle avoidance height for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Height"
    _attr_icon = "mdi:arrow-expand-vertical"
    _attr_native_unit_of_measurement = "cm"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_height"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return obstacle avoidance height in centimeters."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_height_cm",
        )
        return value if isinstance(value, int | float) else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerSelectedZoneObstacleClassSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the enabled AI obstacle classes for the selected/current zone."""

    _attr_name = "Selected Zone Obstacle Classes"
    _attr_icon = "mdi:shape-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_obstacle_classes"
        )

    @property
    def native_value(self) -> str | None:
        """Return a comma-separated list of enabled AI obstacle classes."""
        value = _selected_zone_preference_value(
            self.coordinator,
            "obstacle_avoidance_ai_classes",
        )
        if not isinstance(value, list):
            return None
        labels = [
            item.replace("_", " ").strip().title()
            for item in value
            if isinstance(item, str) and item.strip()
        ]
        return ", ".join(labels) if labels else None

    @property
    def available(self) -> bool:
        """Return whether selected/current zone preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the compact selected/current zone preference summary."""
        return _selected_zone_preference_summary(self.coordinator)


class DreameLawnMowerCurrentVectorMapNameSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current vector-map name."""

    _attr_name = "Current Vector Map Name"
    _attr_icon = "mdi:map-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_vector_map_name"

    @property
    def native_value(self) -> str | None:
        """Return the current vector-map name."""
        return _current_vector_map_name(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current vector-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentVectorMapIdSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current vector-map id."""

    _attr_name = "Current Vector Map ID"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_vector_map_id"

    @property
    def native_value(self) -> int | None:
        """Return the current vector-map id."""
        return _current_vector_map_id(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current vector-map metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapAreaSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the total area of the current cached app map."""

    _attr_name = "Current App Map Area"
    _attr_icon = "mdi:texture-box"
    _attr_native_unit_of_measurement = "m²"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_current_app_map_area"

    @property
    def native_value(self) -> float | int | None:
        """Return the total area of the current app map."""
        return _current_app_map_total_area(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map area metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapZoneCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the zone count of the current cached app map."""

    _attr_name = "Current App Map Zone Count"
    _attr_icon = "mdi:vector-square"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_zone_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the zone count of the current app map."""
        return _current_app_map_zone_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map zone metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapSpotCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the spot count of the current cached app map."""

    _attr_name = "Current App Map Spot Count"
    _attr_icon = "mdi:map-marker-radius-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_spot_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the spot count of the current app map."""
        return _current_app_map_spot_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map spot metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapEdgeCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the edge contour count of the current vector map."""

    _attr_name = "Current App Map Edge Count"
    _attr_icon = "mdi:vector-polyline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_edge_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the edge contour count of the current vector map."""
        return _current_vector_map_contour_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map vector contour metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live trajectory point count of the current cached app map."""

    _attr_name = "Current App Map Trajectory Point Count"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_trajectory_point_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the trajectory point count of the current app map."""
        return _current_app_map_trajectory_point_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map trajectory metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapTrajectoryLengthSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live trajectory length of the current cached app map."""

    _attr_name = "Current App Map Trajectory Length"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = "m"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_trajectory_length"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the approximate trajectory length in meters."""
        return _current_app_map_trajectory_length_m(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map trajectory metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapCutRelationCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the cut-relation count of the current cached app map."""

    _attr_name = "Current App Map Cut Relation Count"
    _attr_icon = "mdi:vector-polyline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_cut_relation_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the cut-relation count of the current app map."""
        return _current_app_map_cut_relation_count(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map cut-relation metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current-map attributes."""
        return current_app_map_attributes(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapMowPathPointCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live mow-path point count of the current vector map."""

    _attr_name = "Current App Map Mow Path Point Count"
    _attr_icon = "mdi:route"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_mow_path_point_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the live mow-path point count of the current vector map."""
        return _current_vector_map_mow_path_point_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map vector mow-path metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerCurrentAppMapMowPathLengthSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the live mow-path length of the current vector map."""

    _attr_name = "Current App Map Mow Path Length"
    _attr_icon = "mdi:ruler-square"
    _attr_native_unit_of_measurement = "m"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_current_app_map_mow_path_length"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the approximate live mow-path length in meters."""
        return _current_vector_map_mow_path_length_m(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current-map vector mow-path metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current vector-map attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerMowingProgressSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose a calculated mowing progress percentage for the active map."""

    _attr_name = "Mowing Progress"
    _attr_icon = "mdi:progress-check"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_mowing_progress"

    @property
    def native_value(self) -> float | int | None:
        """Return the current mowing progress percentage."""
        snapshot = self.coordinator.data
        cleaned_area = (
            None if snapshot is None else getattr(snapshot, "cleaned_area", None)
        )
        current_map_area = _current_app_map_total_area(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if cleaned_area is None or current_map_area in (None, 0):
            return None
        progress = (float(cleaned_area) / float(current_map_area)) * 100
        return round(max(0.0, min(progress, 100.0)), 1)

    @property
    def available(self) -> bool:
        """Return whether enough live/session metadata is present."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the values used to calculate the percentage."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        attributes: dict[str, Any] = {
            "cleaned_area": getattr(snapshot, "cleaned_area", None),
            "cleaning_time": getattr(snapshot, "cleaning_time", None),
            "current_zone": _current_zone_label(snapshot),
            "active_segment_count": getattr(snapshot, "active_segment_count", None),
        }
        current_map = _current_app_map_summary(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if isinstance(current_map, dict):
            attributes["current_app_map"] = current_map
        runtime_summary = _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )
        if runtime_summary:
            attributes["runtime_status_blob"] = runtime_summary
        return {
            key: value
            for key, value in attributes.items()
            if value not in (None, [], {})
        }


class DreameLawnMowerRuntimeMissionProgressSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose mission progress decoded from the runtime `1.4` payload."""

    _attr_name = "Runtime Mission Progress"
    _attr_icon = "mdi:map-clock-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_mission_progress"

    @property
    def native_value(self) -> float | int | None:
        """Return the active mission progress percentage from runtime telemetry."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_progress_percent(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime mission telemetry is available and relevant."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime progress details used for the percentage."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimeCurrentAreaSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current mission area decoded from runtime `1.4` telemetry."""

    _attr_name = "Runtime Current Area"
    _attr_icon = "mdi:texture-box"
    _attr_native_unit_of_measurement = "m²"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_current_area"

    @property
    def native_value(self) -> float | int | None:
        """Return the current completed mission area in square meters."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_current_area_sqm(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime mission-area telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimeTotalAreaSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the total mission area decoded from runtime `1.4` telemetry."""

    _attr_name = "Runtime Total Area"
    _attr_icon = "mdi:map"
    _attr_native_unit_of_measurement = "m²"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_total_area"

    @property
    def native_value(self) -> float | int | None:
        """Return the total mission area in square meters."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_total_area_sqm(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime total-area telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimePositionXSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime mower X coordinate."""

    _attr_name = "Runtime Position X"
    _attr_icon = "mdi:axis-x-arrow"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_position_x"

    @property
    def native_value(self) -> int | None:
        """Return the current runtime X coordinate in map units."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_pose_x(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime position telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimePositionYSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime mower Y coordinate."""

    _attr_name = "Runtime Position Y"
    _attr_icon = "mdi:axis-y-arrow"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_position_y"

    @property
    def native_value(self) -> int | None:
        """Return the current runtime Y coordinate in map units."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_pose_y(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime position telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimeHeadingSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime mower heading."""

    _attr_name = "Runtime Heading"
    _attr_icon = "mdi:compass-outline"
    _attr_native_unit_of_measurement = "°"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_heading"

    @property
    def native_value(self) -> float | int | None:
        """Return the current runtime heading in degrees."""
        snapshot = self.coordinator.data
        if snapshot is None or not _runtime_progress_available_for_snapshot(snapshot):
            return None
        return _runtime_status_blob_heading_deg(
            getattr(self.coordinator, "runtime_status_blob", None)
        )

    @property
    def available(self) -> bool:
        """Return whether runtime heading telemetry is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the decoded runtime mission details."""
        return _runtime_status_blob_summary(
            getattr(self.coordinator, "runtime_status_blob", None)
        )


class DreameLawnMowerRuntimeTrackPointCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime live-track point count."""

    _attr_name = "Runtime Live Track Point Count"
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_runtime_live_track_point_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the current runtime live-track point count."""
        return _current_vector_map_runtime_track_point_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current runtime live-track metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current runtime-track attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerRuntimeTrackLengthSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime live-track length."""

    _attr_name = "Runtime Live Track Length"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = "m"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_runtime_live_track_length"

    @property
    def native_value(self) -> float | int | None:
        """Return the current runtime live-track length in meters."""
        return _current_vector_map_runtime_track_length_m(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current runtime live-track metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current runtime-track attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerRuntimeTrackSegmentCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the current runtime live-track segment count."""

    _attr_name = "Runtime Live Track Segment Count"
    _attr_icon = "mdi:vector-polyline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_runtime_live_track_segment_count"
        )

    @property
    def native_value(self) -> int | None:
        """Return the current runtime live-track segment count."""
        return _current_vector_map_runtime_track_segment_count(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )

    @property
    def available(self) -> bool:
        """Return whether current runtime live-track metadata is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached current runtime-track attributes."""
        return current_vector_map_attributes(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )


class DreameLawnMowerConfiguredScheduleCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the cached batch schedule count."""

    _attr_name = "Configured Schedule Count"
    _attr_icon = "mdi:calendar-multiple"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_configured_schedule_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of decoded cached schedules."""
        return _batch_schedule_count(self.coordinator.batch_device_data)

    @property
    def available(self) -> bool:
        """Return whether cached schedule data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached schedule attributes."""
        return batch_schedule_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerPreferenceMapCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the cached batch preference map count."""

    _attr_name = "Preference Map Count"
    _attr_icon = "mdi:map-marker-multiple-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_preference_map_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of maps with cached preference data."""
        return _batch_preference_map_count(self.coordinator.batch_device_data)

    @property
    def available(self) -> bool:
        """Return whether cached preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached preference attributes."""
        return batch_preference_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerLastScheduleProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only schedule probe result."""

    _attr_name = "Last Schedule Probe"
    _attr_icon = "mdi:calendar-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_schedule_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last schedule probe."""
        return _schedule_probe_state(self.coordinator.last_schedule_probe_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last schedule probe."""
        return schedule_probe_result_attributes(
            self.coordinator.last_schedule_probe_result
        )


def _schedule_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "error"
    return "available" if result.get("available") else "unavailable"


def schedule_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a schedule probe result."""
    if not result:
        return {}

    errors = result.get("errors")
    schedules = [
        _schedule_probe_entry_summary(schedule)
        for schedule in result.get("schedules", [])
        if isinstance(schedule, dict)
    ]
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "current_task": result.get("current_task"),
        "schedule_selection": result.get("schedule_selection"),
        "schedule_count": len(schedules),
        "schedules": schedules,
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


class DreameLawnMowerLastBatchDeviceDataProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only batch device-data probe result."""

    _attr_name = "Last Batch Device Data Probe"
    _attr_icon = "mdi:database-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_last_batch_device_data_probe"
        )

    @property
    def native_value(self) -> str:
        """Return a compact state for the last batch device-data probe."""
        return _batch_device_data_probe_state(
            self.coordinator.last_batch_device_data_probe_result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last batch device-data probe."""
        return batch_device_data_probe_result_attributes(
            self.coordinator.last_batch_device_data_probe_result
        )


def _batch_device_data_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    sections = [
        result.get("batch_schedule"),
        result.get("batch_mowing_preferences"),
        result.get("batch_ota_info"),
    ]
    if any(
        isinstance(section, dict)
        and isinstance(section.get("errors"), list)
        and section["errors"]
        for section in sections
    ):
        if not any(
            isinstance(section, dict) and section.get("available")
            for section in sections
        ):
            return "error"
    return (
        "available"
        if any(
            isinstance(section, dict) and section.get("available")
            for section in sections
        )
        else "unavailable"
    )


def batch_device_data_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a batch device-data probe."""
    if not result:
        return {}

    schedule = result.get("batch_schedule")
    preferences = result.get("batch_mowing_preferences")
    ota = result.get("batch_ota_info")
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "batch_schedule": _batch_schedule_probe_summary(schedule),
        "batch_mowing_preferences": _batch_preference_probe_summary(preferences),
        "batch_ota_info": _batch_ota_probe_summary(ota),
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def batch_schedule_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached batch schedule attributes."""
    summary = _batch_schedule_probe_summary(_batch_schedule_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "batch_schedule": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def app_map_object_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached 3D app-map object attributes."""
    summary = _app_map_object_summary(_app_map_object_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "app_map_objects": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def app_map_attributes(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact cached app-map attributes."""
    summary = _app_maps_summary(result, batch_device_data)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "app_maps": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def vector_map_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached vector-map attributes."""
    summary = _vector_map_summary(result)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "vector_maps": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def current_app_map_attributes(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact cached current app-map attributes."""
    summary = _current_app_map_summary(result, batch_device_data)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "current_app_map": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def current_vector_map_attributes(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact cached current vector-map attributes."""
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "current_vector_map": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def batch_preference_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached batch preference attributes."""
    summary = _batch_preference_probe_summary(_batch_preference_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "batch_mowing_preferences": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def batch_ota_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached batch OTA attributes."""
    summary = _batch_ota_probe_summary(_batch_ota_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "batch_ota_info": summary,
        "ota_status_name": _batch_ota_status_name(result),
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _batch_schedule_count(result: dict[str, Any] | None) -> int | None:
    summary = _batch_schedule_probe_summary(_batch_schedule_section(result))
    if not isinstance(summary, dict):
        return None
    value = summary.get("schedule_count")
    return value if isinstance(value, int) else None


def _runtime_progress_available_for_snapshot(snapshot: Any) -> bool:
    activity = getattr(snapshot, "activity", None)
    return activity in {"mowing", "paused", "returning"}


def _runtime_status_blob_summary(blob: Any) -> dict[str, Any]:
    if blob is None:
        return {}
    track_segments = getattr(blob, "candidate_runtime_track_segments", ()) or ()
    track_point_count = sum(
        len(segment) for segment in track_segments if isinstance(segment, (list, tuple))
    )
    track_length_m = (
        round(
            sum(
                _coordinate_path_length_m(segment)
                for segment in track_segments
                if isinstance(segment, (list, tuple))
            ),
            2,
        )
        if track_point_count
        else None
    )
    attributes = {
        "source": getattr(blob, "source", None),
        "length": getattr(blob, "length", None),
        "frame_valid": getattr(blob, "frame_valid", None),
        "progress_percent": getattr(blob, "candidate_runtime_progress_percent", None),
        "area_progress_percent": getattr(
            blob,
            "candidate_runtime_area_progress_percent",
            None,
        ),
        "current_area_sqm": getattr(blob, "candidate_runtime_current_area_sqm", None),
        "total_area_sqm": getattr(blob, "candidate_runtime_total_area_sqm", None),
        "region_id": getattr(blob, "candidate_runtime_region_id", None),
        "task_id": getattr(blob, "candidate_runtime_task_id", None),
        "pose_x": getattr(blob, "candidate_runtime_pose_x", None),
        "pose_y": getattr(blob, "candidate_runtime_pose_y", None),
        "heading_deg": getattr(blob, "candidate_runtime_heading_deg", None),
        "track_segment_count": len(track_segments) if track_point_count else None,
        "track_point_count": track_point_count or None,
        "track_length_m": track_length_m,
        "notes": list(getattr(blob, "notes", ()) or ()),
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _runtime_status_blob_progress_percent(blob: Any) -> float | int | None:
    area_progress = getattr(blob, "candidate_runtime_area_progress_percent", None)
    if isinstance(area_progress, int | float):
        return area_progress
    progress = getattr(blob, "candidate_runtime_progress_percent", None)
    return progress if isinstance(progress, int | float) else None


def _runtime_status_blob_current_area_sqm(blob: Any) -> float | int | None:
    value = getattr(blob, "candidate_runtime_current_area_sqm", None)
    return value if isinstance(value, int | float) else None


def _runtime_status_blob_total_area_sqm(blob: Any) -> float | int | None:
    value = getattr(blob, "candidate_runtime_total_area_sqm", None)
    return value if isinstance(value, int | float) else None


def _runtime_status_blob_pose_x(blob: Any) -> int | None:
    value = getattr(blob, "candidate_runtime_pose_x", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _runtime_status_blob_pose_y(blob: Any) -> int | None:
    value = getattr(blob, "candidate_runtime_pose_y", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _runtime_status_blob_heading_deg(blob: Any) -> float | int | None:
    value = getattr(blob, "candidate_runtime_heading_deg", None)
    return value if isinstance(value, int | float) else None


def _coordinate_path_length_m(points: Any) -> float:
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        return 0.0

    total = 0.0
    previous = points[0]
    for current in points[1:]:
        if not (
            isinstance(previous, (list, tuple))
            and len(previous) >= 2
            and isinstance(current, (list, tuple))
            and len(current) >= 2
        ):
            previous = current
            continue
        total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total / 100.0


def _app_map_object_count(result: dict[str, Any] | None) -> int | None:
    summary = _app_map_object_summary(_app_map_object_section(result))
    if not isinstance(summary, dict):
        return None
    value = summary.get("object_count")
    return value if isinstance(value, int) else None


def _app_map_count(result: dict[str, Any] | None) -> int | None:
    summary = _app_maps_summary(result)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_count")
    return value if isinstance(value, int) else None


def _available_vector_map_count(result: dict[str, Any] | None) -> int | None:
    summary = _vector_map_summary(result)
    if not isinstance(summary, dict):
        return None
    value = summary.get("available_map_count")
    return value if isinstance(value, int) else None


def _current_vector_map_name(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> str | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    map_id = summary.get("map_id")
    if isinstance(map_id, int):
        return f"Map {map_id}"
    return None


def _current_app_map_index(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("idx")
    return value if isinstance(value, int) else None


def _selected_mowing_action_label(coordinator: Any) -> str | None:
    action = getattr(coordinator, "selected_mowing_action", None)
    if not isinstance(action, str) or not action.strip():
        return None
    return mowing_action_label(action)


def _selected_map_index(
    app_maps: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None,
    selected_map_index: int | None,
) -> int | None:
    entries = map_entries(app_maps, batch_device_data)
    if not entries:
        return None
    normalized = selected_current_map_index(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    )
    if any(entry["map_index"] == normalized for entry in entries):
        return normalized
    return None


def _selected_map_label(
    app_maps: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None,
    selected_map_index: int | None,
) -> str | None:
    normalized = _selected_map_index(app_maps, batch_device_data, selected_map_index)
    if normalized is None:
        return None
    for entry in map_entries(app_maps, batch_device_data):
        if entry["map_index"] == normalized:
            return str(entry["label"])
    return None


def _selected_contour_id(value: Any) -> tuple[int, int] | None:
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    ):
        return (int(value[0]), int(value[1]))
    return None


def _selected_target_summary(coordinator: Any) -> dict[str, Any]:
    action = getattr(coordinator, "selected_mowing_action", None)
    selected_map_index = _selected_map_index(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    selected_map = _selected_map_label(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    if action == MOWING_ACTION_ZONE:
        entries = current_zone_entries(
            getattr(coordinator, "batch_device_data", None),
            getattr(coordinator, "app_maps", None),
            selected_map_index=selected_map_index,
        )
        selected_zone_id = getattr(coordinator, "selected_zone_id", None)
        if isinstance(selected_zone_id, int):
            for entry in entries:
                if entry["area_id"] == selected_zone_id:
                    return {
                        "target_type": "zone",
                        "target_id": selected_zone_id,
                        "target_label": str(entry["label"]),
                        "available_target_count": len(entries),
                        "selected_map_index": selected_map_index,
                        "selected_map_label": selected_map,
                    }
        if entries:
            fallback = int(entries[0]["area_id"])
            return {
                "target_type": "zone",
                "target_id": fallback,
                "target_label": zone_label(fallback),
                "available_target_count": len(entries),
                "selected_map_index": selected_map_index,
                "selected_map_label": selected_map,
            }
        return {}

    if action == MOWING_ACTION_SPOT:
        entries = current_spot_entries(
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            selected_map_index=selected_map_index,
        )
        selected_spot_id = getattr(coordinator, "selected_spot_id", None)
        if isinstance(selected_spot_id, int):
            for entry in entries:
                if entry["spot_id"] == selected_spot_id:
                    return {
                        "target_type": "spot",
                        "target_id": selected_spot_id,
                        "target_label": str(entry["label"]),
                        "available_target_count": len(entries),
                        "selected_map_index": selected_map_index,
                        "selected_map_label": selected_map,
                    }
        if entries:
            fallback = int(entries[0]["spot_id"])
            return {
                "target_type": "spot",
                "target_id": fallback,
                "target_label": spot_label(fallback),
                "available_target_count": len(entries),
                "selected_map_index": selected_map_index,
                "selected_map_label": selected_map,
            }
        return {}

    if action == MOWING_ACTION_EDGE:
        entries = current_contour_entries(
            getattr(coordinator, "vector_map_details", None),
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            selected_map_index=selected_map_index,
        )
        selected_contour_id = _selected_contour_id(
            getattr(coordinator, "selected_contour_id", None)
        )
        if selected_contour_id is not None:
            for entry in entries:
                if entry["contour_id"] == selected_contour_id:
                    return {
                        "target_type": "edge",
                        "target_id": list(selected_contour_id),
                        "target_label": str(entry["label"]),
                        "available_target_count": len(entries),
                        "selected_map_index": selected_map_index,
                        "selected_map_label": selected_map,
                    }
        if entries:
            entry_id = entries[0]["contour_id"]
            if isinstance(entry_id, tuple):
                return {
                    "target_type": "edge",
                    "target_id": list(entry_id),
                    "target_label": contour_label(entry_id),
                    "available_target_count": len(entries),
                    "selected_map_index": selected_map_index,
                    "selected_map_label": selected_map,
                }
        return {}

    return {
        "selected_map_index": selected_map_index,
        "selected_map_label": selected_map,
    }


def _selected_target_label(coordinator: Any) -> str | None:
    summary = _selected_target_summary(coordinator)
    value = summary.get("target_label")
    return value if isinstance(value, str) and value.strip() else None


def _selected_map_preference_summary(coordinator: Any) -> dict[str, Any]:
    selected_map_index = _selected_map_index(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    if selected_map_index is None:
        return {}

    preference_maps = (
        getattr(coordinator, "batch_device_data", {}).get("batch_mowing_preferences")
        if isinstance(getattr(coordinator, "batch_device_data", None), dict)
        else None
    )
    maps = preference_maps.get("maps") if isinstance(preference_maps, dict) else None
    if not isinstance(maps, list):
        return {}

    selected_map_label = _selected_map_label(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )

    for map_entry in maps:
        if (
            not isinstance(map_entry, dict)
            or map_entry.get("idx") != selected_map_index
        ):
            continue
        preferences = map_entry.get("preferences")
        summary = {
            "selected_map_index": selected_map_index,
            "selected_map_label": selected_map_label,
            "mode": map_entry.get("mode"),
            "mode_name": map_entry.get("mode_name"),
            "area_count": map_entry.get("area_count"),
            "preference_count": len(preferences)
            if isinstance(preferences, list)
            else None,
        }
        return {
            key: value for key, value in summary.items() if value not in (None, [], {})
        }
    return {}


def _selected_map_preference_value(coordinator: Any, key: str) -> Any:
    return _selected_map_preference_summary(coordinator).get(key)


def _selected_zone_preference_summary(coordinator: Any) -> dict[str, Any]:
    selected_map_index = _selected_map_index(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    if selected_map_index is None:
        return {}

    zone_entries = current_zone_entries(
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "app_maps", None),
        selected_map_index=getattr(coordinator, "selected_map_index", None),
    )
    selected_zone_id = getattr(coordinator, "selected_zone_id", None)
    if not isinstance(selected_zone_id, int) or not any(
        entry["area_id"] == selected_zone_id for entry in zone_entries
    ):
        selected_zone_id = int(zone_entries[0]["area_id"]) if zone_entries else None
    if selected_zone_id is None:
        return {}

    preference_maps = (
        getattr(coordinator, "batch_device_data", {}).get("batch_mowing_preferences")
        if isinstance(getattr(coordinator, "batch_device_data", None), dict)
        else None
    )
    maps = preference_maps.get("maps") if isinstance(preference_maps, dict) else None
    if not isinstance(maps, list):
        return {}

    selected_map_label = _selected_map_label(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    zone_entry = next(
        (entry for entry in zone_entries if entry["area_id"] == selected_zone_id),
        None,
    )
    zone_label_value = (
        str(zone_entry["label"])
        if isinstance(zone_entry, dict)
        else zone_label(selected_zone_id)
    )

    for map_entry in maps:
        if (
            not isinstance(map_entry, dict)
            or map_entry.get("idx") != selected_map_index
        ):
            continue
        preferences = map_entry.get("preferences")
        if not isinstance(preferences, list):
            return {}
        for preference in preferences:
            if (
                not isinstance(preference, dict)
                or preference.get("area_id") != selected_zone_id
            ):
                continue
            summary = {
                "selected_map_index": selected_map_index,
                "selected_map_label": selected_map_label,
                "selected_zone_id": selected_zone_id,
                "selected_zone_label": zone_label_value,
                "mode": map_entry.get("mode"),
                "mode_name": map_entry.get("mode_name"),
                "reported_version": preference.get("reported_version"),
                "mowing_height_cm": preference.get("mowing_height_cm"),
                "efficient_mode_name": preference.get("efficient_mode_name"),
                "mowing_direction_mode_name": preference.get(
                    "mowing_direction_mode_name"
                ),
                "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
                "edge_mowing_auto": preference.get("edge_mowing_auto"),
                "edge_mowing_walk_mode_name": preference.get(
                    "edge_mowing_walk_mode_name"
                ),
                "edge_mowing_obstacle_avoidance": preference.get(
                    "edge_mowing_obstacle_avoidance"
                ),
                "cutter_position_name": preference.get("cutter_position_name"),
                "edge_mowing_num": preference.get("edge_mowing_num"),
                "obstacle_avoidance_enabled": preference.get(
                    "obstacle_avoidance_enabled"
                ),
                "obstacle_avoidance_height_cm": preference.get(
                    "obstacle_avoidance_height_cm"
                ),
                "obstacle_avoidance_distance_cm": preference.get(
                    "obstacle_avoidance_distance_cm"
                ),
                "obstacle_avoidance_ai_classes": preference.get(
                    "obstacle_avoidance_ai_classes"
                ),
                "edge_mowing_safe": preference.get("edge_mowing_safe"),
            }
            return {
                key: value
                for key, value in summary.items()
                if value not in (None, [], {})
            }
        return {}
    return {}


def _selected_zone_preference_value(coordinator: Any, key: str) -> Any:
    return _selected_zone_preference_summary(coordinator).get(key)


def _selected_run_scope_attributes(coordinator: Any) -> dict[str, Any]:
    action = getattr(coordinator, "selected_mowing_action", None)
    attributes = {
        "selected_mowing_action": action,
        "selected_mowing_action_label": _selected_mowing_action_label(coordinator),
        "selected_map_index": _selected_map_index(
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            getattr(coordinator, "selected_map_index", None),
        ),
        "selected_map_label": _selected_map_label(
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            getattr(coordinator, "selected_map_index", None),
        ),
    }
    attributes.update(_selected_target_summary(coordinator))
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _current_app_map_total_area(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("total_area")
    return value if isinstance(value, int | float) else None


def _current_app_map_zone_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_area_count")
    return value if isinstance(value, int) else None


def _current_app_map_spot_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("spot_count")
    return value if isinstance(value, int) else None


def _current_app_map_trajectory_point_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("trajectory_point_count")
    return value if isinstance(value, int) else None


def _current_app_map_trajectory_length_m(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("trajectory_length_m")
    return value if isinstance(value, int | float) else None


def _current_app_map_cut_relation_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("cut_relation_count")
    return value if isinstance(value, int) else None


def _current_vector_map_contour_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("contour_count")
    return value if isinstance(value, int) else None


def _current_vector_map_id(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_id")
    return value if isinstance(value, int) else None


def _current_vector_map_mow_path_point_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("mow_path_point_count")
    return value if isinstance(value, int) else None


def _current_vector_map_mow_path_length_m(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("mow_path_length_m")
    return value if isinstance(value, int | float) else None


def _current_vector_map_runtime_track_segment_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("runtime_track_segment_count")
    return value if isinstance(value, int) else None


def _current_vector_map_runtime_track_point_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("runtime_track_point_count")
    return value if isinstance(value, int) else None


def _current_vector_map_runtime_track_length_m(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("runtime_track_length_m")
    return value if isinstance(value, int | float) else None


def _batch_preference_map_count(result: dict[str, Any] | None) -> int | None:
    summary = _batch_preference_probe_summary(_batch_preference_section(result))
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_count")
    return value if isinstance(value, int) else None


def _batch_schedule_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("batch_schedule") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _app_map_object_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("app_map_objects") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _batch_preference_section(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    value = result.get("batch_mowing_preferences") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _batch_ota_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("batch_ota_info") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _batch_ota_status_name(result: dict[str, Any] | None) -> str | None:
    ota = _batch_ota_section(result)
    if ota is None:
        return None
    status = ota.get("ota_status")
    if isinstance(status, str) and status:
        return status
    if isinstance(status, int):
        if ota.get("update_available") and status == 0:
            return "update_available"
        return f"status_{status}"
    if ota.get("update_available") is True:
        return "update_available"
    if ota.get("available"):
        return "no_update"
    return None


def _app_map_object_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    objects = [
        {
            key: item.get(key)
            for key in ("name", "extension", "url_present", "error")
            if item.get(key) is not None
        }
        for item in value.get("objects", [])
        if isinstance(item, dict)
    ]
    extension_counts: dict[str, int] = {}
    for item in objects:
        extension = item.get("extension")
        if isinstance(extension, str) and extension:
            extension_counts[extension] = extension_counts.get(extension, 0) + 1
    summary = {
        "source": value.get("source"),
        "object_count": value.get("object_count", len(objects)),
        "urls_included": value.get("urls_included"),
        "extension_counts": extension_counts,
        "objects": objects,
    }
    raw = value.get("raw")
    if isinstance(raw, dict):
        summary["raw_keys"] = sorted(raw.keys())
    error = value.get("error")
    if error is not None:
        summary["error"] = error
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _app_maps_summary(
    value: Any,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    maps = [
        _app_map_entry_summary(entry)
        for entry in value.get("maps", [])
        if isinstance(entry, dict)
    ]
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "map_count": len(maps),
        "current_map_index": selected_current_map_index(value, batch_device_data),
        "maps": maps,
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _vector_map_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    maps = [
        _vector_map_entry_summary(entry)
        for entry in value.get("maps", [])
        if isinstance(entry, dict)
    ]
    summary = {
        "available": value.get("available"),
        "map_id": value.get("map_id"),
        "map_index": value.get("map_index"),
        "current_map_id": value.get("current_map_id"),
        "available_map_count": value.get("available_map_count", len(maps)),
        "available_maps": value.get("available_maps"),
        "map_names": [
            item.get("map_name")
            for item in maps
            if isinstance(item.get("map_name"), str) and item.get("map_name")
        ],
        "maps": maps,
    }
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _current_app_map_summary(
    value: Any,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    current_idx = selected_current_map_index(value, batch_device_data)
    maps = value.get("maps")
    if not isinstance(maps, list):
        return None

    for entry in maps:
        if isinstance(entry, dict) and entry.get("idx") == current_idx:
            return _app_map_entry_summary(entry)

    for entry in maps:
        if isinstance(entry, dict) and entry.get("current"):
            return _app_map_entry_summary(entry)
    return None


def _current_vector_map_summary(
    value: Any,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    maps = value.get("maps")
    current_idx = selected_current_map_index(app_maps, batch_device_data)
    if isinstance(maps, list):
        for entry in maps:
            if isinstance(entry, dict) and entry.get("map_index") == current_idx:
                return _vector_map_entry_summary(entry)

    top_level = _vector_map_entry_summary(value)
    if current_idx is None and top_level:
        return top_level

    if isinstance(top_level, dict) and top_level.get("map_index") == current_idx:
        return top_level
    return None


def _app_map_entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
    result = {
        "idx": entry.get("idx"),
        "current": entry.get("current"),
        "available": entry.get("available"),
        "created": entry.get("created"),
        "hash_match": entry.get("hash_match"),
        "force_load": entry.get("force_load"),
        "chunk_count": entry.get("chunk_count"),
        "total_area": summary.get("total_area"),
        "map_area_total": summary.get("map_area_total"),
        "map_area_count": summary.get("map_area_count"),
        "spot_count": summary.get("spot_count"),
        "trajectory_count": summary.get("trajectory_count"),
        "trajectory_point_count": summary.get("trajectory_point_count"),
        "trajectory_length_m": summary.get("trajectory_length_m"),
        "cut_relation_count": summary.get("cut_relation_count"),
        "has_live_path": bool(summary.get("trajectory_point_count")),
        "error": entry.get("error"),
    }
    return {key: item for key, item in result.items() if item not in (None, [], {})}


def _vector_map_entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    result = {
        "map_id": entry.get("map_id"),
        "map_index": entry.get("map_index"),
        "map_name": entry.get("map_name"),
        "total_area": entry.get("total_area"),
        "zone_ids": entry.get("zone_ids"),
        "zone_names": entry.get("zone_names"),
        "spot_ids": entry.get("spot_ids"),
        "contour_ids": entry.get("contour_ids"),
        "contour_count": entry.get("contour_count"),
        "clean_point_count": entry.get("clean_point_count"),
        "cruise_point_count": entry.get("cruise_point_count"),
        "mow_path_count": entry.get("mow_path_count"),
        "mow_path_segment_count": entry.get("mow_path_segment_count"),
        "mow_path_point_count": entry.get("mow_path_point_count"),
        "mow_path_length_m": entry.get("mow_path_length_m"),
        "runtime_track_segment_count": entry.get("runtime_track_segment_count"),
        "runtime_track_point_count": entry.get("runtime_track_point_count"),
        "runtime_track_length_m": entry.get("runtime_track_length_m"),
        "runtime_pose_x": entry.get("runtime_pose_x"),
        "runtime_pose_y": entry.get("runtime_pose_y"),
        "runtime_heading_deg": entry.get("runtime_heading_deg"),
        "has_live_path": entry.get("has_live_path"),
    }
    return {key: item for key, item in result.items() if item not in (None, [], {})}


def _schedule_probe_entry_summary(schedule: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "idx": schedule.get("idx"),
        "label": schedule.get("label"),
        "available": schedule.get("available"),
        "version": schedule.get("version"),
        "plan_count": schedule.get("plan_count"),
        "enabled_plan_count": schedule.get("enabled_plan_count"),
        "error": schedule.get("error"),
    }
    return {key: value for key, value in summary.items() if value is not None}


class DreameLawnMowerLastPreferenceProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only mowing preference probe result."""

    _attr_name = "Last Preference Probe"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_preference_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last preference probe."""
        return _preference_probe_state(
            self.coordinator.last_preference_probe_result,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last preference probe."""
        return preference_probe_result_attributes(
            self.coordinator.last_preference_probe_result,
        )


def _preference_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "error"
    return "available" if result.get("available") else "unavailable"


def preference_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a preference probe result."""
    if not result:
        return {}

    errors = result.get("errors")
    maps = [
        _preference_probe_map_summary(map_entry)
        for map_entry in result.get("maps", [])
        if isinstance(map_entry, dict)
    ]
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "property_hint": result.get("property_hint"),
        "map_count": len(maps),
        "maps": maps,
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


class DreameLawnMowerLastTaskStatusProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only app task/status probe result."""

    _attr_name = "Last Task Status Probe"
    _attr_icon = "mdi:clipboard-pulse-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_task_status_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last task/status probe."""
        return task_status_probe_state(
            self.coordinator.last_task_status_probe_result,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last task/status probe."""
        return task_status_probe_result_attributes(
            self.coordinator.last_task_status_probe_result,
        )


class DreameLawnMowerLastWeatherProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only weather/rain protection probe result."""

    _attr_name = "Last Weather Probe"
    _attr_icon = "mdi:weather-pouring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_weather_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last weather probe."""
        return _weather_probe_state(
            self.coordinator.last_weather_probe_result,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last weather probe."""
        return weather_probe_result_attributes(
            self.coordinator.last_weather_probe_result,
        )


def _weather_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors and not result.get("available"):
        return "error"
    if result.get("rain_protection_active"):
        return "rain_delay_active"
    if result.get("rain_protection_enabled"):
        return "rain_protection_enabled"
    return "available" if result.get("available") else "unavailable"


def _weather_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return result


def weather_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a weather probe result."""
    if not result:
        return {}

    errors = result.get("errors")
    warnings = result.get("warnings")
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "fault_hint": result.get("fault_hint"),
        "present_config_keys": result.get("present_config_keys"),
        "weather_switch_enabled": result.get("weather_switch_enabled"),
        "rain_protection_enabled": result.get("rain_protection_enabled"),
        "rain_protection_duration_hours": result.get(
            "rain_protection_duration_hours",
        ),
        "rain_sensor_sensitivity": result.get("rain_sensor_sensitivity"),
        "rain_protect_end_time": result.get("rain_protect_end_time"),
        "rain_protect_end_time_iso": result.get("rain_protect_end_time_iso"),
        "rain_protect_end_time_present": result.get(
            "rain_protect_end_time_present",
        ),
        "rain_protection_active": result.get("rain_protection_active"),
        "rain_protection_raw": result.get("rain_protection_raw"),
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    if isinstance(warnings, list):
        attributes["warning_count"] = len(warnings)
        attributes["warnings"] = warnings
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _preference_probe_map_summary(map_entry: dict[str, Any]) -> dict[str, Any]:
    preferences = [
        _preference_probe_entry_summary(preference)
        for preference in map_entry.get("preferences", [])
        if isinstance(preference, dict)
    ]
    summary = {
        "idx": map_entry.get("idx"),
        "label": map_entry.get("label"),
        "available": map_entry.get("available"),
        "mode": map_entry.get("mode"),
        "mode_name": map_entry.get("mode_name"),
        "area_count": map_entry.get("area_count"),
        "preference_count": len(preferences),
        "preferences": preferences,
        "error": map_entry.get("error"),
    }
    return {key: value for key, value in summary.items() if value not in (None, [], {})}


def _preference_probe_entry_summary(preference: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "area_id": preference.get("area_id"),
        "reported_version": preference.get("reported_version"),
        "version": preference.get("version"),
        "efficient_mode_name": preference.get("efficient_mode_name"),
        "mowing_height_cm": preference.get("mowing_height_cm"),
        "mowing_direction_mode_name": preference.get("mowing_direction_mode_name"),
        "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
        "edge_mowing_auto": preference.get("edge_mowing_auto"),
        "edge_mowing_safe": preference.get("edge_mowing_safe"),
        "edge_mowing_walk_mode_name": preference.get("edge_mowing_walk_mode_name"),
        "cutter_position_name": preference.get("cutter_position_name"),
        "edge_mowing_num": preference.get("edge_mowing_num"),
        "edge_mowing_obstacle_avoidance": preference.get(
            "edge_mowing_obstacle_avoidance",
        ),
        "obstacle_avoidance_enabled": preference.get("obstacle_avoidance_enabled"),
        "obstacle_avoidance_height_cm": preference.get(
            "obstacle_avoidance_height_cm",
        ),
        "obstacle_avoidance_distance_cm": preference.get(
            "obstacle_avoidance_distance_cm",
        ),
        "obstacle_avoidance_ai_classes": preference.get(
            "obstacle_avoidance_ai_classes",
        ),
    }
    return {key: value for key, value in summary.items() if value not in (None, [], {})}


def _batch_schedule_probe_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    schedules = [
        _schedule_probe_entry_summary(schedule)
        for schedule in value.get("schedules", [])
        if isinstance(schedule, dict)
    ]
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "current_task": value.get("current_task"),
        "schedule_count": len(schedules),
        "schedules": schedules,
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _batch_preference_probe_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    maps = [
        _preference_probe_map_summary(map_entry)
        for map_entry in value.get("maps", [])
        if isinstance(map_entry, dict)
    ]
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "property_hint": value.get("property_hint"),
        "map_count": len(maps),
        "maps": maps,
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _batch_ota_probe_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "update_available": value.get("update_available"),
        "auto_upgrade_enabled": value.get("auto_upgrade_enabled"),
        "ota_info": value.get("ota_info"),
        "ota_status": value.get("ota_status"),
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}
